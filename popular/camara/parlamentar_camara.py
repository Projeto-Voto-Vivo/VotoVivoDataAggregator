import os
import time
import logging
import mysql.connector
from urllib.parse import urlparse
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
logger = logging.getLogger("ETL_Camara")

# ---------------------------------------------------------
# 2. CARREGAR VARIÁVEIS DE AMBIENTE
# ---------------------------------------------------------
load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# ---------------------------------------------------------
# 3. FUNÇÕES AUXILIARES
# ---------------------------------------------------------
def conectar_db():
    try:
        conexao = mysql.connector.connect(**DB_CONFIG)
        logger.info("Conexão com o banco de dados estabelecida com sucesso.")
        return conexao
    except mysql.connector.Error as err:
        logger.error(f"Erro crítico de conexão com o banco: {err}")
        exit(1)

def identificar_plataforma(url):
    dominio = urlparse(url).netloc.lower()
    if 'twitter' in dominio or 'x.com' in dominio: return 'Twitter'
    if 'facebook' in dominio: return 'Facebook'
    if 'instagram' in dominio: return 'Instagram'
    if 'youtube' in dominio: return 'YouTube'
    if 'tiktok' in dominio: return 'TikTok'
    if 'linkedin' in dominio: return 'LinkedIn'
    return 'Website'

def processar_gabinete(gabinete_dados):
    if not gabinete_dados:
        return None, None
        
    endereco = []
    if gabinete_dados.get('predio'): endereco.append(f"Prédio {gabinete_dados['predio']}")
    if gabinete_dados.get('andar'): endereco.append(f"Andar {gabinete_dados['andar']}")
    if gabinete_dados.get('sala'): endereco.append(f"Sala {gabinete_dados['sala']}")
    
    str_endereco = ", ".join(endereco) if endereco else None
    telefone = gabinete_dados.get('telefone')
    
    return str_endereco, telefone

# ---------------------------------------------------------
# 4. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def buscar_deputados():
    deputados = []
    pagina = 1
    
    logger.info("Iniciando busca paginada da lista de deputados...")
    while True:
        url = f"{BASE_URL}/deputados?pagina={pagina}&itens=100&ordem=ASC&ordenarPor=nome"
        
        # Utilizando o http_client mantido
        response = http_client.get(url, headers={'accept': 'application/json'})
        
        if response.status_code != 200:
            logger.error(f"Erro na API da Câmara ao buscar página {pagina}: HTTP {response.status_code}")
            break
            
        dados = response.json().get('dados', [])
        if not dados:
            break
            
        deputados.extend(dados)
        logger.debug(f"Página {pagina} extraída ({len(dados)} deputados).")
        pagina += 1
        
    logger.info(f"Busca finalizada. Total de {len(deputados)} deputados mapeados no plenário.")
    return deputados

def buscar_detalhes_deputado(id_api):
    url = f"{BASE_URL}/deputados/{id_api}"
    response = http_client.get(url, headers={'accept': 'application/json'})
    
    if response.status_code == 200:
        return response.json().get('dados', {})
    else:
        logger.warning(f"Falha ao buscar detalhes do deputado {id_api} (HTTP {response.status_code})")
        return None

def processar_camara():
    conexao = conectar_db()
    cursor = conexao.cursor()
    chk_manager = CheckpointManager(conexao)
    nome_script = "parlamentar_camara_v1"
    
    
    deputados_basico = buscar_deputados()
    total = len(deputados_basico)
    
    sql_parlamentar = """
        INSERT INTO parlamentar 
        (idApi, cargo, nomeCivil, nomeUrna, partidoAtual, uf, fotoUrl, dataNascimento, email, telefone, enderecoGabinete)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        cargo=VALUES(cargo), nomeCivil=VALUES(nomeCivil), nomeUrna=VALUES(nomeUrna),
        partidoAtual=VALUES(partidoAtual), uf=VALUES(uf), fotoUrl=VALUES(fotoUrl),
        dataNascimento=VALUES(dataNascimento), email=VALUES(email),
        telefone=VALUES(telefone), enderecoGabinete=VALUES(enderecoGabinete)
    """
    
    sql_get_id = "SELECT idParlamentar FROM parlamentar WHERE idApi = %s"
    sql_delete_redes = "DELETE FROM redeSocial WHERE idParlamentar = %s"
    sql_insert_rede = "INSERT INTO redeSocial (idParlamentar, plataforma, url) VALUES (%s, %s, %s)"

    sucesso_total = True

    for i, dep in enumerate(deputados_basico, 1):
        id_api = str(dep['id'])
        logger.info(f"[{i}/{total}] Processando detalhes: {dep['nome']} ({id_api})")
        
        detalhes = buscar_detalhes_deputado(id_api)
        if not detalhes:
            time.sleep(1)
            sucesso_total = False
            continue
            
        ultimo_status = detalhes.get('ultimoStatus', {})
        gabinete = ultimo_status.get('gabinete', {})
        endereco_gab, telefone_gab = processar_gabinete(gabinete)
        
        email = gabinete.get('email') or dep.get('email')
        data_nasc = detalhes.get('dataNascimento')
        
        try:
            # 1. Inserir Parlamentar
            valores = (
                id_api, 'Deputado(a)', detalhes.get('nomeCivil'), dep.get('nome'),
                dep.get('siglaPartido'), dep.get('siglaUf'), dep.get('urlFoto'),
                data_nasc if data_nasc else None, email, telefone_gab, endereco_gab
            )
            cursor.execute(sql_parlamentar, valores)
            
            # 2. Resgatar ID
            cursor.execute(sql_get_id, (id_api,))
            id_parlamentar = cursor.fetchone()[0]
            
            # 3. Atualizar Redes Sociais
            redes = detalhes.get('redeSocial', [])
            if redes:
                cursor.execute(sql_delete_redes, (id_parlamentar,))
                for url in redes:
                    plataforma = identificar_plataforma(url)
                    cursor.execute(sql_insert_rede, (id_parlamentar, plataforma, url))
            
            conexao.commit()
            
            # Atualiza checkpoint a cada 50 deputados inseridos para manter tracking
            if i % 50 == 0:
                chk_manager.salvar(nome_script, f"PROCESSADO_ATE_INDEX_{i}")
                
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao salvar dados do deputado {id_api} no banco: {e}")
            sucesso_total = False

        time.sleep(0.1) # Respeitar rate limit

    if sucesso_total:
        data_hoje = datetime.now().strftime('%Y-%m-%d')
        chk_manager.salvar(nome_script, f"CONCLUIDO_{data_hoje}")
        logger.info("Sincronização da Câmara finalizada com SUCESSO!")
    else:
        logger.warning("Sincronização finalizada, mas com alguns erros. Verifique os logs.")

    cursor.close()
    conexao.close()

if __name__ == "__main__":
    logger.info("=== INICIANDO SCRIPT DE CARGA: CÂMARA DOS DEPUTADOS ===")
    processar_camara()
