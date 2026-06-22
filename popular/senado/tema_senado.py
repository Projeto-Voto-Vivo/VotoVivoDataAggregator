from utils.http_client import http_client
from utils.db import get_connection

db, cursor = get_connection()

def popular_assuntos_senado():
    url = "https://legis.senado.leg.br/dadosabertos/processo/assuntos"
    headers = {"Accept": "application/json"}
    res = http_client.get_safe(url, headers=headers).json()

    if isinstance(res, list):
        assuntos = res
    else:
        assuntos_data = res.get("assuntos", [])
        assuntos = assuntos_data if isinstance(assuntos_data, list) else assuntos_data.get("assunto", [])

    for a in assuntos:
        cursor.execute("""
            INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
            VALUES (%s, 'Senado', %s, 'ESPECIFICO')
        """, (a['id'], a['assuntoEspecifico']))
    db.commit()

if __name__ == "__main__":
    popular_assuntos_senado()
    cursor.close()
    db.close()
