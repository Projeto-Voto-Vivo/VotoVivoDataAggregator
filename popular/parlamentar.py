import requests
import mysql.connector
import time
import os 
from dotenv import load_dotenv

load_dotenv()


try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor()

    cursor.execute("SELECT DATABASE()")
    print(" Banco conectado:", cursor.fetchone()[0])

    print(" Conexão estabelecida.")
except mysql.connector.Error as err:
    print(f" Erro de conexão: {err}")
    exit(1)

sql_insert = """
    INSERT INTO parlamentar 
    (idApi, cargo, nomeCivil, nomeUrna, partidoAtual, uf, fotoUrl, dataNascimento, email, telefone, enderecoGabinete)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE 
    cargo = VALUES(cargo),
    partidoAtual = VALUES(partidoAtual),
    fotoUrl = VALUES(fotoUrl),
    email = VALUES(email),
    telefone = VALUES(telefone),
    enderecoGabinete = VALUES(enderecoGabinete),
    dataNascimento = VALUES(dataNascimento)
"""


print("\n Importando Deputados...")
url_camara = "https://dadosabertos.camara.leg.br/api/v2/deputados"
pagina = 1
total_deputados = 0

while True:
    response = requests.get(url_camara, params={"pagina": pagina, "itens": 100}, timeout=20)
    dados = response.json().get("dados", [])

    if not dados:
        break

    for dep in dados:
        id_api = dep.get("id")
        nome = dep.get("nome")
        email = dep.get("email")
        partido = dep.get("siglaPartido")
        uf = dep.get("siglaUf")
        foto = dep.get("urlFoto")

        telefone = None
        endereco = None
        data_nascimento = None

       
        try:
            detalhe_url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}"
            resp = requests.get(detalhe_url, timeout=10)

            if resp.status_code == 200:
                detalhe = resp.json().get("dados", {})
                gabinete = detalhe.get("ultimoStatus", {}).get("gabinete", {})

                telefone = gabinete.get("telefone")
                data_nascimento = detalhe.get("dataNascimento")

                predio = gabinete.get("predio")
                sala = gabinete.get("sala")

                if predio and sala:
                    endereco = f"Anexo {predio}, Sala {sala}"
                elif sala:
                    endereco = f"Sala {sala}"
                else:
                    endereco = None

        except Exception as e:
            print(f" Erro deputado {id_api}: {e}")

        valores = (
            id_api, "Deputado Federal", nome, nome,
            partido, uf, foto,
            data_nascimento, email, telefone, endereco
        )

        try:
            cursor.execute(sql_insert, valores)
            total_deputados += 1
        except Exception as e:
            print(f" Erro insert deputado {id_api}: {e}")

        time.sleep(0.2)

    db.commit()
    print(f" Página {pagina} | Total: {total_deputados}")
    pagina += 1



print("\n🚀 Importando Senadores...")
url_senado = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
headers = {"Accept": "application/json"}
total_senadores = 0

res = requests.get(url_senado, headers=headers, timeout=30)

if res.status_code == 200:
    lista = res.json()["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]

    for sen in lista:
        ident = sen["IdentificacaoParlamentar"]
        codigo = ident["CodigoParlamentar"]

        nome = ident["NomeParlamentar"]
        nome_completo = ident["NomeCompletoParlamentar"]
        partido = ident.get("SiglaPartidoParlamentar", "S/PARTIDO")
        uf = ident["UfParlamentar"]
        foto = ident.get("UrlFotoParlamentar")
        email = ident.get("EmailParlamentar")

        telefone = None
        endereco = "Senado Federal, Praça dos Três Poderes"
        data_nascimento = None

        
        try:
            detalhe_url = f"https://legis.senado.leg.br/dadosabertos/senador/{codigo}"
            resp = requests.get(detalhe_url, headers=headers, timeout=10)

            if resp.status_code == 200:
                detalhe = resp.json()

                dados_basicos = detalhe.get("DetalheParlamentar", {}) \
                                       .get("Parlamentar", {}) \
                                       .get("DadosBasicosParlamentar", {})

                data_nascimento = dados_basicos.get("DataNascimento")

        except Exception as e:
            print(f" Erro senador {codigo}: {e}")

        valores = (
            codigo, "Senador",
            nome_completo, nome,
            partido, uf, foto,
            data_nascimento, email, telefone, endereco
        )

        try:
            cursor.execute(sql_insert, valores)
            total_senadores += 1
        except Exception as e:
            print(f" Erro insert senador {codigo}: {e}")

        time.sleep(0.2)

    db.commit()


print("\n" + "="*50)
print(" IMPORTAÇÃO CONCLUÍDA")
print("="*50)
print(f" Deputados: {total_deputados}")
print(f" Senadores: {total_senadores}")
print(f" Total: {total_deputados + total_senadores}")
print("="*50)


print("\n🔍 Testando dados:")
cursor.execute("""
    SELECT nomeUrna, dataNascimento, telefone, enderecoGabinete
    FROM parlamentar
    LIMIT 10
""")

for row in cursor.fetchall():
    print(row)

cursor.close()
db.close()
print("\n🔌 Conexão encerrada")