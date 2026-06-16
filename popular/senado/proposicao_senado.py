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
logger = logging.getLogger("ETL_Proposicao_Senado")

load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

BASE_URL_SENADO = 'https://legis.senado.leg.br/dadosabertos'

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
    url = f"{BASE_URL_SENADO}/processo/siglas"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    mapa_tipos = {}
    
    if resp.status_code == 200:
        dados = resp.json()
        tipos = dados if isinstance(dados, list) else dados.get('Siglas', [])
        
        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Senado')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('descricao') or sigla
            if not sigla: continue
            
            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Senado'", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]
            
        conexao.commit()
        logger.info(f"{len(mapa_tipos)} Tipos de Proposição do Senado sincronizados.")
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL_SENADO}/processo/assuntos"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    mapa_temas = {}
    
    if resp.status_code == 200:
        dados = resp.json()
        temas = dados if isinstance(dados, list) else dados.get('Assuntos', [])
        
        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao) VALUES (%s, 'Senado', %s)
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = t.get('id')
            nome = t.get('assuntoEspecifico') or t.get('assuntoGeral')
            if not cod_externo or not nome: continue
            
            cursor.execute(sql, (int(cod_externo), nome))
            cursor.execute("SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Senado'", (int(cod_externo),))
            mapa_temas[int(cod_externo)] = cursor.fetchone()[0]
            
        conexao.commit()
        logger.info(f"{len(mapa_temas)} Temas (Assuntos) do Senado sincronizados.")
    cursor.close()
    return mapa_temas

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def obter_senadores_ativos(cursor):
    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Senador(a)'")
    return cursor.fetchall()

def buscar_detalhes_processo_senado(id_processo_api):
    url = f"{BASE_URL_SENADO}/processo/{id_processo_api}?v=1"
    resp = http_client.get(url, headers={'accept': 'application/json'})
    return resp.json() if resp.status_code == 200 else None

def processar_proposicoes_senado():
    conexao = conectar_db()
    cursor = conexao.cursor()
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_senado_v1"
    
    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)
    
    senadores = obter_senadores_ativos(cursor)
    total_senadores = len(senadores)
    ultimo_senador_processado = chk_manager.obter(nome_script, "0")
    
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

    for i, (id_parlamentar, id_api_senador, nome_urna) in enumerate(senadores, 1):
        if str(id_api_senador) <= ultimo_senador_processado and ultimo_senador_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_senadores}] Buscando proposições de: {nome_urna}")
        sucesso_senador = True
        
        for ano in anos_mandato:
            url_lista = f"{BASE_URL_SENADO}/processo?ano={ano}&codigoParlamentarAutor={id_api_senador}&v=1"
            
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
                sucesso_senador = False
                continue
                
            dados_lista = resp_lista.json()
            lista_props = dados_lista if isinstance(dados_lista, list) else dados_lista.get('Processos', [])
            
            if not lista_props: 
                continue
                
            for prop_basico in lista_props:
                id_processo_lista = str(prop_basico.get('id'))
                status_atual_lista = prop_basico.get('situacaoAtual')
                
                detalhes = buscar_detalhes_processo_senado(id_processo_lista)
                if not detalhes:
                    time.sleep(0.5)
                    continue
                
                codigo_materia = str(detalhes.get('codigoMateria') or id_processo_lista)
                sigla_tipo = detalhes.get('sigla')
                id_tipo_interno = map_tipos.get(sigla_tipo)
                
                conteudo = detalhes.get('conteudo', {})
                documento = detalhes.get('documento', {})
                
                ementa_final = conteudo.get('ementa') or prop_basico.get('ementa')
                data_apresentacao_raw = documento.get('dataApresentacao') or prop_basico.get('dataApresentacao')
                data_apresentacao = data_apresentacao_raw.replace('T', ' ') if data_apresentacao_raw else None
                
                try:
                    cursor.execute(sql_proposicao, (
                        codigo_materia, id_tipo_interno, detalhes.get('numero'),
                        detalhes.get('ano'), ementa_final, status_atual_lista,
                        data_apresentacao
                    ))
                    
                    cursor.execute(sql_get_prop_id, (codigo_materia,))
                    id_proposicao_interno = cursor.fetchone()[0]
                    
                    cursor.execute(sql_autoria, (id_parlamentar, id_proposicao_interno))
                    
                    assuntos = detalhes.get('assuntos', []) or prop_basico.get('assuntos', [])
                    for a in assuntos:
                        cod_tema_api = a.get('id') or a.get('codigo')
                        if cod_tema_api:
                            id_tema_interno = map_temas.get(int(cod_tema_api))
                            if id_tema_interno:
                                cursor.execute(sql_vincular_tema, (id_proposicao_interno, id_tema_interno))
                    
                    conexao.commit()
                except Exception as e:
                    conexao.rollback()
                    logger.error(f"Erro ao salvar proposição Senado {codigo_materia}: {e}")
                    sucesso_senador = False
                
                time.sleep(0.3)
                
        if sucesso_senador:
            chk_manager.salvar(nome_script, str(id_api_senador))
            time.sleep(1)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_proposicoes_senado()
