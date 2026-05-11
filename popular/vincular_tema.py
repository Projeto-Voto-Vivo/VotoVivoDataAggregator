import requests
import mysql.connector
import os
import time
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

db = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
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
                id_tema = mapa_temas.get((t['cod'], 'Camara'))
                if id_tema:
                    cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))
            db.commit()
            time.sleep(0.1) # Evitar rate limit
        except: continue

def vincular_senado():
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Senado'
    """)
    props = cursor.fetchall()
    
    print("Vinculando assuntos do Senado...")
    for id_interno, id_api in tqdm(props):
        try:
            url = f"https://legis.senado.leg.br/dadosabertos/processo/{id_api}"
            res = requests.get(url, headers={"Accept": "application/json"}).json()
            
            # No Senado, o assunto vem direto no detalhe do processo
            assunto = res.get("processo", {}).get("assunto", {})
            if not assunto: continue
            
            # Tenta pegar Geral e Específico
            cods = []
            if 'assuntoGeral' in assunto: cods.append(assunto['assuntoGeral'].get('codigo'))
            if 'assuntoEspecifico' in assunto: cods.append(assunto['assuntoEspecifico'].get('codigo'))
            
            for c in cods:
                if c:
                    id_tema = mapa_temas.get((int(c), 'Senado'))
                    if id_tema:
                        cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))
            db.commit()
        except: continue

if __name__ == "__main__":
    vincular_camara()
    vincular_senado()
    print("Vinculação concluída!")
    cursor.close()
    db.close()
