import os
import sys
import time
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Orgao_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# COALESCE: uma linha do catálogo nunca apaga metadado já existente com NULL,
# mas corrige os placeholders ('N/A' / 'Órgão não mapeado') criados pelo
# OrgaoCache — o idOrgao interno não muda, então nada em tramitacao/votacao/
# evento precisa ser recoletado.
SQL_ORGAO = """
    INSERT INTO orgao (idApi, sigla, nome, tipoOrgao, casa)
    VALUES (%s, %s, %s, %s, 'Camara')
    ON DUPLICATE KEY UPDATE
        sigla = COALESCE(VALUES(sigla), sigla),
        nome = COALESCE(VALUES(nome), nome),
        tipoOrgao = COALESCE(VALUES(tipoOrgao), tipoOrgao)
"""
SQL_MEMBRO = "INSERT IGNORE INTO membroOrgao (idParlamentar, idOrgao, cargo) VALUES (%s, %s, %s)"


# ---------------------------------------------------------
# 1. CATÁLOGO COMPLETO DE ÓRGÃOS
# ---------------------------------------------------------
def carregar_catalogo_orgaos(cursor, conexao):
    """Catálogo completo em ~17 requisições (/orgaos?itens=100), no lugar de
    513 paginações por deputado + uma chamada de detalhe por órgão inédito.

    Inclui órgãos dos quais nenhum deputado é membro — Plenário (180) e
    Coordenação de Comissões Permanentes (186), justamente os que mais aparecem
    na tramitação. O item da lista já traz sigla, nome e tipoOrgao."""
    pagina, linhas = 1, []
    while True:
        resp = http_client.get_safe(
            f"{BASE_URL}/orgaos?itens=100&pagina={pagina}",
            headers={'accept': 'application/json'},
        )
        if resp.status_code != 200:
            logger.error(f"Falha ao carregar catálogo de órgãos (página {pagina}, HTTP {resp.status_code}).")
            return False

        dados = resp.json().get('dados', [])
        if not dados:
            break

        linhas += [
            (str(o['id']), o.get('sigla'), o.get('nome'), o.get('tipoOrgao'))
            for o in dados if o.get('id')
        ]
        pagina += 1

    garantir_conexao(conexao)
    for i in range(0, len(linhas), 500):
        cursor.executemany(SQL_ORGAO, linhas[i:i + 500])
    conexao.commit()
    logger.info(f"Catálogo de órgãos carregado: {len(linhas)} registros.")
    return True


def obter_deputados_ativos(cursor):
    cursor.execute("""
        SELECT idParlamentar, idApi, nomeUrna FROM parlamentar
        WHERE cargo = 'Deputado(a)' ORDER BY idParlamentar ASC
    """)
    return cursor.fetchall()


def carregar_cache_orgaos(cursor):
    cursor.execute("SELECT idApi, idOrgao FROM orgao WHERE casa = 'Camara'")
    return {str(row[0]): row[1] for row in cursor.fetchall()}


# ---------------------------------------------------------
# 2. MEMBRESIAS (um commit por deputado)
# ---------------------------------------------------------
def processar_orgaos_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "orgao_camara_v2"
    execucao = ExecucaoEtl(conexao, nome_script)

    if not carregar_catalogo_orgaos(cursor, conexao):
        execucao.finalizar("FALHA", "catálogo de órgãos indisponível")
        cursor.close()
        conexao.close()
        return False

    deputados = obter_deputados_ativos(cursor)
    map_orgaos = carregar_cache_orgaos(cursor)

    total_deputados = len(deputados)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    data_corte = f"{int(os.getenv('ANO_INICIO_ETL', '2023'))}-01-01"
    sucesso_total = True

    for i, (id_parlamentar, id_api_deputado, nome_urna) in enumerate(deputados, 1):
        if id_parlamentar <= ultimo_processado:
            continue

        logger.info(f"[{i}/{total_deputados}] Buscando órgãos/comissões de: {nome_urna}")
        sucesso_deputado = True
        membros = []          # (idParlamentar, idApi do órgão, cargo)
        orgaos_fora_catalogo = {}

        pagina = 1
        while True:
            url_lista = f"{BASE_URL}/deputados/{id_api_deputado}/orgaos?ordem=ASC&ordenarPor=dataInicio&pagina={pagina}&itens=100"
            resp_lista = http_client.get_safe(url_lista, headers={'accept': 'application/json'})
            if resp_lista.status_code != 200:
                logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                sucesso_deputado = False
                break

            lista_orgaos = resp_lista.json().get('dados', [])
            if not lista_orgaos:
                break

            for org_basico in lista_orgaos:
                id_orgao_api = str(org_basico.get('idOrgao') or '')
                cargo_parlamentar = org_basico.get('titulo')
                if not id_orgao_api or not cargo_parlamentar:
                    continue

                # Apenas vínculos ativos na janela do ETL
                data_fim = org_basico.get('dataFim')
                if data_fim and data_fim < data_corte:
                    continue

                if id_orgao_api not in map_orgaos:
                    orgaos_fora_catalogo[id_orgao_api] = (
                        id_orgao_api, org_basico.get('siglaOrgao'), org_basico.get('nomeOrgao'), None,
                    )
                membros.append((id_parlamentar, id_orgao_api, cargo_parlamentar))

            pagina += 1

        if sucesso_deputado:
            try:
                garantir_conexao(conexao)
                if orgaos_fora_catalogo:
                    cursor.executemany(SQL_ORGAO, list(orgaos_fora_catalogo.values()))
                    for id_api_novo in orgaos_fora_catalogo:
                        cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = 'Camara'", (id_api_novo,))
                        res = cursor.fetchone()
                        if res:
                            map_orgaos[id_api_novo] = res[0]

                linhas_membro = [
                    (id_parl, map_orgaos[id_api_org], cargo)
                    for id_parl, id_api_org, cargo in membros if id_api_org in map_orgaos
                ]
                if linhas_membro:
                    cursor.executemany(SQL_MEMBRO, linhas_membro)
                conexao.commit()
            except Exception as e:
                conexao.rollback()
                logger.error(f"Erro ao salvar membresias do deputado {id_api_deputado}: {e}")
                sucesso_deputado = False

        if not sucesso_deputado:
            sucesso_total = False
        execucao.incrementar(processados=1, registros=len(membros), erros=0 if sucesso_deputado else 1)

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
        time.sleep(0.1)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("=== Sincronização de Órgãos e Membros da Câmara FINALIZADA ===")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total


if __name__ == "__main__":
    if not processar_orgaos_camara():
        sys.exit(1)
