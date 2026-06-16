import os
import time
import logging
import mysql.connector
import xml.etree.ElementTree as ET
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
logger = logging.getLogger("ETL_Orgao_Senado")

load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

BASE_URL_SENADO = 'https://legis.senado.leg.br/dadosabertos'

# ---------------------------------------------------------
# 2. FUNÇÕES DE BANCO E AUXILIARES
# ---------------------------------------------------------
def conectar_db():
    try:
        conexao = mysql.connector.connect(**DB_CONFIG)
        logger.info("Conexão com o banco de dados estabelecida com sucesso.")
        return conexao
    except mysql.connector.Error as err:
        logger.error(f"Erro crítico de conexão com o banco: {err}")
        exit(1)

def obter_senadores_ativos(cursor):
    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Senador(a)'")
    return cursor.fetchall()

def carregar_cache_orgaos(cursor):
    """Carrega os órgãos já existentes no banco para a memória, evitando INSERTs redundantes"""
    cursor.execute("SELECT idApi, idOrgao FROM orgao WHERE casa IN ('Senado', 'Congresso')")
    return {str(row[0]): row[1] for row in cursor.fetchall()}

def get_xml_text(element, tag_name, default=None):
    """Busca recursivamente uma tag no XML e retorna o seu texto."""
    if element is None: return default
    node = element.find(f".//{tag_name}")
    return node.text if node is not None and node.text else default

def mapear_casa(sigla_casa_xml):
    if sigla_casa_xml == 'CN':
        return 'Congresso'
    elif sigla_casa_xml == 'CD':
        return 'Camara'
    return 'Senado'

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def processar_orgaos_senado():
    conexao = conectar_db()
    cursor = conexao.cursor()
    chk_manager = CheckpointManager(conexao)
    nome_script = "orgao_senado_v1"
    
    senadores = obter_senadores_ativos(cursor)
    map_orgaos = carregar_cache_orgaos(cursor)
    
    total_senadores = len(senadores)
    ultimo_senador_processado = chk_manager.obter(nome_script, "0")
    
    sql_orgao = """
        INSERT INTO orgao (idApi, sigla, nome, casa)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE sigla=VALUES(sigla), nome=VALUES(nome), casa=VALUES(casa)
    """
    sql_get_orgao_id = "SELECT idOrgao FROM orgao WHERE idApi = %s AND casa IN ('Senado', 'Congresso')"
    sql_membro = "INSERT IGNORE INTO membroOrgao (idParlamentar, idOrgao, cargo) VALUES (%s, %s, %s)"

    for i, (id_parlamentar, id_api_senador, nome_urna) in enumerate(senadores, 1):
        if str(id_api_senador) <= ultimo_senador_processado and ultimo_senador_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_senadores}] Buscando órgãos/comissões de: {nome_urna}")
        sucesso_senador = True
        
        # Diferente da Câmara, o Senado retorna tudo numa chamada só (não precisa paginar)
        url_lista = f"{BASE_URL_SENADO}/senador/{id_api_senador}/comissoes?v=5"
        
        max_tentativas = 3
        tentativa_atual = 0
        sucesso_requisicao = False
        resp_lista = None

        while tentativa_atual < max_tentativas:
            resp_lista = http_client.get(url_lista, headers={'accept': 'application/xml'})
            
            if resp_lista.status_code == 200:
                sucesso_requisicao = True
                break
            elif resp_lista.status_code in [429, 500, 502, 503, 504]:
                tentativa_atual += 1
                tempo_espera = 3 * tentativa_atual
                logger.warning(f"Rate Limit na API (HTTP {resp_lista.status_code}). Aguardando {tempo_espera}s... (Tentativa {tentativa_atual}/{max_tentativas})")
                time.sleep(tempo_espera)
            else:
                logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                break
        
        if not sucesso_requisicao:
            sucesso_senador = False
            continue
            
        try:
            root = ET.fromstring(resp_lista.content)
            comissoes = root.findall('.//Comissao')
            
            for comissao_xml in comissoes:
                id_orgao_api = get_xml_text(comissao_xml, 'CodigoComissao')
                sigla_orgao = get_xml_text(comissao_xml, 'SiglaComissao')
                nome_orgao = get_xml_text(comissao_xml, 'NomeComissao')
                sigla_casa = get_xml_text(comissao_xml, 'SiglaCasaComissao')
                cargo_parlamentar = get_xml_text(comissao_xml, 'DescricaoParticipacao')
                data_fim = get_xml_text(comissao_xml, 'DataFim')
                
                # Ignorar se os dados base não existirem
                if not id_orgao_api or not cargo_parlamentar:
                    continue
                
                # Filtrar para manter apenas órgãos ativos no mandato atual (pós-2023)
                if data_fim and data_fim < "2023-01-01":
                    continue

                casa_db = mapear_casa(sigla_casa)

                try:
                    # 1. Inserir ou recuperar Órgão (Graças à riqueza do XML do Senado, não precisamos de ir buscar os detalhes)
                    if id_orgao_api not in map_orgaos:
                        cursor.execute(sql_orgao, (id_orgao_api, sigla_orgao, nome_orgao, casa_db))
                        
                        cursor.execute(sql_get_orgao_id, (id_orgao_api,))
                        resultado_id = cursor.fetchone()
                        if resultado_id:
                            map_orgaos[id_orgao_api] = resultado_id[0]
                    
                    id_orgao_interno = map_orgaos.get(id_orgao_api)
                    
                    # 2. Inserir a relação de membresia (Parlamentar <-> Órgão)
                    if id_orgao_interno:
                        cursor.execute(sql_membro, (id_parlamentar, id_orgao_interno, cargo_parlamentar))
                    
                    conexao.commit()
                except Exception as e:
                    conexao.rollback()
                    logger.error(f"Erro ao salvar órgão {id_orgao_api} para o senador {id_api_senador}: {e}")
                    sucesso_senador = False
                    
        except ET.ParseError as e:
            logger.error(f"Erro ao fazer o parse do XML para o senador {id_api_senador}: {e}")
            sucesso_senador = False
            
        if sucesso_senador:
            chk_manager.salvar(nome_script, str(id_api_senador))
            time.sleep(0.3)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    logger.info("=== Sincronização de Órgãos e Membros do Senado FINALIZADA ===")
    
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_orgaos_senado()
