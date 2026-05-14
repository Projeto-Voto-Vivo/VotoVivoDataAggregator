import requests
import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()



db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo"),
    autocommit=False
)

cursor = db.cursor()
print("Conectado ao banco.")



orgaos_cache = {}

cursor.execute("SELECT idOrgao, idApi FROM orgao")
for id_, idApi in cursor.fetchall():
    orgaos_cache[str(idApi)] = id_

def garantir_orgao(uri_orgao, sigla=None):
    if not uri_orgao:
        return None

    id_api = uri_orgao.split("/")[-1]

    if id_api in orgaos_cache:
        return orgaos_cache[id_api]

    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = %s", (id_api,))
    res = cursor.fetchone()

    if res:
        orgaos_cache[id_api] = res[0]
        return res[0]

    nome = None
    try:
        url = f"https://dadosabertos.camara.leg.br/api/v2/orgaos/{id_api}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            dados = r.json().get("dados", {})
            nome = dados.get("nome")
            sigla = dados.get("sigla") or sigla
    except:
        pass

    cursor.execute(
        "INSERT INTO orgao (idApi, sigla, nome) VALUES (%s, %s, %s)",
        (id_api, sigla or "N/A", nome)
    )
    db.commit()

    id_novo = cursor.lastrowid
    orgaos_cache[id_api] = id_novo
    return id_novo



cursor.execute("SELECT idApi, idProposicao FROM proposicao")
map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}



meses = [
    ("2025-07-01", "2025-07-31"),
    ("2025-08-01", "2025-08-31"),
    ("2025-09-01", "2025-09-30"),
]

url = "https://dadosabertos.camara.leg.br/api/v2/votacoes"
lista_ids = []



for inicio, fim in meses:
    pagina = 1
    
    while True:
        params = {
            "dataInicio": inicio,
            "dataFim": fim,
            "itens": 100,
            "pagina": pagina
        }

        res = requests.get(url, params=params, timeout=60)
        if res.status_code != 200:
            break

        dados = res.json().get("dados", [])
        if not dados:
            break

        for v in dados:
            if v.get("id"):
                lista_ids.append(v["id"])

        if len(dados) < 100:
            break

        pagina += 1
        time.sleep(0.2)

lista_ids = list(set(lista_ids))



for id_api in tqdm(lista_ids):
    try:
        res = requests.get(f"{url}/{id_api}", timeout=60)
        if res.status_code != 200:
            continue

        v = res.json().get("dados", {})

        

        id_prop_api = None

        for p in v.get("proposicoesAfetadas", []):
            if p.get("id"):
                id_prop_api = str(p.get("id"))
                break

        if not id_prop_api:
            for obj in v.get("objetosPossiveis", []):
                if obj.get("id"):
                    id_prop_api = str(obj.get("id"))
                    break

        id_proposicao = map_proposicoes.get(id_prop_api)

    

        id_orgao = garantir_orgao(
            v.get("uriOrgao"),
            v.get("siglaOrgao")
        )

        

        dataHora = v.get("dataHoraRegistro")

        if not dataHora:
            data = v.get("data")
            if data:
                dataHora = data + " 00:00:00"

        

        aprovacao = v.get("aprovacao")

        if aprovacao == 1:
            resultado = "Aprovado"
        elif aprovacao == 0:
            resultado = "Rejeitado"
        else:
            resultado = v.get("resultado")

        resumo = v.get("descricao")
        tipo = "SIMBOLICA"

        

        sql = """
            INSERT INTO votacao
            (idApi, idProposicao, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                idProposicao = VALUES(idProposicao),
                idOrgao = VALUES(idOrgao),
                dataHora = VALUES(dataHora),
                resumoMateria = VALUES(resumoMateria),
                resultadoFinal = VALUES(resultadoFinal),
                tipoVotacao = VALUES(tipoVotacao)
        """

        cursor.execute(sql, (
            str(id_api),
            id_proposicao,
            id_orgao,
            dataHora,
            resumo,
            resultado,
            tipo
        ))

        db.commit()

    except Exception as e:
        print(f"Erro na votação {id_api}: {e}")
        continue

cursor.close()
db.close()

print("Importação finalizada.")