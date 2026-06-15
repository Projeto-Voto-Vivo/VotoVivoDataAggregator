import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

from utils.http_client import http_client

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
    print("Conexão estabelecida para Votos.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)

script_camara = "popular/voto.py#camara_dinamico_v2"
script_senado = "popular/voto.py#senado_dinamico_v2"

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = '''
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    '''
    cursor.execute(query, (nome_script, str(valor_parametro)))

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

def importar_votos_camara():
    print("\n--- IMPORTANDO VOTOS DA CÂMARA ---")
    cursor.execute('''
        SELECT idApi, idVotacao 
        FROM votacao 
        WHERE casa = 'Camara' AND tipoVotacao = 'NOMINAL'
        ORDER BY idVotacao ASC
    ''')
    votacoes = cursor.fetchall()
    checkpoint_atual = int(obter_ultimo_checkpoint(script_camara, default_value="0"))
    total_votos = 0
    start_time = time.time()

    try:
        for id_api_votacao, id_votacao in tqdm(votacoes, desc="Votos Câmara"):
            if id_votacao <= checkpoint_atual: continue
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                break

            try:
                res = http_client.get_safe(f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{id_api_votacao}/votos", timeout=30)
                if res.status_code != 200: continue
                
                dados = res.json().get("dados", [])
                
                if db.in_transaction: db.commit()
                db.start_transaction()

                if not dados: 
                    salvar_checkpoint_transacao(script_camara, id_votacao)
                    db.commit()
                    continue

                batch = []
                for v in dados:
                    id_dep_api = str(v.get("deputado_", {}).get("id"))
                    if id_dep_api in map_parlamentares:
                        voto_txt = v.get("tipoVoto", "").strip().lower()
                        if voto_txt == "sim": voto_enum = "SIM"
                        elif voto_txt == "não" or voto_txt == "nao": voto_enum = "NAO"
                        elif "absten" in voto_txt: voto_enum = "ABSTENCAO"
                        else: continue

                        id_api_voto = f"{id_api_votacao}_{id_dep_api}"
                        batch.append((map_parlamentares[id_dep_api], id_votacao, id_api_voto, voto_enum))

                if batch:
                    cursor.executemany('''
                        INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                        VALUES (%s, %s, %s, %s)
                    ''', batch)
                    total_votos += len(batch)

                salvar_checkpoint_transacao(script_camara, id_votacao)
                db.commit()
                time.sleep(0.1)
            except Exception as e:
                if db.in_transaction: db.rollback()
                continue
    except KeyboardInterrupt:
        if db.in_transaction: db.rollback()
        cursor.close()
        db.close()
        sys.exit(0)
    print(f"Votos da Câmara inseridos: {total_votos}")

def importar_votos_senado():
    print("\n--- IMPORTANDO VOTOS DO SENADO ---")
    cursor.execute('''
        SELECT v.idApi, v.idVotacao, p.idApi
        FROM votacao v
        JOIN proposicao p ON v.idProposicao = p.idProposicao
        WHERE v.casa = 'Senado' AND v.tipoVotacao = 'NOMINAL'
        ORDER BY v.idVotacao ASC
    ''')
    votacoes_senado = cursor.fetchall()
    checkpoint_atual = int(obter_ultimo_checkpoint(script_senado, default_value="0"))
    total_votos = 0
    start_time = time.time()
    headers = {"Accept": "application/json"}

    materias = {}
    for id_api_votacao, id_votacao, id_api_materia in votacoes_senado:
        if id_votacao <= checkpoint_atual: continue
        if id_api_materia not in materias: materias[id_api_materia] = []
        materias[id_api_materia].append((id_api_votacao, id_votacao))

    try:
        for id_api_materia, lista_votacoes_banco in tqdm(materias.items(), desc="Votos Senado"):
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: break

            try:
                url = f"https://legis.senado.leg.br/dadosabertos/materia/{id_api_materia}/votacoes"
                res = http_client.get_safe(url, headers=headers, timeout=15)
                
                if db.in_transaction: db.commit()
                db.start_transaction()

                if res.status_code != 200:
                    for _, id_votacao in lista_votacoes_banco:
                        salvar_checkpoint_transacao(script_senado, id_votacao)
                    db.commit()
                    continue
                
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
                    cursor.executemany('''
                        INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                        VALUES (%s, %s, %s, %s)
                    ''', batch)
                    total_votos += len(batch)
                
                for _, id_votacao in lista_votacoes_banco:
                    salvar_checkpoint_transacao(script_senado, id_votacao)
                db.commit()
                time.sleep(0.3)
                
            except Exception:
                if db.in_transaction: db.rollback()
                continue

    except KeyboardInterrupt:
        if db.in_transaction: db.rollback()
        cursor.close()
        db.close()
        sys.exit(0)

    print(f"Votos do Senado inseridos: {total_votos}")

if __name__ == "__main__":
    try:
        importar_votos_camara()
        importar_votos_senado()
    except KeyboardInterrupt: pass
    finally:
        cursor.close()
        db.close()
        print("\nImportação de Votos (Câmara e Senado) finalizada.")
