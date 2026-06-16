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
logger = logging.getLogger("ETL_Proposicao_Camara")

load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# ---------------------------------------------------------
# 2. FUNÇÕES DE PRÉ-SINCRONIZAÇÃO (REFERÊNCIAS)
# ---------------------------------------------------------
def conectar_db():
    try:
        conexao = mysql.connector.connect(**DB_CONFIG)
        logger.info("Conexão com o banco de dados estabelecida com sucesso.")
        return conexao
    except mysql.connector.Error as err:
        logger.error(f"Erro crítico de conexão com o banco: {err}")
        exit(1)

def sincronizar_tipos_proposicao(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/siglaTipo"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    mapa_tipos = {}
    
    if resp.status_code == 200:
        tipos = resp.json().get('dados', [])
        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Camara')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('nome') or sigla
            if not sigla: continue
            
            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Camara'", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]
            
        conexao.commit()
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/codTema"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    mapa_temas = {}
    
    if resp.status_code == 200:
        temas = resp.json().get('dados', [])
        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao) VALUES (%s, 'Camara', %s)
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = int(t.get('cod'))
            nome = t.get('nome')
            
            cursor.execute(sql, (cod_externo, nome))
            cursor.execute("SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Camara'", (cod_externo,))
            mapa_temas[cod_externo] = cursor.fetchone()[0]
            
        conexao.commit()
    cursor.close()
    return mapa_temas

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def obter_deputados_ativos(cursor):
    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Deputado(a)'")
    return cursor.fetchall()

def buscar_detalhes_proposicao(id_prop_api):
    url = f"{BASE_URL}/proposicoes/{id_prop_api}"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    return resp.json().get('dados', {}) if resp.status_code == 200 else None

def buscar_temas_da_proposicao(id_prop_api):
    url = f"{BASE_URL}/proposicoes/{id_prop_api}/temas"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    return resp.json().get('dados', []) if resp.status_code == 200 else []

def processar_proposicoes_camara():
    conexao = conectar_db()
    cursor = conexao.cursor()
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_camara_v2"
    
    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)
    
    deputados = obter_deputados_ativos(cursor)
    total_deputados = len(deputados)
    ultimo_deputado_processado = chk_manager.obter(nome_script, "0")
    
    sql_proposicao = """
        INSERT INTO proposicao (idApi, idTipoProposicao, numero, ano, ementa, statusAtual, dataApresentacao)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        idTipoProposicao=VALUES(idTipoProposicao), numero=VALUES(numero), 
        ano=VALUES(ano), ementa=VALUES(ementa), statusAtual=VALUES(statusAtual),
        dataApresentacao=VALUES(dataApresentacao)
    """
    sql_get_prop_id = "SELECT idProposicao FROM proposicao WHERE idApi = %s"
    sql_autoria = "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)"
    sql_vincular_tema = "INSERT IGNORE INTO temaProposicao (idProposicao, idTema) VALUES (%s, %s)"

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year
    anos_mandato = list(range(ano_inicio, ano_atual + 1)) 

    for i, (id_parlamentar, id_api_deputado, nome_urna) in enumerate(deputados, 1):
        if str(id_api_deputado) <= ultimo_deputado_processado and ultimo_deputado_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_deputados}] Buscando proposições de: {nome_urna}")
        sucesso_deputado = True
        
        for ano in anos_mandato:
            pagina = 1
            
            while True:
                url_lista = f"{BASE_URL}/proposicoes?ano={ano}&idDeputadoAutor={id_api_deputado}&pagina={pagina}&itens=100&ordem=ASC"
                
                max_tentativas = 3
                tentativa_atual = 0
                sucesso_requisicao = False
                resp_lista = None

                while tentativa_atual < max_tentativas:
                    resp_lista = http_client.get(url_lista, headers={'accept': 'application/json'})
                    
                    if resp_lista.status_code == 200:
                        sucesso_requisicao = True
                        break
                    elif resp_lista.status_code in [429, 500, 502, 503, 504]:
                        tentativa_atual += 1
                        tempo_espera = 5 * tentativa_atual
                        logger.warning(f"Aguardando {tempo_espera}s devido ao status HTTP {resp_lista.status_code}... (Tentativa {tentativa_atual}/{max_tentativas})")
                        time.sleep(tempo_espera)
                    else:
                        logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                        break
                
                if not sucesso_requisicao:
                    sucesso_deputado = False
                    break
                    
                lista_props = resp_lista.json().get('dados', [])
                if not lista_props: 
                    break
                    
                for prop_basico in lista_props:
                    id_prop_api = str(prop_basico['id'])
                    
                    detalhes = buscar_detalhes_proposicao(id_prop_api)
                    if not detalhes:
                        time.sleep(0.5)
                        continue
                    
                    sigla_tipo = detalhes.get('siglaTipo')
                    id_tipo_interno = map_tipos.get(sigla_tipo)
                    status_node = detalhes.get('statusProposicao') or {}
                    status_atual = status_node.get('descricaoSituacao')
                    
                    data_apresentacao_raw = detalhes.get('dataApresentacao')
                    data_apresentacao = data_apresentacao_raw.replace('T', ' ') if data_apresentacao_raw else None
                    
                    try:
                        cursor.execute(sql_proposicao, (
                            id_prop_api, id_tipo_interno, detalhes.get('numero'),
                            detalhes.get('ano'), detalhes.get('ementa'), status_atual,
                            data_apresentacao
                        ))
                        
                        cursor.execute(sql_get_prop_id, (id_prop_api,))
                        id_proposicao_interno = cursor.fetchone()[0]
                        
                        cursor.execute(sql_autoria, (id_parlamentar, id_proposicao_interno))
                        
                        temas_da_prop = buscar_temas_da_proposicao(id_prop_api)
                        for t in temas_da_prop:
                            cod_tema_api = t.get('codTema')
                            id_tema_interno = map_temas.get(cod_tema_api)
                            
                            if id_tema_interno:
                                cursor.execute(sql_vincular_tema, (id_proposicao_interno, id_tema_interno))
                        
                        conexao.commit()
                    except Exception as e:
                        conexao.rollback()
                        logger.error(f"Erro ao salvar proposição {id_prop_api}: {e}")
                        sucesso_deputado = False
                    
                    time.sleep(0.3)
                    
                pagina += 1
                
        if sucesso_deputado:
            chk_manager.salvar(nome_script, str(id_api_deputado))
            time.sleep(1)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_proposicoes_camara()
