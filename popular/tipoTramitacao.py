import os
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
import time
from tqdm import tqdm

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)

script_checkpoint = "popular/tipo_tramitacao.py#camara"

def importar_tipo_tramitacao():
    cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE idApi IS NOT NULL ORDER BY idProposicao ASC")
    proposicoes_banco = cursor.fetchall()
    
    checkpoint_atual = int(chk_manager.obter(script_checkpoint, default_value="0"))
    fila_proposicoes = [p for p in proposicoes_banco if p[0] > checkpoint_atual]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Importando tipos", unit="proposição"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = http_client.get_safe(url, timeout=30)

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
            
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_tipo_tramitacao()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()
