import requests
import mysql.connector
import time
import os
from dotenv import load_dotenv

load_dotenv()

# CONEXÃO
db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo")
)
cursor = db.cursor()

print("Conectado.\n")


ANO = 2025
MESES = [7, 8, 9]


cursor.execute("""
    SELECT idApi, idParlamentar, cargo
    FROM parlamentar
    WHERE cargo IN ('Deputado Federal', 'Senador')
""")
parlamentares = cursor.fetchall()

print(f"Total parlamentares: {len(parlamentares)}\n")


def despesas_deputado(id_api):
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}/despesas"
    resultado = []

    for mes in MESES:
        pagina = 1

        while True:
            params = {
                "ano": ANO,
                "mes": mes,
                "itens": 100,
                "pagina": pagina
            }

            r = requests.get(url, params=params)

            if r.status_code != 200:
                break

            data = r.json()
            dados = data.get("dados", [])

            if not dados:
                break

            resultado.extend(dados)

            if not any(l["rel"] == "next" for l in data.get("links", [])):
                break

            pagina += 1
            time.sleep(0.05)

    return resultado


def despesas_senador(id_api):
    url = f"https://legis.senado.leg.br/dadosabertos/senador/{id_api}/despesas"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=10)

        if r.status_code != 200:
            return []

        data = r.json()

       
        lista = data.get("ListaDespesasSenador", {}).get("Despesas", {}).get("Despesa", [])

        return lista

    except:
        return []


total = 0

for id_api, id_parlamentar, cargo in parlamentares:

    if cargo == "Deputado Federal":
        despesas = despesas_deputado(id_api)

        valores = []
        for d in despesas:
            valores.append((
                id_parlamentar,
                d.get("dataDocumento"),
                d.get("valorLiquido"),
                d.get("nomeFornecedor"),
                d.get("cnpjCpfFornecedor"),
                d.get("urlDocumento"),
                d.get("tipoDespesa")
            ))

        if valores:
            cursor.executemany("""
                INSERT INTO despesa
                (idParlamentar, dataDespesa, valor, fornecedorNome,
                 fornecedorCnpjCpf, notaFiscalUrl, categoria)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, valores)

            db.commit()

        print(f"Deputado {id_api}: {len(valores)} despesas")

        total += len(valores)

    elif cargo == "Senador":
        despesas = despesas_senador(id_api)

        print(f"Senador {id_api}: {len(despesas)} despesas ")

    time.sleep(0.1)


print("\n==========================")
print(f"TOTAL INSERIDO: {total}")
print("==========================")

cursor.close()
db.close()