import os
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.execucao import ExecucaoEtl
import time
from tqdm import tqdm

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)

script_checkpoint = "popular/tipo_tramitacao.py#camara"
execucao = ExecucaoEtl(db, script_checkpoint)

def importar_tipo_tramitacao():
    # Apenas proposições da Câmara: este script consulta a API da Câmara, e os
    # codigoMateria do Senado poderiam colidir com ids de proposições da Câmara.
    cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE casa = 'Camara' AND idApi IS NOT NULL ORDER BY idProposicao ASC")
    proposicoes_banco = cursor.fetchall()
    
    checkpoint_atual = int(chk_manager.obter(script_checkpoint, default_value="0"))
    fila_proposicoes = [p for p in proposicoes_banco if p[0] > checkpoint_atual]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Importando tipos", unit="proposição"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
            break

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = http_client.get_safe(url, timeout=30)
            garantir_conexao(db)

            if res.status_code != 200:
                chk_manager.salvar(script_checkpoint, id_interno)
                db.commit()
                continue

            dados = res.json().get("dados", [])

            for t in dados:
                cod = t.get("codTipoTramitacao")
                desc = t.get("descricaoTramitacao")
                regime = t.get("regime")

                if not cod:
                    continue

                cursor.execute("""
                    INSERT INTO tipoTramitacao (idApi, descricao, regime)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        descricao = VALUES(descricao),
                        regime = VALUES(regime)
                """, (str(cod), desc, regime))

            chk_manager.salvar(script_checkpoint, id_interno)
            db.commit()
            execucao.incrementar(processados=1)

            time.sleep(0.1)

        except Exception:
            db.rollback()
            execucao.incrementar(erros=1)
            continue

if __name__ == "__main__":
    try:
        importar_tipo_tramitacao()
        execucao.finalizar("SUCESSO")
    except KeyboardInterrupt:
        execucao.finalizar("INTERROMPIDO")
    finally:
        cursor.close()
        db.close()
