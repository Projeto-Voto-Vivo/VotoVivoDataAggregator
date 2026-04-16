import requests
import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        autocommit=False
    )
    cursor = db.cursor()
    print("Conexão estabelecida.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

cursor.execute("""
    SELECT idApi, idVotacao 
    FROM votacao 
    WHERE YEAR(dataVotacao) = 2025 
    AND MONTH(dataVotacao) IN (7,8,9)
""")
votacoes = [(str(row[0]), row[1]) for row in cursor.fetchall()]

total_votos = 0

for id_api, id_votacao in tqdm(votacoes):
    try:
        time.sleep(0.1)

        res = requests.get(
            f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{id_api}/votos",
            timeout=30
        )

        if res.status_code != 200:
            continue

        dados = res.json().get("dados", [])
        if not dados:
            continue

        batch = []

        for v in dados:
            id_dep_api = str(v.get("deputado_", {}).get("id"))

            if id_dep_api == "None":
                continue

            if id_dep_api in map_parlamentares:
                voto_txt = v.get("tipoVoto")

                if voto_txt == "Sim":
                    voto_enum = "SIM"
                elif voto_txt == "Não":
                    voto_enum = "NAO"
                elif voto_txt == "Abstenção":
                    voto_enum = "ABSTENCAO"
                else:
                    continue

                batch.append((
                    map_parlamentares[id_dep_api],
                    id_votacao,
                    f"{id_api}_{id_dep_api}",
                    voto_enum
                ))

        if batch:
            sql = """
                INSERT IGNORE INTO voto 
                (idParlamentar, idVotacao, idApi, votoRegistrado)
                VALUES (%s, %s, %s, %s)
            """
            cursor.executemany(sql, batch)
            db.commit()
            total_votos += len(batch)

    except:
        continue

print(f"Votos inseridos: {total_votos}")

cursor.close()
db.close()