import os
import time
import logging
import mysql.connector
from datetime import datetime
from dotenv import load_dotenv
from utils.http_client import http_client 
from utils.checkpoint_manager import CheckpointManager

# ---------------------------------------------------------
# 1. CONFIGURAÇÃO DE LOGGING
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s', 
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ETL_Votacao_Senado")
load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

BASE_URL = 'https://legis.senado.leg.br/dadosabertos'

# ---------------------------------------------------------
# 2. FUNÇÕES AUXILIARES
# ---------------------------------------------------------
def conectar_db():
    try:
        conexao = mysql.connector.connect(**DB_CONFIG)
        logger.info("Conexão com a base de dados estabelecida com sucesso.")
        return conexao
    except mysql.connector.Error as err:
        logger.error(f"Erro crítico de conexão com a base de dados: {err}")
        exit(1)

def fazer_requisicao_com_retry(url, max_tentativas=3):
    tentativa = 0
    while tentativa < max_tentativas:
        resp = http_client.get(url, headers={'accept': 'application/json'})
        if resp.status_code == 200: return resp
        elif resp.status_code == 404: return None 
        elif resp.status_code in [429, 500, 502, 503, 504]:
            tentativa += 1
            tempo_espera = 3 * tentativa
            logger.warning(f"Aguardando {tempo_espera}s (HTTP {resp.status_code}) para URL: {url}...")
            time.sleep(tempo_espera)
        else: return None
    return None

