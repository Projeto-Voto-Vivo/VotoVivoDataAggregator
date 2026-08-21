import os
import time
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.paralelo import buscar_lote, em_lotes

logger = get_logger("ETL_Tipo_Tramitacao")

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)

script_checkpoint = "popular/tipo_tramitacao.py#camara"
execucao = ExecucaoEtl(db, script_checkpoint)

SQL_TIPO = """
    INSERT INTO tipoTramitacao (idApi, descricao, regime)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
        descricao = VALUES(descricao),
        regime = VALUES(regime)
"""


def buscar_tramitacoes(item):
    _, id_api = item
    return http_client.get_safe(f"{BASE_URL}/proposicoes/{id_api}/tramitacoes", timeout=30)


def importar_tipo_tramitacao():
    # Apenas proposições da Câmara: este script consulta a API da Câmara, e os
    # codigoMateria do Senado poderiam colidir com ids de proposições da Câmara.
    cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE casa = 'Camara' AND idApi IS NOT NULL ORDER BY idProposicao ASC")
    proposicoes_banco = cursor.fetchall()

    checkpoint_atual = int(chk_manager.obter(script_checkpoint, default_value="0"))
    fila_proposicoes = [p for p in proposicoes_banco if p[0] > checkpoint_atual]

    start_time = time.time()
    barra = tqdm(total=len(fila_proposicoes), desc="Importando tipos", unit="proposição")

    # Fetch em paralelo por lote; gravação e checkpoint sequenciais, em ordem.
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
                    chk_manager.salvar(script_checkpoint, id_interno)
                    db.commit()
                    continue

                tipos = {}
                for t in res.json().get("dados", []):
                    cod = t.get("codTipoTramitacao")
                    if cod:
                        tipos[str(cod)] = (str(cod), t.get("descricaoTramitacao"), t.get("regime"))

                if tipos:
                    cursor.executemany(SQL_TIPO, list(tipos.values()))

                chk_manager.salvar(script_checkpoint, id_interno)
                db.commit()
                execucao.incrementar(processados=1, registros=len(tipos))

            except Exception as e:
                db.rollback()
                logger.error(f"Erro ao importar tipos da proposição {id_interno} ({id_api}): {e}")
                execucao.incrementar(erros=1)

        barra.update(len(lote))

    barra.close()


if __name__ == "__main__":
    try:
        importar_tipo_tramitacao()
        execucao.finalizar("SUCESSO")
    except KeyboardInterrupt:
        execucao.finalizar("INTERROMPIDO")
    finally:
        cursor.close()
        db.close()
