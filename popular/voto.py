import mysql.connector
import time
import sys
import os
import logging
from dotenv import load_dotenv

from utils.http_client import http_client

# ==========================================
# CONFIGURAÇÃO DE LOGS
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("VotoETL")

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
    logger.info("Conexão estabelecida para Votos da Câmara.")
except mysql.connector.Error as err:
    logger.error(f"Erro de conexão: {err}")
    sys.exit(1)

script_camara = "popular/voto.py#camara_logs_ausencia_justificada"

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    if resultado:
        logger.debug(f"Checkpoint recuperado para {nome_script}: {resultado[0]}")
        return resultado[0]
    return default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = '''
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    '''
    cursor.execute(query, (nome_script, str(valor_parametro)))

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado Federal'")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

def importar_votos_camara():
    logger.info("="*50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTOS DA CÂMARA")
    logger.info("="*50)
    
    cursor.execute('''
        SELECT idApi, idVotacao 
        FROM votacao 
        WHERE casa = 'Camara'
        ORDER BY idVotacao ASC
    ''')
    votacoes = cursor.fetchall()
    checkpoint_atual = int(obter_ultimo_checkpoint(script_camara, default_value="0"))
    total_votos = 0
    start_time = time.time()

    try:
        for index, (id_api_votacao, id_votacao) in enumerate(votacoes):
            if id_votacao <= checkpoint_atual: continue
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: 
                logger.warning("Tempo limite atingido para Votos da Câmara.")
                break
            
            if index % 50 == 0:
                logger.info(f"Processando votação {index}/{len(votacoes)} (ID: {id_votacao})...")

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
                        
                        if voto_txt == "sim": 
                            voto_enum = "SIM"
                        elif voto_txt in ["não", "nao"]: 
                            voto_enum = "NAO"
                        elif "absten" in voto_txt: 
                            voto_enum = "ABSTENCAO"
                        elif any(palavra in voto_txt for palavra in ["justificad", "licença", "missão", "afastament"]):
                            # Cobre casos raros mas possíveis de "Ausência Justificada", "Licença Médica", etc. na Camara
                            voto_enum = "AUSENCIA JUSTIFICADA"
                        elif voto_txt == "ausente" or "ausência" in voto_txt:
                            voto_enum = "AUSENTE"
                        else: 
                            # Cobre coisas como "Obstrução", "Artigo 17", "Branco", Votações Secretas
                            voto_enum = "SEM REGISTRO"

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
                logger.error(f"Erro ao buscar votos da votação {id_votacao}: {e}")
                if db.in_transaction: db.rollback()
                continue
    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário.")
        if db.in_transaction: db.rollback()
        sys.exit(0)
    
    logger.info(f"Concluído: {total_votos} votos da Câmara inseridos no total.")

if __name__ == "__main__":
    importar_votos_camara()
    cursor.close()
    db.close()
