import requests
import mysql.connector
import os
import time
from dotenv import load_dotenv

load_dotenv()

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "testpass"),
    database=os.getenv("DB_NAME", "votovivo")
)
cursor = db.cursor()

def popular_temas_camara():
    print("-> Buscando temas da Câmara...")
    url = "https://dadosabertos.camara.leg.br/api/v2/referencias/proposicoes/codTema"
    res = requests.get(url).json()
    
    for t in res.get("dados", []):
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Camara', %s, 'UNICO')
        """, (t['cod'], t['nome']))
    db.commit()

def popular_assuntos_senado():
    print("-> Buscando assuntos do Senado...")
    url = "https://legis.senado.leg.br/dadosabertos/processo/assuntos"
    headers = {"Accept": "application/json"}
    res = requests.get(url, headers=headers).json()
    
    # O Senado retorna uma lista de assuntos
    # Alguns podem ter hierarquia, mas a API de lista simplifica
    if isinstance(res, list):
        assuntos = res
    else:
        assuntos_data = res.get("assuntos", [])
        assuntos = assuntos_data if isinstance(assuntos_data, list) else assuntos_data.get("assunto", [])
    
    for a in assuntos:
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Senado', %s, %s)
        """, (a['id'], a['assuntoEspecifico'], a['assuntoGeral']))
    db.commit()

if __name__ == "__main__":
    popular_temas_camara()
    popular_assuntos_senado()
    print("Catálogo de temas atualizado!")
    cursor.close()
    db.close()
