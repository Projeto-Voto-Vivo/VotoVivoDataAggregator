from utils.http_client import http_client
from utils.db import get_connection
import time
import os

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()

print("=" * 80)
print(" IMPORTAÇÃO DE PARTIDOS")
print("=" * 80)

url_partidos = "https://dadosabertos.camara.leg.br/api/v2/partidos"
params = {
    "itens": 100,
    "ordem": "ASC",
    "ordenarPor": "sigla"
}

total_partidos = 0

try:
    print("\n Buscando lista de partidos na API...\n")
    
    response = http_client.get_safe(url_partidos, params=params)
    
    if response.status_code == 200:
        partidos = response.json()["dados"]

        if is_test_mode:
            partidos = partidos[:5]
            print("[MODO TESTE] Limitando a 5 partidos.")

        print(f" Encontrados {len(partidos)} partidos\n")

        start_time = time.time()
        for partido in partidos:
            id_partido_api = partido.get('id')
            
          
            url_detalhe = f"https://dadosabertos.camara.leg.br/api/v2/partidos/{id_partido_api}"
            response_detalhe = http_client.get_safe(url_detalhe)
            
            if response_detalhe.status_code == 200:
                detalhe = response_detalhe.json()["dados"]
                
                sql = """
                    INSERT IGNORE INTO Partido
                    (siglaPartido, uriPartido, nome)
                    VALUES (%s, %s, %s)
                """
                
                valores = (
                    detalhe.get('sigla'),
                    detalhe.get('uri'),
                    detalhe.get('nome')
                )
                
                cursor.execute(sql, valores)
                db.commit()
                total_partidos += 1
                
                sigla = detalhe.get('sigla') or 'N/A'
                nome = detalhe.get('nome') or 'Sem nome'
                print(f"✔ [{total_partidos:2d}] {sigla:15s} - {nome}")
                
                time.sleep(0.1)

                if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                    print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
                    break
            else:
                print(f" Erro ao buscar detalhes do partido ID {id_partido_api}")
        
        print("\n" + "=" * 80)
        print(f" Importação concluída!")
        print(f"   Total de partidos importados: {total_partidos}")
        print("=" * 80)
    else:
        print(f" Erro ao buscar partidos: Status {response.status_code}")

except Exception as e:
    print(f" Erro durante a importação: {e}")

cursor.close()
db.close()
