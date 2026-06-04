import os
import requests
import mysql.connector
import time
import sys
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor()
except mysql.connector.Error:
    sys.exit(1)

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = """
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    """
    cursor.execute(query, (nome_script, str(valor_parametro)))

script_redes = "popular/redeSocial.py"
ultimo_id_interno_chk = int(obter_ultimo_checkpoint(script_redes, default_value="0"))

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar ORDER BY idParlamentar ASC")
deputados_db = cursor.fetchall()

if is_test_mode:
    deputados_db = deputados_db[:10]
    print("[MODO TESTE] Limitando a 10 parlamentares.")

contador_redes = 0
contador_parlamentares_com_redes = 0

start_time = time.time()
try:
    for (id_api, id_interno) in deputados_db:
        if id_interno <= ultimo_id_interno_chk:
            continue

        url_api = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}"
        
        try:
            time.sleep(0.3)
            response = requests.get(url_api, timeout=15)
            
            if response.status_code == 200:
                dados_dep = response.json().get("dados", {})
                redes_sociais = dados_dep.get('redeSocial', []) 
                
                if db.in_transaction:
                    db.commit()

                db.start_transaction()

                if redes_sociais:
                    for link in redes_sociais:
                        if not link:
                            continue
                            
                        link_lower = link.lower()
                        if 'instagram' in link_lower:
                            plataforma = 'Instagram'
                        elif 'facebook' in link_lower:
                            plataforma = 'Facebook'
                        elif 'twitter' in link_lower or 'x.com' in link_lower:
                            plataforma = 'Twitter/X'
                        elif 'youtube' in link_lower:
                            plataforma = 'YouTube'
                        elif 'tiktok' in link_lower:
                            plataforma = 'TikTok'
                        else:
                            plataforma = 'Outros'

                        sql = """
                            INSERT IGNORE INTO redeSocial
                            (idParlamentar, plataforma, url)
                            VALUES (%s, %s, %s)
                        """
                        cursor.execute(sql, (id_interno, plataforma, link))
                        contador_redes += 1
                    
                    contador_parlamentares_com_redes += 1
                
                salvar_checkpoint_transacao(script_redes, id_interno)
                db.commit()

            else:
                if db.in_transaction:
                    db.commit()
                db.start_transaction()
                salvar_checkpoint_transacao(script_redes, id_interno)
                db.commit()

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
                break

        except Exception:
            continue

except KeyboardInterrupt:
    if db.in_transaction:
        db.rollback()

cursor.close()
db.close()