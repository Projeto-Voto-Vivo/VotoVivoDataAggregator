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
    database=os.getenv("DB_NAME", "votoVivo")
)
cursor = db.cursor()


cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
deputados_db = cursor.fetchall()

print(f"Total de parlamentares no banco: {len(deputados_db)}")
print("Buscando redes sociais...\n")

contador_redes = 0
contador_parlamentares_com_redes = 0

for (id_api, id_interno) in deputados_db:
    
    url_api = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}"
    
    try:
        
        time.sleep(0.3)
        response = requests.get(url_api)
        
        if response.status_code == 200:
            dados_dep = response.json()["dados"]
            redes_sociais = dados_dep.get('redeSocial', []) 
            
            if redes_sociais:
                for link in redes_sociais:
                    
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
                
                db.commit()
                contador_parlamentares_com_redes += 1
                print(f" Parlamentar ID {id_api}: {len(redes_sociais)} rede(s) encontrada(s)")
        
    except Exception as e:
        print(f" Erro ao processar parlamentar ID {id_api}: {e}")

print("\n" + "="*40)
print(f"Importação concluída!")
print(f"Parlamentares com redes: {contador_parlamentares_com_redes}")
print(f"Total de registros: {contador_redes}")
print("="*40)

cursor.close()
db.close()