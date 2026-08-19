"""Importa TODAS as proposições/processos do Senado por ano (universo completo,
incluindo autoria de ex-senadores, comissões e Executivo), em vez de buscar
apenas por senador atual como autor.

Fase 1 (barata): GET /processo?ano=X devolve todos os processos do ano em uma
chamada — a lista já traz identificação (sigla/número/ano), ementa, datas e
situação, suficiente para a tabela proposicao.

Fase 2 (detalhes): GET /processo/{id} apenas para processos com autoria de
senador (para vincular autoriaProposicao e assuntos/temas).
"""

import os
import re
import sys
import time
from datetime import datetime
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Proposicao_Senado")

BASE_URL_SENADO = 'https://legis.senado.leg.br/dadosabertos'

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))
TAMANHO_LOTE = 500

RE_IDENTIFICACAO = re.compile(r'^([A-Z]+)\s+(\d+)/(\d{4})$')

# ---------------------------------------------------------
# 1. FUNÇÕES DE PRÉ-SINCRONIZAÇÃO (REFERÊNCIAS)
# ---------------------------------------------------------
def sincronizar_tipos_proposicao(conexao):
    cursor = conexao.cursor(buffered=True)
    url = f"{BASE_URL_SENADO}/processo/siglas"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_tipos = {}

    if resp.status_code == 200:
        dados = resp.json()
        tipos = dados if isinstance(dados, list) else dados.get('Siglas', [])

        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Senado')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('descricao') or sigla
            if not sigla: continue

            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Senado' LIMIT 1", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]

        conexao.commit()
        logger.info(f"{len(mapa_tipos)} Tipos de Proposição do Senado sincronizados.")
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor(buffered=True)
    url = f"{BASE_URL_SENADO}/processo/assuntos"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_temas = {}

    if resp.status_code == 200:
        dados = resp.json()
        temas = dados if isinstance(dados, list) else dados.get('Assuntos', [])


        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao, nivel) VALUES (%s, 'Senado', %s, 'ESPECIFICO')
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = t.get('id')
            nome = t.get('assuntoEspecifico') or t.get('assuntoGeral')
            if not cod_externo or not nome: continue

            cursor.execute(sql, (int(cod_externo), nome))
            cursor.execute(
                "SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Senado' AND nivel = 'ESPECIFICO' LIMIT 1",
                (int(cod_externo),),
            )
            mapa_temas[int(cod_externo)] = cursor.fetchone()[0]

        conexao.commit()
        logger.info(f"{len(mapa_temas)} Temas (Assuntos) do Senado sincronizados.")
    cursor.close()
    return mapa_temas

def garantir_tipo(conexao, cursor, map_tipos, sigla):
    """Sigla presente nos processos mas ausente do catálogo /processo/siglas."""
    if not sigla:
        return None
    if sigla in map_tipos:
        return map_tipos[sigla]
    cursor.execute(
        "INSERT IGNORE INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Senado')",
        (sigla, sigla),
    )
    conexao.commit()
    cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Senado' LIMIT 1", (sigla,))
    res = cursor.fetchone()
    if res:
        map_tipos[sigla] = res[0]
        return res[0]
    return None

# ---------------------------------------------------------
# 2. EXTRAÇÃO
# ---------------------------------------------------------
def buscar_processos_do_ano(ano):
    """Lista anual completa (uma chamada). Devolve None em falha real."""
    url = f"{BASE_URL_SENADO}/processo?ano={ano}&v=1"
    logger.info(f"Baixando lista anual de processos do Senado: {url}")
    resp = http_client.get_safe(url, headers={'accept': 'application/json'}, timeout=300)
    if resp.status_code != 200:
        logger.error(f"Falha ao listar processos de {ano} (HTTP {resp.status_code})")
        return None
    dados = resp.json()
    lista = dados if isinstance(dados, list) else dados.get('Processos', [])
    logger.info(f"   └─ {len(lista)} processos em {ano}.")
    return lista

def buscar_detalhes_processo_senado(id_processo_api):
    url = f"{BASE_URL_SENADO}/processo/{id_processo_api}?v=1"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    return resp.json() if resp.status_code == 200 else None

