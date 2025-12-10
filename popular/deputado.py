import requests
import mysql.connector
import time
import os  
from dotenv import load_dotenv

load_dotenv()

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votovivo")
)

cursor = db.cursor()

url = "https://dadosabertos.camara.leg.br/api/v2/deputados"
pagina = 1
total_importados = 0

print(" Iniciando importação de deputados...")

while True:
    response = requests.get(url, params={"pagina": pagina, "itens": 100})
    dados = response.json()["dados"]

    if not dados:
        break  

    for dep in dados:
        id_dep = dep["id"]

        detalhe = requests.get(f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_dep}").json()["dados"]

        sql = """
            INSERT IGNORE INTO Deputado
            (idDeputado, nomeCivil, cpf, sexo, dataNascimento, ufNascimento, municipioNascimento,
             dataFalecimento, escolaridade, urlDetalhes, urlWebsite)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """

        valores = (
            id_dep,  
            detalhe.get('nomeCivil'),
            detalhe.get('cpf'),
            detalhe.get('sexo'),
            detalhe.get('dataNascimento'),
            detalhe.get('ufNascimento'),
            detalhe.get('municipioNascimento'),
            detalhe.get('dataFalecimento'),
            detalhe.get('escolaridade'),
            detalhe.get('uri'),  
            detalhe.get('urlWebsite')
        )

        cursor.execute(sql, valores)
        db.commit()
        
        total_importados += 1
        
        if total_importados % 50 == 0:
            print(f" {total_importados} deputados importados...")
        
        time.sleep(0.1) 

    pagina += 1

print(f"\n Importação concluída! Total: {total_importados} deputados importados com sucesso!")

cursor.close()
db.close()
