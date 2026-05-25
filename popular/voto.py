import requests
import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "test"),
        password=os.getenv("DB_PASSWORD", "testpass"),
        database=os.getenv("DB_NAME", "votovivo")
    )
    cursor = db.cursor()
    print("Conexão estabelecida para Votos.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

def importar_votos_camara():
    print("\n--- IMPORTANDO VOTOS DA CÂMARA ---")
    
    cursor.execute("""
        SELECT idApi, idVotacao 
        FROM votacao 
        WHERE casa = 'Camara' AND tipoVotacao = 'NOMINAL'
    """)
    votacoes = cursor.fetchall()
    
    if is_test_mode:
        votacoes = votacoes[:5]
        print("[MODO TESTE] Limitando a 5 votações nominais da Câmara.")

    total_votos = 0
    start_time = time.time()

    for id_api_votacao, id_votacao in tqdm(votacoes, desc="Votos Câmara"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Câmara interrompida após {tempo_limite_segundos}s.")
            break

        try:
            res = requests.get(f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{id_api_votacao}/votos", timeout=30)
            if res.status_code != 200: continue
            
            dados = res.json().get("dados", [])
            if not dados: continue

            batch = []
            for v in dados:
                id_dep_api = str(v.get("deputado_", {}).get("id"))
                if id_dep_api in map_parlamentares:
                    voto_txt = v.get("tipoVoto", "").strip().lower()

                    if voto_txt == "sim": voto_enum = "SIM"
                    elif voto_txt == "não" or voto_txt == "nao": voto_enum = "NAO"
                    elif "absten" in voto_txt: voto_enum = "ABSTENCAO"
                    else: continue

                    # Cria um idApi único para o voto: votacaoId_deputadoId
                    id_api_voto = f"{id_api_votacao}_{id_dep_api}"

                    batch.append((map_parlamentares[id_dep_api], id_votacao, id_api_voto, voto_enum))

            if batch:
                cursor.executemany("""
                    INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                    VALUES (%s, %s, %s, %s)
                """, batch)
                db.commit()
                total_votos += len(batch)
            time.sleep(0.1)

        except Exception as e:
            continue
            
    print(f"Votos da Câmara inseridos: {total_votos}")

def importar_votos_senado():
    print("\n--- IMPORTANDO VOTOS DO SENADO ---")
    
    cursor.execute("""
        SELECT v.idApi, v.idVotacao, p.idApi
        FROM votacao v
        JOIN proposicao p ON v.idProposicao = p.idProposicao
        WHERE v.casa = 'Senado' AND v.tipoVotacao = 'NOMINAL'
    """)
    votacoes_senado = cursor.fetchall()

    if is_test_mode:
        votacoes_senado = votacoes_senado[:5]
        print("[MODO TESTE] Limitando a 5 votações nominais do Senado.")

    total_votos = 0
    start_time = time.time()
    headers = {"Accept": "application/json"}

    materias = {}
    for id_api_votacao, id_votacao, id_api_materia in votacoes_senado:
        if id_api_materia not in materias:
            materias[id_api_materia] = []
        materias[id_api_materia].append((id_api_votacao, id_votacao))

    for id_api_materia, lista_votacoes_banco in tqdm(materias.items(), desc="Votos Senado"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Senado interrompido após {tempo_limite_segundos}s.")
            break

        try:
            url = f"https://legis.senado.leg.br/dadosabertos/materia/{id_api_materia}/votacoes"
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code != 200: continue
            
            dados = res.json()
            votacoes_json = dados.get("VotacaoMateria", {}).get("Materia", {}).get("Votacoes", {}).get("Votacao", [])
            
            if isinstance(votacoes_json, dict): votacoes_json = [votacoes_json]

            batch = []
            
            for v_api in votacoes_json:
                id_sessao = v_api.get("CodigoSessao", "")
                id_votacao_sessao = v_api.get("CodigoSessaoVotacao", "")
                id_api_montado = f"SEN_{id_api_materia}_{id_sessao}_{id_votacao_sessao}"
                
                votacao_db = next((v for v in lista_votacoes_banco if v[0] == id_api_montado), None)
                
                if votacao_db:
                    _, id_votacao_interno = votacao_db
                    votos_api = v_api.get("Votos", {}).get("VotoParlamentar", [])
                    if isinstance(votos_api, dict): votos_api = [votos_api]
                    
                    for voto_senador in votos_api:
                        id_sen_api = str(voto_senador.get("CodigoParlamentar"))
                        
                        if id_sen_api in map_parlamentares:
                            voto_txt = voto_senador.get("Voto", "").strip().lower()
                            
                            if voto_txt == "sim": voto_enum = "SIM"
                            elif voto_txt == "não" or voto_txt == "nao": voto_enum = "NAO"
                            elif "absten" in voto_txt: voto_enum = "ABSTENCAO"
                            else: continue
                            
                            id_api_voto = f"{id_api_montado}_{id_sen_api}"
                            batch.append((map_parlamentares[id_sen_api], id_votacao_interno, id_api_voto, voto_enum))

            if batch:
                cursor.executemany("""
                    INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                    VALUES (%s, %s, %s, %s)
                """, batch)
                db.commit()
                total_votos += len(batch)
            
            time.sleep(0.3)
            
        except Exception as e:
            continue

    print(f"Votos do Senado inseridos: {total_votos}")

if __name__ == "__main__":
    importar_votos_camara()
    importar_votos_senado()
    
    cursor.close()
    db.close()
    print("\nImportação de Votos (Câmara e Senado) finalizada.")
