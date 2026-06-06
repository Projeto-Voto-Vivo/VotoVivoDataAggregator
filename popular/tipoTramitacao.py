import os
import requests
import mysql.connector
import time
import sys
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votovivo")
    )
    cursor = db.cursor()
except mysql.connector.Error:
    sys.exit(1)

script_checkpoint = "popular/tipo_tramitacao.py#camara"

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = """
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    """
    cursor.execute(query, (nome_script, str(valor_parametro)))

def importar_tipo_tramitacao():
    cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE idApi IS NOT NULL ORDER BY idProposicao ASC")
    proposicoes_banco = cursor.fetchall()
    
    checkpoint_atual = int(obter_ultimo_checkpoint(script_checkpoint, default_value="0"))
    fila_proposicoes = [p for p in proposicoes_banco if p[0] > checkpoint_atual]

    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Importando tipos", unit="proposição"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=30)

            if res.status_code != 200:
                salvar_checkpoint_transacao(script_checkpoint, id_interno)
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

            salvar_checkpoint_transacao(script_checkpoint, id_interno)
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
