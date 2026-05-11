import requests
import mysql.connector
import time
import os
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

# Período de busca (Câmara exige ano/mês, Senado retorna histórico)
ANO_BUSCA = 2025
MESES_BUSCA = [7, 8, 9]

# Busca parlamentares ativos para coletar despesas
cursor.execute("""
    SELECT idApi, idParlamentar, cargo
    FROM parlamentar
    WHERE cargo IN ('Deputado Federal', 'Senador')
""")
parlamentares = cursor.fetchall()

print(f"Iniciando coleta para {len(parlamentares)} parlamentares...\n")

def buscar_despesas_deputado(id_api_dep):
    """Coleta despesas da API da Câmara dos Deputados"""
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api_dep}/despesas"
    resultado = []

    for mes in MESES_BUSCA:
        pagina = 1
        while True:
            params = {
                "ano": ANO_BUSCA,
                "mes": mes,
                "itens": 100,
                "pagina": pagina
            }
            try:
                r = requests.get(url, params=params, timeout=20)
                if r.status_code != 200:
                    break
                
                data = r.json()
                dados = data.get("dados", [])
                if not dados:
                    break

                resultado.extend(dados)

                # Verifica se existe próxima página
                if not any(l["rel"] == "next" for l in data.get("links", [])):
                    break

                pagina += 1
                time.sleep(0.1)
            except Exception as e:
                print(f" Erro na API da Câmara (Dep. {id_api_dep}): {e}")
                break

    return resultado

def buscar_despesas_senador(id_api_sen):
    """Coleta despesas da API do Senado Federal e mapeia para o padrão local"""
    url = f"https://legis.senado.leg.br/dadosabertos/senador/{id_api_sen}/despesas"
    resultado_mapeado = []
    
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return []

        data = r.json()
        # O Senado retorna uma lista dentro de ListaDespesasSenador -> Despesas -> Despesa
        lista_bruta = data.get("ListaDespesasSenador", {}).get("Despesas", {}).get("Despesa", [])
        
        # Garante que lista_bruta seja uma lista (API as vezes retorna objeto único se houver apenas uma despesa)
        if isinstance(lista_bruta, dict):
            lista_bruta = [lista_bruta]

        for d in lista_bruta:
            # Filtra apenas o período solicitado
            ano_despesa = int(d.get("Ano", 0))
            mes_despesa = int(d.get("Mes", 0))
            
            if ano_despesa == ANO_BUSCA and mes_despesa in MESES_BUSCA:
                # O Senado não fornece uma "data exata" da nota, apenas Mês/Ano. 
                # Normalizamos para o dia 1 do mês.
                data_normalizada = f"{ano_despesa}-{mes_despesa:02d}-01"
                
                # Mapeamento para os campos da tabela local
                resultado_mapeado.append({
                    "data": data_normalizada,
                    "valor": d.get("Valor"),
                    "fornecedor": d.get("Fornecedor"),
                    "cnpjCpf": d.get("CnpjCpf"),
                    "url": None, # Senado raramente fornece link direto da nota fiscal nesta API
                    "categoria": d.get("TipoDespesa")
                })

        return resultado_mapeado
    except Exception as e:
        print(f" Erro na API do Senado (Sen. {id_api_sen}): {e}")
        return []

total_geral_inserido = 0

for id_api, id_interno, cargo in parlamentares:
    dados_para_inserir = []
    
    if cargo == "Deputado Federal":
        print(f"Processando Deputado {id_api}...", end="\r")
        despesas_brutas = buscar_despesas_deputado(id_api)
        for d in despesas_brutas:
            dados_para_inserir.append((
                id_interno,
                d.get("dataDocumento"),
                d.get("valorLiquido"),
                d.get("nomeFornecedor"),
                d.get("cnpjCpfFornecedor"),
                d.get("urlDocumento"),
                d.get("tipoDespesa")
            ))

    elif cargo == "Senador":
        print(f"Processando Senador {id_api}...", end="\r")
        despesas_mapeadas = buscar_despesas_senador(id_api)
        for d in despesas_mapeadas:
            dados_para_inserir.append((
                id_interno,
                d["data"],
                d["valor"],
                d["fornecedor"],
                d["cnpjCpf"],
                d["url"],
                d["categoria"]
            ))

    if dados_para_inserir:
        try:
            sql = """
                INSERT INTO despesa
                (idParlamentar, dataDespesa, valor, fornecedorNome,
                 fornecedorCnpjCpf, notaFiscalUrl, categoria)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.executemany(sql, dados_para_inserir)
            db.commit()
            total_geral_inserido += len(dados_para_inserir)
        except mysql.connector.Error as err:
            print(f"\n Erro ao inserir despesas do parlamentar {id_api}: {err}")

    time.sleep(0.1)

print("\n" + "="*40)
print(" IMPORTAÇÃO DE DESPESAS CONCLUÍDA")
print(f" Total de registros inseridos: {total_geral_inserido}")
print("="*40)

cursor.close()
db.close()
