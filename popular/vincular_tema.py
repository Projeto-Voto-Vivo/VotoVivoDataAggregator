import requests
import mysql.connector
import os
import time
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "testpass"),
    database=os.getenv("DB_NAME", "votovivo")
)
cursor = db.cursor(buffered=True)

# Mapeia o catálogo local para busca rápida {(codigoExterno, casa): idTema}
cursor.execute("SELECT codigoExterno, casa, idTema FROM tema")
mapa_temas = {(row[0], row[1]): row[2] for row in cursor.fetchall()}

def vincular_camara():
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Camara'
    """)
    props = cursor.fetchall()

    print("Vinculando temas da Câmara...")
    for id_interno, id_api in tqdm(props):
        try:
            url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_api}/temas"
            res = requests.get(url).json().get("dados", [])
            for t in res:
                id_tema = mapa_temas.get((t['codTema'], 'Camara'))
                if id_tema:
                    cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))
            db.commit()
            time.sleep(0.1)
        except Exception as e:
            print(f"  Erro Câmara id_api={id_api}: {e}")
            continue

def vincular_senado():
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Senado'
    """)
    props = cursor.fetchall()

    print("Vinculando classificações do Senado...")
    for id_interno, id_api in tqdm(props):
        try:
            url = f"https://legis.senado.leg.br/dadosabertos/processo/{id_api}?v=1"
            res = requests.get(url, headers={"Accept": "application/json"}).json()

            classificacoes = res.get("classificacoes") or []
            for c in classificacoes:
                codigo = c.get("codigo")
                if not codigo:
                    continue

                id_tema = mapa_temas.get((codigo, 'Senado'))
                if not id_tema:
                    # Classificação nova: inserir no catálogo e atualizar o mapa local
                    cursor.execute("""
                        INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
                        VALUES (%s, 'Senado', %s, 'UNICO')
                    """, (codigo, c.get("descricao", "")))
                    cursor.execute("SELECT idTema FROM tema WHERE codigoExterno=%s AND casa='Senado'", (codigo,))
                    row = cursor.fetchone()
                    if row:
                        id_tema = row[0]
                        mapa_temas[(codigo, 'Senado')] = id_tema

                if id_tema:
                    cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))

            db.commit()
            time.sleep(0.1)
        except Exception as e:
            print(f"  Erro Senado id_api={id_api}: {e}")
            continue

if __name__ == "__main__":
    vincular_camara()
    vincular_senado()
    print("Vinculação concluída!")
    cursor.close()
    db.close()
