import os
import sys
import time
from datetime import datetime
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Proposicao_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'
BASE_ARQUIVOS = 'https://dadosabertos.camara.leg.br/arquivos'

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))
TAMANHO_LOTE = 500

# ---------------------------------------------------------
# 1. FUNÇÕES DE PRÉ-SINCRONIZAÇÃO (REFERÊNCIAS)
# ---------------------------------------------------------
def sincronizar_tipos_proposicao(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/siglaTipo"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_tipos = {}

    if resp.status_code == 200:
        tipos = resp.json().get('dados', [])
        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Camara')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('nome') or sigla
            if not sigla: continue

            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Camara'", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]

        conexao.commit()
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/codTema"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_temas = {}

    if resp.status_code == 200:
        temas = resp.json().get('dados', [])
        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao) VALUES (%s, 'Camara', %s)
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = int(t.get('cod'))
            nome = t.get('nome')

            cursor.execute(sql, (cod_externo, nome))
            cursor.execute("SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Camara'", (cod_externo,))
            mapa_temas[cod_externo] = cursor.fetchone()[0]

        conexao.commit()
    cursor.close()
    return mapa_temas

# ---------------------------------------------------------
# 2. DUMPS ANUAIS (dadosabertos.camara.leg.br/arquivos)
# ---------------------------------------------------------
def baixar_dump_anual(recurso, ano, ano_atual):
    """Baixa o dump anual completo da Câmara. Um único arquivo cobre TODAS as
    proposições do ano (inclusive de ex-parlamentares, comissões e Executivo),
    no lugar de milhares de chamadas por deputado.

    Devolve a lista de registros, [] quando o arquivo do ano corrente ainda não
    foi publicado (início de janeiro), ou None em falha real."""
    url = f"{BASE_ARQUIVOS}/{recurso}/json/{recurso}-{ano}.json"
    logger.info(f"Baixando dump anual: {url}")
    resp = http_client.get_safe(url, timeout=600)

    if resp.status_code == 404 and ano == ano_atual:
        logger.warning(f"Dump {recurso}-{ano} ainda não publicado; seguindo sem ele.")
        return []
    if resp.status_code != 200:
        logger.error(f"Falha ao baixar {url} (HTTP {resp.status_code})")
        return None

    dados = resp.json().get('dados', [])
    logger.info(f"   └─ {len(dados)} registros em {recurso}-{ano}.")
    return dados

def extrair_id_da_uri(uri):
    try:
        return str(int(str(uri).rstrip('/').split('/')[-1]))
    except (ValueError, AttributeError):
        return None

def executar_em_lotes(conexao, cursor, sql, linhas):
    total = 0
    for i in range(0, len(linhas), TAMANHO_LOTE):
        lote = linhas[i:i + TAMANHO_LOTE]
        cursor.executemany(sql, lote)
        conexao.commit()
        total += len(lote)
    return total

