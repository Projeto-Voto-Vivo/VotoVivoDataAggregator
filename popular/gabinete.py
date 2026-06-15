from utils.http_client import http_client
import mysql.connector
import time
import os
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo")
)

cursor = db.cursor()

print("=" * 80)
print(" IMPORTAÇÃO DE GABINETES")
print("=" * 80)

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado Federal'")
deputados_db = cursor.fetchall()

print(f"\n Total de deputados no banco: {len(deputados_db)}")
print(" Buscando gabinetes...\n")

total_atualizados = 0
deputados_processados = 0

start_time = time.time()
for (id_api, id_parlamentar) in deputados_db:
    try:
        url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}"
        response = http_client.get_safe(url, timeout=15)

        if response.status_code == 200:
            dados = response.json()["dados"]
            ultimo_status = dados.get('ultimoStatus', {})
            gabinete = ultimo_status.get('gabinete', {})

            predio = gabinete.get('predio')
            andar  = gabinete.get('andar')
            sala   = gabinete.get('sala')
            nome   = gabinete.get('nome')
            fone   = gabinete.get('telefone')

            if predio or sala or nome:
                partes = []
                if nome:   partes.append(nome)
                if predio: partes.append(f"Prédio {predio}")
                if andar:  partes.append(f"Andar {andar}")
                if sala:   partes.append(f"Sala {sala}")
                endereco = " - ".join(partes)

                cursor.execute(
                    "UPDATE parlamentar SET enderecoGabinete = %s, telefone = %s WHERE idParlamentar = %s",
                    (endereco, fone, id_parlamentar)
                )
                db.commit()
                total_atualizados += 1
                print(f" [{total_atualizados:3d}] {endereco}")

        deputados_processados += 1

        if deputados_processados % 100 == 0:
            print(f"    Progresso: {deputados_processados}/{len(deputados_db)}")

        time.sleep(0.05)

        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
            break

    except Exception as e:
        print(f" Erro ao processar deputado {id_api}: {e}")

print(f"\nConcluído: {total_atualizados} parlamentares com endereço de gabinete atualizado.")
print("\n" + "=" * 80)
cursor.close()
db.close()
