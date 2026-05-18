import requests
import mysql.connector
import time
from tqdm import tqdm

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "votoVivo"
}

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"

db = mysql.connector.connect(**DB_CONFIG)
cursor = db.cursor()



def extrair_id_orgao(uri):
    try:
        return int(uri.split("/")[-1])
    except:
        return None



def buscar_nome_orgao(id_api):
    try:
        url = f"{BASE_URL}/orgaos/{id_api}"
        r = requests.get(url, timeout=10)

        if r.status_code == 200:
            return r.json().get("dados", {}).get("nome")
    except:
        pass

    return None



def importar_orgaos():
    cursor.execute("SELECT idApi FROM proposicao")
    proposicoes = cursor.fetchall()

    total = len(proposicoes)
    print(f"Total de proposições: {total}")

    for (id_api,) in tqdm(proposicoes, desc="Importando órgãos", unit="proposição"):

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=10)

            if res.status_code != 200:
                continue

            dados = res.json().get("dados", [])

            orgaos_unicos = {}

            for t in dados:
                uri = t.get("uriOrgao")
                sigla = t.get("siglaOrgao")

                id_orgao_api = extrair_id_orgao(uri)

                if id_orgao_api and id_orgao_api not in orgaos_unicos:
                    orgaos_unicos[id_orgao_api] = sigla or "N/A"

            for id_orgao_api, sigla in orgaos_unicos.items():

                
                cursor.execute(
                    "SELECT idOrgao, nome FROM orgao WHERE idApi = %s",
                    (id_orgao_api,)
                )
                existente = cursor.fetchone()

                if existente:
                    
                    if not existente[1]:
                        nome = buscar_nome_orgao(id_orgao_api)
                        if nome:
                            cursor.execute("""
                                UPDATE orgao
                                SET nome = %s
                                WHERE idApi = %s
                            """, (nome, id_orgao_api))
                else:
                    
                    nome = buscar_nome_orgao(id_orgao_api)

                    cursor.execute("""
                        INSERT INTO orgao (idApi, sigla, nome)
                        VALUES (%s, %s, %s)
                    """, (id_orgao_api, sigla, nome))

            db.commit()
            time.sleep(0.1)

        except Exception as e:
            print(f"\nErro na proposição {id_api}: {e}")



if __name__ == "__main__":
    importar_orgaos()
    cursor.close()
    db.close()