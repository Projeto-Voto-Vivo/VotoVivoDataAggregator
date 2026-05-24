import os
import requests
import mysql.connector
import time
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "testpass"),
    database=os.getenv("DB_NAME", "votovivo")
)
cursor = db.cursor()

def importar_tipo_tramitacao():

    cursor.execute("SELECT idApi FROM proposicao")
    proposicoes = cursor.fetchall()

    print(f"Total de proposições: {len(proposicoes)}")

    inseridos = 0

    for (id_api,) in tqdm(proposicoes, desc="Importando tipos", unit="proposição"):

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=10)

            if res.status_code != 200:
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
                """, (cod, desc, regime))

                inseridos += 1

            
            db.commit()

            
            time.sleep(0.1)

        except Exception as e:
            print(f"\nErro na proposição {id_api}: {e}")

    print(f"\nRegistros processados: {inseridos}")

if __name__ == "__main__":
    importar_tipo_tramitacao()
    cursor.close()
    db.close()