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

logger = get_logger("ETL_Tramitacao_Senado")

BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Senado")

script_senado = "popular/tramitacao.py#senado"
fila_erros = EtlErro(db, script_senado)
execucao = ExecucaoEtl(db, script_senado)

HEADERS = {"Accept": "application/json"}

SQL_TRAMITACAO = """
    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
    VALUES (%s, %s, NULL, %s, %s, %s, %s, NULL, %s)
    ON DUPLICATE KEY UPDATE
        idOrgao = VALUES(idOrgao),
        dataHora = VALUES(dataHora),
        descricaoTramitacao = VALUES(descricaoTramitacao),
        despacho = VALUES(despacho)
"""


def buscar_movimentacoes(item):
    _, id_api = item
    return http_client.get_safe(f"{BASE_URL_SENADO}/materia/movimentacoes/{id_api}", headers=HEADERS, timeout=30)


def importar_tramitacao_senado():
    checkpoint_atual = int(chk_manager.obter(script_senado, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        WHERE p.casa = 'Senado' AND p.idApi IS NOT NULL AND p.idProposicao > %s
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

    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()
    barra = tqdm(total=len(fila_proposicoes), desc="Tramitações Senado")

    # Fetch em paralelo por lote; gravação e checkpoint sequenciais, na ordem
    # original da fila (o checkpoint assume ordem crescente de idProposicao).
    for lote in em_lotes(fila_proposicoes):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
            break

        respostas = buscar_lote(lote, buscar_movimentacoes)
        garantir_conexao(db)

        for (id_interno, id_api), res in zip(lote, respostas):
            try:
                if isinstance(res, Exception):
                    raise res

                if res.status_code != 200:
                    if id_interno > checkpoint_atual:
                        chk_manager.salvar(script_senado, id_interno)
                    db.commit()
                    continue

                dados_materia = res.json().get("MovimentacaoMateria", {}).get("Materia", {})
                historico = dados_materia.get("HistoricoMovimentacoes", {}).get("Movimentacao", [])
                if isinstance(historico, dict):
                    historico = [historico]
                historico.sort(key=lambda x: int(x.get("CodigoTramitacao") or 0))

                linhas = []
                for seq, m in enumerate(historico, start=1):
                    cod_tramitacao = m.get("CodigoTramitacao")
                    if not cod_tramitacao:
                        continue

                    data_hora = m.get("DataTramitacao")
                    if data_hora and len(data_hora) == 10:
                        data_hora = f"{data_hora} 00:00:00"

                    descr_tramitacao = m.get("DescricaoComissao") or m.get("IdentificacaoOrgao") or "Senado"
                    id_orgao = orgaos.garantir(
                        m.get("CodigoComissao") or m.get("CodigoOrgao"),
                        m.get("SiglaComissao") or m.get("SiglaOrgao"),
                        m.get("DescricaoComissao") or m.get("NomeOrgao"),
                    )
                    linhas.append((
                        f"SEN_{id_api}_{cod_tramitacao}", id_interno, id_orgao, data_hora, seq,
                        descr_tramitacao, m.get("TextoParecer") or m.get("DescricaoUltimaSituacao"),
                    ))

                if linhas:
                    cursor.executemany(SQL_TRAMITACAO, linhas)

                # Um item reprocessado da fila de erros não pode regredir o cursor
                if id_interno > checkpoint_atual:
                    chk_manager.salvar(script_senado, id_interno)
                db.commit()
                if id_interno in pendentes:
                    fila_erros.resolver(id_interno)
                execucao.incrementar(processados=1, registros=len(linhas))

            except Exception as e:
                db.rollback()
                logger.error(f"Erro ao importar tramitações da matéria {id_interno} ({id_api}): {e}")
                fila_erros.registrar(id_interno, e)
                execucao.incrementar(erros=1)

        barra.update(len(lote))

    barra.close()


if __name__ == "__main__":
    try:
        importar_tramitacao_senado()
        execucao.finalizar("SUCESSO")
    except KeyboardInterrupt:
        execucao.finalizar("INTERROMPIDO")
    finally:
        orgaos.fechar()
        cursor.close()
        db.close()
