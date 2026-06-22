from utils.http_client import http_client
from utils.db import get_connection

db, cursor = get_connection()

def popular_temas_camara():
    url = "https://dadosabertos.camara.leg.br/api/v2/referencias/proposicoes/codTema"
    res = http_client.get_safe(url).json()

    dados = res.get("dados", [])
    for t in dados:
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Camara', %s, 'UNICO')
        """, (t['cod'], t['nome']))
    db.commit()

if __name__ == "__main__":
    popular_temas_camara()
    cursor.close()
    db.close()