def mapear_voto_senado(voto_string):
    """Mapeia os votos considerando textos completos e as siglas de painel do Senado (S, N, P)"""
    voto = str(voto_string).upper().strip() if voto_string else ""
    palavras = voto.split()
    
    if "SIM" in voto or "S" in palavras: return "SIM"
    if "NÃO" in voto or "NAO" in voto or "N" in palavras: return "NAO"
    if "ABSTENÇÃO" in voto or "ABSTENCAO" in voto or "P" in palavras: return "ABSTENCAO"
    if "OBSTRUÇÃO" in voto or "OBSTRUCAO" in voto: return "OBSTRUCAO"
    if "ART. 17" in voto or "PRESIDENTE" in voto: return "NAO REGISTRADO"
    return "NAO REGISTRADO"

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def processar_votacoes_presencas_senado():
    conexao = conectar_db()
    cursor = conexao.cursor(dictionary=True)
    chk_manager = CheckpointManager(conexao)
    
    nome_script = "votacao_presenca_senado"
    
    logger.info("A carregar Senadores em atividade para a memória...")
    cursor.execute("SELECT idParlamentar, idApi FROM parlamentar WHERE cargo = 'Senador(a)'")
    map_senadores = {str(row['idApi']): row['idParlamentar'] for row in cursor.fetchall()}
    total_senadores_ativos = len(map_senadores)
    logger.info(f"Total de senadores ativos carregados: {total_senadores_ativos}")
    
    cursor.execute("""
        SELECT p.idProposicao, p.idApi 
        FROM proposicao p
        JOIN tipoProposicao tp ON p.idTipoProposicao = tp.idTipoProposicao
        WHERE tp.casa = 'Senado'
        ORDER BY p.ano DESC, p.idProposicao DESC
    """)
    proposicoes = cursor.fetchall()
    total_props = len(proposicoes)
    
    ultimo_processado = chk_manager.obter(nome_script, "0")
    
    cursor.execute("INSERT IGNORE INTO orgao (idApi, sigla, nome, tipoOrgao, casa) VALUES ('1001', 'PLEN-SF', 'Plenário do Senado Federal', 'Plenário', 'Senado')")
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '1001'")
    id_plenario_senado = cursor.fetchone()['idOrgao']
    conexao.commit()

    logger.info(f"=== INICIANDO ETL DE VOTAÇÕES DO SENADO V4 ({total_props} Proposições) ===")

    for i, prop in enumerate(proposicoes, 1):
        id_proposicao_interno = prop['idProposicao']
        id_materia_api = str(prop['idApi'])
        
        if id_materia_api <= ultimo_processado and ultimo_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_props}] A procurar votações nominais para a Matéria ID {id_materia_api}...")
        resp = fazer_requisicao_com_retry(f"{BASE_URL}/votacao?codigoMateria={id_materia_api}&v=1")
        
        if not resp:
            logger.info("   └─ Nenhuma votação nominal encontrada para esta matéria.")
            continue
            
        lista_votacoes = resp.json()
        if not isinstance(lista_votacoes, list): lista_votacoes = [lista_votacoes]
            
        for votacao in lista_votacoes:
            id_sessao = str(votacao.get('codigoSessaoVotacao', ''))
            if not id_sessao: continue
            
            data_votacao = votacao.get('dataSessao')
            data_hora_formatada = data_votacao + " 00:00:00" if data_votacao else None
            resumo = votacao.get('descricaoVotacao')
            
            logger.info(f"   └─ Encontrada Sessão {id_sessao} ({data_votacao}). A processar dados de plenário e votos...")
            
            try:
                # 1. Evento
                id_evento_api = f"SESSAO_{id_sessao}"
                cursor.execute("""
                    INSERT IGNORE INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo) 
                    VALUES (%s, 'Senado', %s, %s, 'Sessão Deliberativa')
                """, (id_evento_api, id_plenario_senado, data_hora_formatada))
                
                cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s", (id_evento_api,))
                id_evento_interno = cursor.fetchone()['idEvento']

                # 2. Votação
                cursor.execute("""
                    INSERT INTO votacao (idApi, casa, idProposicao, idEvento, idOrgao, dataHora, resumoMateria)
                    VALUES (%s, 'Senado', %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE resumoMateria=VALUES(resumoMateria)
                """, (id_sessao, id_proposicao_interno, id_evento_interno, id_plenario_senado, data_hora_formatada, resumo))
                
                cursor.execute("SELECT idVotacao FROM votacao WHERE idApi = %s AND casa = 'Senado'", (id_sessao,))
                id_votacao_interno = cursor.fetchone()['idVotacao']

                # 3. Votos
                votos_api = votacao.get('votos', [])
                if isinstance(votos_api, dict):
                    votos_api = votos_api.get('votoParlamentar') or votos_api.get('voto') or []
                if not isinstance(votos_api, list):
                    votos_api = [votos_api]
                
                senadores_votaram = set()
                contagem_votos = {'SIM': 0, 'NAO': 0, 'ABSTENCAO': 0, 'OBSTRUCAO': 0, 'NAO REGISTRADO': 0}

                sql_voto = """
                    INSERT INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE votoRegistrado=VALUES(votoRegistrado)
                """
                sql_presenca = "INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca) VALUES (%s, %s, 'PRESENTE')"

                for v in votos_api:
                    if not isinstance(v, dict): continue
                    
                    id_senador_api = str(v.get('codigoParlamentar', ''))
                    if not id_senador_api: continue
                    
                    voto_bruto = ""
                    for key, val in v.items():
                        if isinstance(val, str):
                            chave_limpa = key.lower()
                            if 'nome' not in chave_limpa and 'partido' not in chave_limpa and 'uf' not in chave_limpa:
                                voto_bruto += f" {val} "
                                
                    voto_enum = mapear_voto_senado(voto_bruto)
                    contagem_votos[voto_enum] += 1
                    
                    if id_senador_api in map_senadores:
                        id_parlamentar_interno = map_senadores[id_senador_api]
                        id_voto_api = f"{id_sessao}_{id_senador_api}"
                        
                        cursor.execute(sql_voto, (id_parlamentar_interno, id_votacao_interno, id_voto_api, voto_enum))
                        cursor.execute(sql_presenca, (id_parlamentar_interno, id_evento_interno))
                        senadores_votaram.add(id_senador_api)

                # 4. Faltas (Auditoria)
                sql_falta = "INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca) VALUES (%s, %s, 'AUSENTE')"
                ausentes = 0
                for id_api, id_interno in map_senadores.items():
                    if id_api not in senadores_votaram:
                        cursor.execute(sql_falta, (id_interno, id_evento_interno))
                        ausentes += 1

                conexao.commit()
                logger.info(f"      └─ Sucesso: {len(senadores_votaram)} Votantes | {ausentes} Ausentes.")
                logger.info(f"      └─ Detalhe Votos Extraídos: {contagem_votos['SIM']} SIM | {contagem_votos['NAO']} NÃO | {contagem_votos['ABSTENCAO']} ABS.")
                
            except Exception as e:
                conexao.rollback()
                logger.error(f"Erro ao processar votação {id_sessao}: {e}")
                
        chk_manager.salvar(nome_script, str(id_materia_api))
        time.sleep(0.3)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    logger.info("=== ETL DE VOTAÇÕES E PRESENÇAS DO SENADO FINALIZADO COM SUCESSO ===")
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_votacoes_presencas_senado()
