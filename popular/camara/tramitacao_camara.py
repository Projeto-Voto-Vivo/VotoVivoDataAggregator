import os
import time
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.orgao_cache import OrgaoCache
from utils.paralelo import buscar_lote, em_lotes

logger = get_logger("ETL_Tramitacao_Camara")

BASE_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Camara")

script_camara = "popular/tramitacao.py#camara"
fila_erros = EtlErro(db, script_camara)
execucao = ExecucaoEtl(db, script_camara)

cursor.execute("SELECT idTipoTramitacao, idApi FROM tipoTramitacao")
map_tipo = {str(row[1]): row[0] for row in cursor.fetchall()}

SQL_TRAMITACAO = """
    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        idTipoTramitacao = VALUES(idTipoTramitacao),
        idOrgao = VALUES(idOrgao),
        dataHora = VALUES(dataHora),
        descricaoTramitacao = VALUES(descricaoTramitacao),
        descricaoSituacao = VALUES(descricaoSituacao),
        despacho = VALUES(despacho)
"""


def extrair_id(uri):
    try:
        return str(int(uri.split("/")[-1]))
    except Exception:
        return None


def buscar_tramitacoes(item):
    _, id_api = item
    return http_client.get_safe(f"{BASE_URL_CAMARA}/proposicoes/{id_api}/tramitacoes", timeout=30)


def importar_tramitacao_camara():
    checkpoint_atual = int(chk_manager.obter(script_camara, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        WHERE p.casa = 'Camara' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila_proposicoes = cursor.fetchall()

    # Reprocesso: proposições que falharam em execuções anteriores voltam à
    # fila mesmo estando atrás do checkpoint.
    pendentes = {int(c) for c in fila_erros.listar_pendentes() if str(c).isdigit()}
    if pendentes:
        placeholders = ",".join(["%s"] * len(pendentes))
        cursor.execute(f"""
            SELECT p.idProposicao, p.idApi FROM proposicao p
            WHERE p.idProposicao IN ({placeholders}) AND p.idApi IS NOT NULL
        """, tuple(pendentes))
        fila_proposicoes = cursor.fetchall() + fila_proposicoes
        logger.info(f"{len(pendentes)} proposições com erro pendente serão reprocessadas.")

    start_time = time.time()
    barra = tqdm(total=len(fila_proposicoes), desc="Tramitações Câmara")

    # Fetch em paralelo por lote; gravação e checkpoint sequenciais, na ordem
    # original da fila (o checkpoint assume ordem crescente de idProposicao).
    for lote in em_lotes(fila_proposicoes):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
            break

        respostas = buscar_lote(lote, buscar_tramitacoes)
        garantir_conexao(db)

        for (id_interno, id_api), res in zip(lote, respostas):
            try:
                if isinstance(res, Exception):
                    raise res

                if res.status_code != 200:
                    if id_interno > checkpoint_atual:
                        chk_manager.salvar(script_camara, id_interno)
                    db.commit()
                    continue

                dados = res.json().get("dados", [])
                dados.sort(key=lambda x: x.get("sequencia") or 0)

                linhas = []
                for t in dados:
                    sequencia = t.get("sequencia")
                    if not sequencia:
                        continue
                    id_tipo = map_tipo.get(str(t.get("codTipoTramitacao")))
                    if id_tipo is None:
                        continue

                    # siglaOrgao vem na própria resposta: o placeholder nasce (ou
                    # é corrigido) já com a sigla real em vez de 'N/A'.
                    id_orgao = orgaos.garantir(extrair_id(t.get("uriOrgao")), t.get("siglaOrgao"))
                    linhas.append((
                        f"{id_api}_{sequencia}", id_interno, id_tipo, id_orgao, t.get("dataHora"),
                        sequencia, t.get("descricaoTramitacao"), t.get("descricaoSituacao"), t.get("despacho"),
                    ))

                if linhas:
                    cursor.executemany(SQL_TRAMITACAO, linhas)

                # Um item reprocessado da fila de erros não pode regredir o cursor
                if id_interno > checkpoint_atual:
                    chk_manager.salvar(script_camara, id_interno)
                db.commit()
                if id_interno in pendentes:
                    fila_erros.resolver(id_interno)
                execucao.incrementar(processados=1, registros=len(linhas))

            except Exception as e:
                db.rollback()
                logger.error(f"Erro ao importar tramitações da proposição {id_interno} ({id_api}): {e}")
                fila_erros.registrar(id_interno, e)
                execucao.incrementar(erros=1)

        barra.update(len(lote))

    barra.close()


if __name__ == "__main__":
    try:
        importar_tramitacao_camara()
        execucao.finalizar("SUCESSO")
    except KeyboardInterrupt:
        execucao.finalizar("INTERROMPIDO")
    finally:
        orgaos.fechar()
        cursor.close()
        db.close()
