import requests
import mysql.connector
import os
import time
import sys
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "test"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor()
except mysql.connector.Error:
    sys.exit(1)

def popular_temas_camara():
    url = "https://dadosabertos.camara.leg.br/api/v2/referencias/proposicoes/codTema"
    res = requests.get(url).json()

    dados = res.get("dados", [])
    if is_test_mode:
        dados = dados[:10]
        print("[MODO TESTE] Limitando a 10 temas da Câmara.")

    for t in dados:
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Camara', %s, 'UNICO')
        """, (t['cod'], t['nome']))
    db.commit()

def popular_assuntos_senado():
    url = "https://legis.senado.leg.br/dadosabertos/processo/assuntos"
    headers = {"Accept": "application/json"}
    res = requests.get(url, headers=headers).json()
    
    if isinstance(res, list):
        assuntos = res
    else:
        assuntos_data = res.get("assuntos", [])
        assuntos = assuntos_data if isinstance(assuntos_data, list) else assuntos_data.get("assunto", [])

    if is_test_mode:
        assuntos = assuntos[:10]
        print("[MODO TESTE] Limitando a 10 assuntos do Senado.")

    for a in assuntos:
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Senado', %s, %s)
        """, (a['id'], a['assuntoEspecifico'], a['assuntoGeral']))
    db.commit()

if __name__ == "__main__":
    popular_temas_camara()
    popular_assuntos_senado()
    cursor.close()
    db.close()