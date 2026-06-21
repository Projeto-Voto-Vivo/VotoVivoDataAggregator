import os
import time
import logging
import mysql.connector
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from utils.http_client import http_client 
from utils.checkpoint_manager import CheckpointManager

# ---------------------------------------------------------
# 1. CONFIGURAÇÃO DE LOGGING
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger("ETL_Evento_Presenca_Camara")
load_dotenv()

DB_CONFIG = {
    'host': os.getenv("DB_HOST", "localhost"),
    'user': os.getenv("DB_USER", "root"),
    'password': os.getenv("DB_PASSWORD", ""),
    'database': os.getenv("DB_NAME", "votovivo")
}

def conectar_db():
    return mysql.connector.connect(**DB_CONFIG)

def mapear_status(status_texto):
    texto = status_texto.lower().strip()
    if texto == 'presença': return 'PRESENTE'
    if texto == 'ausência' or texto == 'ausência não justificada': return 'AUSENTE'
    if not texto or texto == '-': return 'NAO REGISTRADO'
    return 'JUSTIFICADA'

def formatar_data(data_str):
    try:
        return datetime.strptime(data_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d 00:00:00")
    except:
        return None

def limpar_string_para_id(texto):
    return "".join(c if c.isalnum() else "_" for c in texto).strip("_").upper()

# ---------------------------------------------------------
# 2. LÓGICA DE EXTRAÇÃO UNIFICADA (SCRAPING)
# ---------------------------------------------------------
def processar_eventos_presencas_camara():
    conexao = conectar_db()
    cursor = conexao.cursor(dictionary=True)
    chk_manager = CheckpointManager(conexao)
    
    nome_script = "evento_presenca_camara"
    
    cursor.execute("INSERT IGNORE INTO orgao (idApi, sigla, nome, tipoOrgao, casa) VALUES ('114', 'PLEN', 'Plenário da Câmara dos Deputados', 'Plenário', 'Camara')")
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '114'")
    id_plenario_camara = cursor.fetchone()['idOrgao']

    cursor.execute("INSERT IGNORE INTO orgao (idApi, sigla, nome, tipoOrgao, casa) VALUES ('111', 'CN', 'Congresso Nacional', 'Plenário', 'Congresso')")
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '111'")
    id_plenario_congresso = cursor.fetchone()['idOrgao']
    
    cursor.execute("SELECT idOrgao, nome FROM orgao WHERE casa = 'Camara'")
    map_orgaos = {row['nome'].lower().strip(): row['idOrgao'] for row in cursor.fetchall() if row['nome']}
    conexao.commit()

    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Deputado(a)'")
    deputados = cursor.fetchall()
    total_deps = len(deputados)
    ultimo_processado = chk_manager.obter(nome_script, "0")
    
    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year
    anos_mandato = list(range(ano_inicio, ano_atual + 1))

    sql_evento = """
        INSERT INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE descricaoTipo=VALUES(descricaoTipo), casa=VALUES(casa), idOrgao=VALUES(idOrgao)
    """
    sql_presenca = """
        INSERT INTO presenca (idParlamentar, idEvento, statusPresenca) 
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE statusPresenca=VALUES(statusPresenca)
    """

    logger.info("=== INICIANDO ETL UNIFICADO DE EVENTOS E PRESENÇAS (V5 - LÓGICA INDESTRUTÍVEL) ===")

    for i, dep in enumerate(deputados, 1):
        id_parlamentar = dep['idParlamentar']
        id_api = str(dep['idApi'])
        nome_urna = dep['nomeUrna']
        
        if id_api <= ultimo_processado and ultimo_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_deps}] A processar: {nome_urna}...")
        
        for ano in anos_mandato:
            resp_plen = http_client.get(f"https://www.camara.leg.br/deputados/{id_api}/presenca-plenario/{ano}")
            if resp_plen.status_code == 200:
                soup = BeautifulSoup(resp_plen.text, 'html.parser')
                linhas_sessao = soup.find_all('tr', class_='info-data__child')
                
                eventos_plenario = 0
                for linha in linhas_sessao:
                    celulas = linha.find_all('td')
                    if len(celulas) >= 2:
                        sessao_texto = celulas[0].text.strip()
                        status_texto = celulas[1].text.strip()
                        
                        if "CONJUNTA" in sessao_texto.upper():
                            orgao_evento, casa_evento, desc_tipo = id_plenario_congresso, "Congresso", "Sessão Conjunta"
                        else:
                            orgao_evento, casa_evento, desc_tipo = id_plenario_camara, "Camara", "Sessão Deliberativa"
                        
                        data_str = sessao_texto.split('-')[-1].strip() if '-' in sessao_texto else f"01/01/{ano}"
                        data_formatada = formatar_data(data_str)
                        id_evento_sintetico = f"PLEN_{limpar_string_para_id(sessao_texto)}"
                        status_enum = mapear_status(status_texto)
                        
                        if status_enum == 'NAO REGISTRADO': continue
                        
                        try:
                            cursor.execute(sql_evento, (id_evento_sintetico, casa_evento, orgao_evento, data_formatada, desc_tipo))
                            cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s", (id_evento_sintetico,))
                            id_evento_interno = cursor.fetchone()['idEvento']
                            cursor.execute(sql_presenca, (id_parlamentar, id_evento_interno, status_enum))
                            eventos_plenario += 1
                        except Exception as e:
                            logger.error(f"Erro no Plenário: {e}")
                if eventos_plenario > 0: logger.debug(f"   └─ {eventos_plenario} Plenário/Congresso em {ano}.")
                            
            time.sleep(0.3)

            resp_com = http_client.get(f"https://www.camara.leg.br/deputados/{id_api}/presenca-comissoes/{ano}")
            if resp_com.status_code == 200:
                soup = BeautifulSoup(resp_com.text, 'html.parser')
                linhas = soup.find_all('tr')
                
                eventos_comissao = 0
                for linha in linhas:
                    celulas = linha.find_all('td')
                    if len(celulas) >= 4:
                        links_eventos = celulas[2].find_all('a', href=True)
                        if not links_eventos: continue
                        
                        if 'evento-legislativo' not in links_eventos[0].get('href', ''):
                            continue
                            
                        data_str = celulas[0].text.strip()
                        data_formatada = formatar_data(data_str)
                        
                        orgao_textos = [t.strip() for t in celulas[1].strings if t.strip()]
                        status_textos = [t.strip() for t in celulas[3].strings if t.strip()]
                        
                        for idx, tag_a in enumerate(links_eventos):
                            try:
                                href = tag_a.get('href', '')
                                id_evento_api = href.split('/')[-1]
                                
                                id_evento_api = id_evento_api.split('?')[0]
                                if not id_evento_api.isdigit(): continue
                                    
                                tipo_reuniao = tag_a.text.strip()
                                status_texto = status_textos[idx] if idx < len(status_textos) else status_textos[-1]
                                status_enum = mapear_status(status_texto)
                                
                                if status_enum == 'NAO REGISTRADO': continue
                                
                                id_orgao_interno = None
                                if idx < len(orgao_textos):
                                    nome_limpo_comissao = orgao_textos[idx].split('-', 1)[-1].strip().lower()
                                    id_orgao_interno = map_orgaos.get(nome_limpo_comissao, None)
                                
                                cursor.execute(sql_evento, (id_evento_api, 'Camara', id_orgao_interno, data_formatada, tipo_reuniao))
                                cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s", (id_evento_api,))
                                id_evento_interno = cursor.fetchone()['idEvento']
                                
                                cursor.execute(sql_presenca, (id_parlamentar, id_evento_interno, status_enum))
                                eventos_comissao += 1
                                
                            except mysql.connector.Error as db_err:
                                logger.error(f"Erro no BD (Comissão {id_evento_api}): {db_err}. Verifique se idOrgao permite NULL!")
                            except Exception as e:
                                logger.error(f"Erro de Parsing (Comissão {data_str}): {e}")
                                
                if eventos_comissao > 0: logger.info(f"   └─ Inseridas {eventos_comissao} Presenças de Comissões em {ano}.")
            
            conexao.commit()
            time.sleep(0.3)

        chk_manager.salvar(nome_script, id_api)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_eventos_presencas_camara()
