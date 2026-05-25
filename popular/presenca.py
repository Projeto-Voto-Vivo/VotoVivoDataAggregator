import os
import requests
import mysql.connector
import time
from datetime import datetime
from bs4 import BeautifulSoup
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo")
)
cursor = db.cursor()

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado Federal'")
map_deputados = {str(api): interno for api, interno in cursor.fetchall()}

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Senador'")
map_senadores = {str(api): interno for api, interno in cursor.fetchall()}

def buscar_justificativa_camara(id_api_deputado, data_str):
    """
    Faz Web Scraping no site da Câmara para encontrar o motivo da ausência
    data_str formato: 'YYYY-MM-DD'
    """
    if not data_str: return None
    
    ano = data_str[:4]
    data_formatada = f"{data_str[8:10]}/{data_str[5:7]}/{ano}" # DD/MM/YYYY
    
    url = f"https://www.camara.leg.br/deputados/{id_api_deputado}/presenca-plenario/{ano}"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
            
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
    except Exception as e:
        return None

def registrar_evento(id_api, casa, data_hora, descricao_tipo, id_orgao=None):
    """Insere o evento no banco e retorna o ID interno"""
    cursor.execute("""
        INSERT INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE descricaoTipo = VALUES(descricaoTipo)
    """, (str(id_api), casa, id_orgao, data_hora, descricao_tipo))
    db.commit()
    
    cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s AND casa = %s", (str(id_api), casa))
    return cursor.fetchone()[0]

def importar_presencas_camara():
    print("\n--- IMPORTANDO PRESENÇAS DA CÂMARA ---")
    
    deputados_lista = list(map_deputados.items())
    
    # Trava de Segurança
    if not deputados_lista:
        print("AVISO: Nenhum Deputado Federal encontrado no banco!")
        print("Rode o script 'parlamentar.py' primeiro.")
        return

    if is_test_mode:
        print("[MODO TESTE] Processando 3 deputados para Câmara.")
        deputados_lista = deputados_lista[:3]
        
    start_time = time.time()
    
    for id_api_dep, id_interno_dep in tqdm(deputados_lista, desc="Deputados (Câmara)"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
            break
            
        url_eventos = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api_dep}/eventos"
        params = {"dataInicio": "2025-08-01", "dataFim": "2025-08-31", "ordem": "ASC"}
        
        try:
            res_eventos = requests.get(url_eventos, params=params, timeout=15)
            if res_eventos.status_code != 200: continue
            
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
                    ON DUPLICATE KEY UPDATE 
                        statusPresenca = VALUES(statusPresenca),
                        justificativa = VALUES(justificativa)
                """, (id_interno_dep, id_evento_interno, status_presenca, justificativa))
            
            db.commit()
            time.sleep(0.2)
            
        except Exception as e:
            continue

def importar_presencas_senado():
    print("\n--- IMPORTANDO PRESENÇAS DO SENADO (COMISSÕES) ---")
    
    cursor.execute("SELECT idOrgao, idApi, sigla FROM orgao WHERE casa = 'Senado' OR casa = 'Congresso'")
    comissoes = cursor.fetchall()
    
    if not comissoes:
        print("AVISO: Nenhuma Comissão do Senado encontrada no banco!")
        print("Rode o script 'orgao.py' primeiro.")
        return
    
    if is_test_mode:
        print("[MODO TESTE] Processando 3 comissões do Senado.")
        comissoes = comissoes[:3]

    start_time = time.time()
    headers = {"Accept": "application/json"}
    
    for id_orgao, id_api_orgao, sigla in tqdm(comissoes, desc="Comissões (Senado)"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
            break
            
        if not sigla or sigla == 'N/A': continue
            
        url_reunioes = f"https://legis.senado.leg.br/dadosabertos/comissao/{sigla}/reunioes"
        params = {"dataInicio": "20250801", "dataFim": "20250831"}
        
        try:
            res = requests.get(url_reunioes, headers=headers, params=params, timeout=15)
            if res.status_code != 200: continue
            
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
            db.commit()
            time.sleep(0.3)
            
        except Exception as e:
            continue

if __name__ == "__main__":
    importar_presencas_camara()
    importar_presencas_senado()
    
    cursor.close()
    db.close()
    print("\nImportação de Presenças finalizada com sucesso!")
