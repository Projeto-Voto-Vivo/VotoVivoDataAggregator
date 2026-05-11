import requests
import mysql.connector
import time
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# Configuração da Conexão
try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor()
    print("Conexão com o banco de dados estabelecida.\n")
except mysql.connector.Error as err:
    print(f"Erro ao conectar ao banco: {err}")
    exit(1)

# Período de busca: Da última eleição (2023) até o fim da legislatura (2026)
ANOS_BUSCA = [2023, 2024, 2025, 2026]
MESES_BUSCA = list(range(1, 13)) # Todos os meses

# Busca parlamentares e mapeia idApi -> idParlamentar local
cursor.execute("SELECT idApi, idParlamentar, cargo FROM parlamentar")
parlamentares_db = cursor.fetchall()
mapa_parlamentares = {str(p[0]): p[1] for p in parlamentares_db}

def buscar_despesas_deputado(id_api_dep, ano, meses):
    """Coleta despesas da Câmara para um ano e lista de meses específicos"""
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api_dep}/despesas"
    resultado = []
    
    for mes in meses:
        pagina = 1
        while True:
            params = {"ano": ano, "mes": mes, "itens": 100, "pagina": pagina}
            try:
                r = requests.get(url, params=params, timeout=20)
                if r.status_code != 200: break
                data = r.json()
                dados = data.get("dados", [])
                if not dados: break
                resultado.extend(dados)
                if not any(l["rel"] == "next" for l in data.get("links", [])): break
                pagina += 1
                time.sleep(0.05) # Delay para evitar bloqueio por excesso de requisições
            except: break
    return resultado

def processar_despesas_senado_em_bloco(ano):
    """Coleta despesas de TODOS os senadores (Lote Anual)"""
    url = f"https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/{ano}"
    print(f"-> Baixando lote anual do Senado para o ano {ano}...")
    try:
        r = requests.get(url, timeout=90)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"Erro ao baixar lote do Senado ({ano}): {e}")
        return []

total_inserido = 0

# Separa deputados para o loop individual
deputados = [p for p in parlamentares_db if p[2] == 'Deputado Federal']

for ano in ANOS_BUSCA:
    print(f"\n--- INICIANDO PROCESSAMENTO DO ANO {ano} ---")

    # --- CÂMARA (Individual) ---
    print(f"[CÂMARA] Coletando despesas detalhadas para {len(deputados)} deputados...")
    for id_api, id_interno, _ in tqdm(deputados, desc=f"Deputados {ano}"):
        despesas = buscar_despesas_deputado(id_api, ano, MESES_BUSCA)
        batch = []
        for d in despesas:
            batch.append((
                id_interno, d.get("dataDocumento"), d.get("valorLiquido"),
                d.get("nomeFornecedor"), d.get("cnpjCpfFornecedor"),
                d.get("urlDocumento"), d.get("tipoDespesa")
            ))
        
        if batch:
            cursor.executemany("""
                INSERT IGNORE INTO despesa 
                (idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, batch)
            db.commit()
            total_inserido += len(batch)

    # --- SENADO (Lote) ---
    print(f"[SENADO] Coletando lote detalhado para todos os senadores...")
    lote_senado = processar_despesas_senado_em_bloco(ano)
    batch_senado = []

    if lote_senado:
        for d in tqdm(lote_senado, desc=f"Senadores {ano}"):
            id_api_sen = str(d.get("codSenador"))
            # Verifica se o senador está no nosso banco de dados
            if id_api_sen in mapa_parlamentares:
                id_interno = mapa_parlamentares[id_api_sen]
                
                # A API do Senado retorna a data exata no campo 'data'
                data_despesa = d.get("data") 
                
                batch_senado.append((
                    id_interno, data_despesa, d.get("valorReembolsado"),
                    d.get("fornecedor"), d.get("cpfCnpj"), None, d.get("tipoDespesa")
                ))

        if batch_senado:
            cursor.executemany("""
                INSERT IGNORE INTO despesa 
                (idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, batch_senado)
            db.commit()
            total_inserido += len(batch_senado)

print("\n" + "="*50)
print(f"IMPORTAÇÃO COMPLETA (2023-2026): {total_inserido} registros inseridos.")
print("="*50)

cursor.close()
db.close()