# ---------------------------------------------------------
# 3. CARGA
# ---------------------------------------------------------
def processar_proposicoes_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_camara_v4"
    execucao = ExecucaoEtl(conexao, nome_script)
    fila_erros = EtlErro(conexao, nome_script)

    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)

    cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado(a)'")
    map_deputados = {str(r[0]): r[1] for r in cursor.fetchall()}

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year

    # O cursor guarda o último ano concluído. Na primeira carga vale
    # ano_inicio-1 (carga completa); ao concluir, é reposicionado em
    # ano_atual-1, então execuções seguintes fazem apenas o refresh do ano
    # corrente. Para recarga total, apague o checkpoint (ver README).
    try:
        ultimo_ano = int(chk_manager.obter(nome_script, str(ano_inicio - 1)))
    except ValueError:
        ultimo_ano = ano_inicio - 1

    sql_proposicao = """
        INSERT INTO proposicao (idApi, casa, idTipoProposicao, numero, ano, ementa, statusAtual, dataApresentacao)
        VALUES (%s, 'Camara', %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        idTipoProposicao=VALUES(idTipoProposicao), numero=VALUES(numero),
        ano=VALUES(ano), ementa=VALUES(ementa), statusAtual=VALUES(statusAtual),
        dataApresentacao=VALUES(dataApresentacao)
    """
    sql_autoria = "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)"
    sql_vincular_tema = "INSERT IGNORE INTO temaProposicao (idProposicao, idTema) VALUES (%s, %s)"

    sucesso_total = True
    interrompido = False
    start_time = time.time()

    for ano in range(max(ano_inicio, ultimo_ano + 1), ano_atual + 1):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            logger.warning(f"Tempo limite atingido; parando antes do ano {ano}.")
            interrompido = True
            break

        logger.info(f"=== Processando proposições da Câmara — ano {ano} ===")
        try:
            # 1. Proposições
            proposicoes = baixar_dump_anual("proposicoes", ano, ano_atual)
            if proposicoes is None:
                sucesso_total = False
                break
            if is_test_mode:
                proposicoes = proposicoes[:300]

            garantir_conexao(conexao)
            linhas = []
            for p in proposicoes:
                id_prop_api = str(p.get('id') or '')
                if not id_prop_api:
                    continue
                status_atual = (p.get('ultimoStatus') or {}).get('descricaoSituacao')
                data_raw = p.get('dataApresentacao')
                data_apresentacao = data_raw.replace('T', ' ')[:19] if data_raw else None
                linhas.append((
                    id_prop_api, map_tipos.get(p.get('siglaTipo')), p.get('numero'),
                    p.get('ano'), p.get('ementa'), status_atual, data_apresentacao,
                ))
            gravadas = executar_em_lotes(conexao, cursor, sql_proposicao, linhas)
            logger.info(f"   └─ {gravadas} proposições gravadas/atualizadas.")

            # Mapa idApi -> idProposicao, necessário para autores e temas
            cursor.execute("SELECT idApi, idProposicao FROM proposicao WHERE casa = 'Camara'")
            map_proposicoes = {str(r[0]): r[1] for r in cursor.fetchall()}

            # 2. Autores — vincula os deputados presentes na base
            autores = baixar_dump_anual("proposicoesAutores", ano, ano_atual)
            if autores is None:
                sucesso_total = False
                break
            if is_test_mode:
                autores = autores[:2000]

            garantir_conexao(conexao)
            linhas_autoria = set()
            for a in autores:
                uri_autor = a.get('uriAutor') or ''
                if '/deputados/' not in uri_autor:
                    continue
                id_parl = map_deputados.get(extrair_id_da_uri(uri_autor))
                id_prop = map_proposicoes.get(str(a.get('idProposicao') or ''))
                if id_parl and id_prop:
                    linhas_autoria.add((id_parl, id_prop))
            vinculos_autoria = executar_em_lotes(conexao, cursor, sql_autoria, list(linhas_autoria))
            logger.info(f"   └─ {vinculos_autoria} vínculos de autoria processados.")

            # 3. Temas
            temas = baixar_dump_anual("proposicoesTemas", ano, ano_atual)
            if temas is None:
                sucesso_total = False
                break
            if is_test_mode:
                temas = temas[:2000]

            garantir_conexao(conexao)
            linhas_temas = set()
            for t in temas:
                id_prop = map_proposicoes.get(extrair_id_da_uri(t.get('uriProposicao')) or '')
                id_tema = map_temas.get(t.get('codTema'))
                if id_prop and id_tema:
                    linhas_temas.add((id_prop, id_tema))
            vinculos_tema = executar_em_lotes(conexao, cursor, sql_vincular_tema, list(linhas_temas))
            logger.info(f"   └─ {vinculos_tema} vínculos de tema processados.")

            execucao.incrementar(processados=len(proposicoes),
                                 registros=gravadas + vinculos_autoria + vinculos_tema)
            chk_manager.salvar(nome_script, str(ano))

        except Exception as e:
            if conexao.in_transaction:
                conexao.rollback()
            logger.error(f"Erro ao processar o ano {ano}: {e}")
            fila_erros.registrar(f"ano_{ano}", e)
            execucao.incrementar(erros=1)
            sucesso_total = False
            break

    if sucesso_total and not interrompido:
        # Reposiciona o cursor para que a próxima execução faça apenas o
        # refresh do ano corrente (upserts idempotentes).
        chk_manager.salvar(nome_script, str(ano_atual - 1))
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Proposições da Câmara sincronizadas com SUCESSO.")
    elif interrompido:
        execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
        logger.warning("Execução interrompida por tempo limite; checkpoint preservado.")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total or interrompido

if __name__ == "__main__":
    if not processar_proposicoes_camara():
        sys.exit(1)
