import os
import requests
import mysql.connector
import time
import sys
from datetime import datetime
from bs4 import BeautifulSoup
from tqdm import tqdm
from dotenv import load_dotenv

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
except mysql.connector.Error:
    sys.exit(1)

chk_camara = "popular/presenca.py#camara"
chk_senado = "popular/presenca.py#senado"

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = """
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    """
    cursor.execute(query, (nome_script, str(valor_parametro)))

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado Federal' ORDER BY idParlamentar ASC")
deputados_banco = cursor.fetchall()
map_deputados = {str(row[0]): row[1] for row in deputados_banco}

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Senador'")
map_senadores = {str(row[0]): row[1] for row in cursor.fetchall()}

def buscar_justificativa_camara(id_api_deputado, data_str):
    if not data_str: return None
    ano = data_str[:4]
    data_formatada = f"{data_str[8:10]}/{data_str[5:7]}/{ano}"
    url = f"https://www.camara.leg.br/deputados/{id_api_deputado}/presenca-plenario/{ano}"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200: return None
            
        soup = BeautifulSoup(res.text, 'lxml')
        tabelas = soup.find_all('table')
        
        for tabela in tabelas:
            linhas = tabela.find_all('tr')
            for linha in linhas:
                colunas = linha.find_all('td')
                if len(colunas) >= 3:
                    data_tabela = colunas[0].text.strip()
                    if data_formatada in data_tabela:
                        texto_presenca = colunas[2].text.strip().lower()
                        if "licença" in texto_presenca or "missão" in texto_presenca or "justificada" in texto_presenca:
                            return colunas[2].text.strip()[:255]
        return None
    except Exception:
        return None

def registrar_evento(id_api, casa, data_hora, descricao_tipo, id_orgao=None):
    cursor.execute("""
        INSERT INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE descricaoTipo = VALUES(descricaoTipo)
    """, (str(id_api), casa, id_orgao, data_hora, descricao_tipo))
    db.commit()
    
    cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s AND casa = %s", (str(id_api), casa))
    return cursor.fetchone()[0]

def importar_presencas_camara():
    if not deputados_banco: return

    checkpoint_atual = int(obter_ultimo_checkpoint(chk_camara, default_value="0"))
    fila_deputados = [d for d in deputados_banco if d[1] > checkpoint_atual]

    start_time = time.time()
    
    for id_api_dep, id_interno_dep in tqdm(fila_deputados, desc="Deputados (Câmara)"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break
            
        url_eventos = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api_dep}/eventos"
        params = {"dataInicio": "2025-08-01", "dataFim": "2025-08-31", "ordem": "ASC"}
        
        try:
            res_eventos = requests.get(url_eventos, params=params, timeout=15)
            if res_eventos.status_code != 200: 
                salvar_checkpoint_transacao(chk_camara, id_interno_dep)
                db.commit()
                continue
            
            eventos = res_eventos.json().get("dados", [])
            
            for ev in eventos:
                id_evento_api = str(ev.get("id"))
                tipo_evento = ev.get("descricaoTipo", "")
                data_hora = ev.get("dataHoraInicio")
                
                if "Deliberativa" not in tipo_evento:
                    continue
                    
                id_evento_interno = registrar_evento(id_evento_api, 'Camara', data_hora, tipo_evento)
                
                url_presenca = f"https://dadosabertos.camara.leg.br/api/v2/eventos/{id_evento_api}/deputados"
                res_pres = requests.get(url_presenca, timeout=15)
                
                status_presenca = 'AUSENTE'
                justificativa = None
                
                if res_pres.status_code == 200:
                    presentes = res_pres.json().get("dados", [])
                    if any(str(p.get("id")) == id_api_dep for p in presentes):
                        status_presenca = 'PRESENTE'
                
                if status_presenca == 'AUSENTE':
                    data_str = data_hora.split('T')[0] if data_hora else None
                    motivo = buscar_justificativa_camara(id_api_dep, data_str)
                    if motivo:
                        status_presenca = 'JUSTIFICADA'
                        justificativa = motivo
                
                cursor.execute("""
                    INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca, justificativa)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE statusPresenca = VALUES(statusPresenca), justificativa = VALUES(justificativa)
                """, (id_interno_dep, id_evento_interno, status_presenca, justificativa))
            
            salvar_checkpoint_transacao(chk_camara, id_interno_dep)
            db.commit()
            time.sleep(0.2)
            
        except Exception:
            db.rollback()
            continue

def importar_presencas_senado():
    cursor.execute("SELECT idOrgao, idApi, sigla FROM orgao WHERE (casa = 'Senado' OR casa = 'Congresso') ORDER BY idOrgao ASC")
    comissoes = cursor.fetchall()
    
    if not comissoes: return
    
    checkpoint_atual = int(obter_ultimo_checkpoint(chk_senado, default_value="0"))
    fila_comissoes = [c for c in comissoes if c[0] > checkpoint_atual]

    start_time = time.time()
    headers = {"Accept": "application/json"}
    
    for id_orgao, id_api_orgao, sigla in tqdm(fila_comissoes, desc="Comissões (Senado)"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break
            
        if not sigla or sigla == 'N/A': 
            salvar_checkpoint_transacao(chk_senado, id_orgao)
            db.commit()
            continue
            
        url_reunioes = f"https://legis.senado.leg.br/dadosabertos/comissao/{sigla}/reunioes"
        params = {"dataInicio": "20250801", "dataFim": "20250831"}
        
        try:
            res = requests.get(url_reunioes, headers=headers, params=params, timeout=15)
            if res.status_code != 200:
                salvar_checkpoint_transacao(chk_senado, id_orgao)
                db.commit()
                continue
            
            dados = res.json()
            reunioes = dados.get("ReunioesComissao", {}).get("Reunioes", {}).get("Reuniao", [])
            if isinstance(reunioes, dict): reunioes = [reunioes]
            
            for r in reunioes:
                id_evento_api = f"SEN_REU_{r.get('Codigo')}"
                data_hora = r.get("Data") + " 00:00:00" if r.get("Data") else None
                tipo = "Reunião de Comissão"
                
                id_evento_interno = registrar_evento(id_evento_api, 'Senado', data_hora, tipo, id_orgao)
                
                presentes = r.get("MembrosPresentes", {}).get("Parlamentar", [])
                if isinstance(presentes, dict): presentes = [presentes]
                
                batch = []
                for p in presentes:
                    id_senador_api = str(p.get("CodigoParlamentar"))
                    if id_senador_api in map_senadores:
                        id_interno = map_senadores[id_senador_api]
                        batch.append((id_interno, id_evento_interno, 'PRESENTE', None))
                
                if batch:
                    cursor.executemany("""
                        INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca, justificativa)
                        VALUES (%s, %s, %s, %s)
                    """, batch)
                    
            salvar_checkpoint_transacao(chk_senado, id_orgao)
            db.commit()
            time.sleep(0.3)
            
        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_presencas_camara()
        importar_presencas_senado()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()