def extrair_autores_senado(detalhes):
    codigos = []
    for chave in ("autoriaIniciativa", "autoria"):
        for autor in detalhes.get(chave, []) or []:
            codigo = autor.get("codigoParlamentar")
            if codigo:
                codigos.append(str(codigo))

    for autor in (detalhes.get("documento") or {}).get("autoria", []) or []:
        codigo = autor.get("codigoParlamentar")
        if codigo:
            codigos.append(str(codigo))

    return codigos

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
def processar_proposicoes_senado():
    conexao, cursor = get_connection(buffered=True)
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_senado_v3"
    execucao = ExecucaoEtl(conexao, nome_script)
    fila_erros = EtlErro(conexao, nome_script)

    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)

    cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Senador(a)'")
    mapa_senadores = {str(r[0]): r[1] for r in cursor.fetchall()}

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year

    # Cursor = último ano concluído; ao concluir, é reposicionado em ano_atual-1
    # para que execuções seguintes façam apenas o refresh do ano corrente.
    try:
        ultimo_ano = int(chk_manager.obter(nome_script, str(ano_inicio - 1)))
    except ValueError:
        ultimo_ano = ano_inicio - 1

    sql_proposicao = """
        INSERT INTO proposicao (idApi, casa, idTipoProposicao, numero, ano, ementa, statusAtual, dataApresentacao)
        VALUES (%s, 'Senado', %s, %s, %s, %s, %s, %s)
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

        logger.info(f"=== Processando processos do Senado — ano {ano} ===")
        try:
            processos = buscar_processos_do_ano(ano)
            if processos is None:
                sucesso_total = False
                break
            if is_test_mode:
                processos = processos[:200]

            garantir_conexao(conexao)

            # ── Fase 1: upsert de todas as proposições a partir da lista ──
            linhas = []
            for pr in processos:
                codigo_materia = str(pr.get('codigoMateria') or pr.get('id') or '')
                if not codigo_materia:
                    continue

                sigla, numero, ano_ident = None, None, None
                m = RE_IDENTIFICACAO.match((pr.get('identificacao') or '').strip())
                if m:
                    sigla, numero, ano_ident = m.group(1), m.group(2), int(m.group(3))

                data_raw = pr.get('dataApresentacao')
                data_apresentacao = f"{data_raw} 00:00:00" if data_raw and len(str(data_raw)) == 10 else data_raw

                linhas.append((
                    codigo_materia,
                    garantir_tipo(conexao, cursor, map_tipos, sigla),
                    numero, ano_ident or ano,
                    pr.get('ementa'), pr.get('situacaoAtual'), data_apresentacao,
                ))

            gravadas = executar_em_lotes(conexao, cursor, sql_proposicao, linhas)
            logger.info(f"   └─ {gravadas} proposições gravadas/atualizadas.")

            cursor.execute("SELECT idApi, idProposicao FROM proposicao WHERE casa = 'Senado'")
            map_proposicoes = {str(r[0]): r[1] for r in cursor.fetchall()}

            # ── Fase 2: detalhes apenas para autoria de senador ──
            com_senador = [pr for pr in processos if 'Senador' in (pr.get('autoria') or '')]
            logger.info(f"   └─ Buscando detalhes de {len(com_senador)} processos com autoria de senador...")

            for j, pr in enumerate(com_senador, 1):
                id_processo = str(pr.get('id') or '')
                codigo_materia = str(pr.get('codigoMateria') or id_processo)
                id_proposicao_interno = map_proposicoes.get(codigo_materia)
                if not id_processo or not id_proposicao_interno:
                    continue

                detalhes = buscar_detalhes_processo_senado(id_processo)
                if not detalhes:
                    fila_erros.registrar(f"processo_{id_processo}", "detalhe indisponível")
                    execucao.incrementar(erros=1)
                    continue

                try:
                    garantir_conexao(conexao)
                    for codigo_autor in extrair_autores_senado(detalhes):
                        id_autor = mapa_senadores.get(codigo_autor)
                        if id_autor:
                            cursor.execute(sql_autoria, (id_autor, id_proposicao_interno))

                    for a in detalhes.get('assuntos', []) or []:
                        cod_tema = a.get('id') or a.get('codigo')
                        id_tema = map_temas.get(int(cod_tema)) if cod_tema else None
                        if id_tema:
                            cursor.execute(sql_vincular_tema, (id_proposicao_interno, id_tema))

                    conexao.commit()
                except Exception as e:
                    conexao.rollback()
                    logger.error(f"Erro ao vincular autores/temas do processo {id_processo}: {e}")
                    fila_erros.registrar(f"processo_{id_processo}", e)
                    execucao.incrementar(erros=1)
                    sucesso_total = False

                if j % 200 == 0:
                    logger.info(f"      └─ {j}/{len(com_senador)} detalhes processados.")
                time.sleep(0.2)

            execucao.incrementar(processados=len(processos), registros=gravadas)
            if sucesso_total:
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
        chk_manager.salvar(nome_script, str(ano_atual - 1))
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Proposições do Senado sincronizadas com SUCESSO (universo completo).")
    elif interrompido:
        execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total or interrompido

if __name__ == "__main__":
    if not processar_proposicoes_senado():
        sys.exit(1)
