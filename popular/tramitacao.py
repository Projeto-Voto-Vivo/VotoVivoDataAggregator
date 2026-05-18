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

# CACHE (MAPEAMENTOS)
# ============================

cursor.execute("SELECT idProposicao, idApi FROM proposicao")
map_proposicao = {str(api): interno for interno, api in cursor.fetchall()}

cursor.execute("SELECT idTipoTramitacao, idApi FROM tipoTramitacao")
map_tipo = {str(api): interno for interno, api in cursor.fetchall()}

cursor.execute("SELECT idOrgao, idApi FROM orgao")
map_orgao = {str(api): interno for interno, api in cursor.fetchall()}



def extrair_id(uri):
    try:
        return str(int(uri.split("/")[-1]))
    except:
        return None



def importar_tramitacao():

    proposicoes = list(map_proposicao.keys())
    print(f"Total de proposições: {len(proposicoes)}")

    for id_api in tqdm(proposicoes, desc="Importando tramitações"):

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=10)

            if res.status_code != 200:
                continue

            dados = res.json().get("dados", [])

            
            dados.sort(key=lambda x: x.get("sequencia") or 0)

            for t in dados:

                dataHora = t.get("dataHora")
                sequencia = t.get("sequencia")

                if not sequencia:
                    continue

                descricao = t.get("descricaoTramitacao")
                situacao = t.get("descricaoSituacao")
                despacho = t.get("despacho")

                codTipo = t.get("codTipoTramitacao")
                uriOrgao = t.get("uriOrgao")

                id_proposicao = map_proposicao.get(str(id_api))
                id_tipo = map_tipo.get(str(codTipo))

                id_orgao_api = extrair_id(uriOrgao)
                id_orgao = map_orgao.get(id_orgao_api)

                # pula se tipo não existir
                if id_tipo is None:
                    continue

                
                id_api_tramitacao = str(id_api)

                cursor.execute("""
                    INSERT INTO tramitacao (
                        idApi,
                        idProposicao,
                        idTipoTramitacao,
                        idOrgao,
                        dataHora,
                        sequencia,
                        descricaoTramitacao,
                        descricaoSituacao,
                        despacho
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        idTipoTramitacao = VALUES(idTipoTramitacao),
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        descricaoSituacao = VALUES(descricaoSituacao),
                        despacho = VALUES(despacho)
                """, (
                    id_api_tramitacao,
                    id_proposicao,
                    id_tipo,
                    id_orgao,
                    dataHora,
                    sequencia,
                    descricao,
                    situacao,
                    despacho
                ))

            db.commit()
            time.sleep(0.1)

        except Exception as e:
            print(f"\nErro na proposição {id_api}: {e}")



if __name__ == "__main__":
    importar_tramitacao()
    cursor.close()
    db.close()