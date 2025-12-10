import requests
import mysql.connector
import time
import sys 
import os  
from dotenv import load_dotenv 

load_dotenv()

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votovivo")
    )
    cursor = db.cursor()
    print("Conexão com o banco de dados estabelecida.")
except mysql.connector.Error as err:
    print(f"Erro ao conectar ao MySQL: {err}")
    sys.exit(1)


try:

    cursor.execute("SELECT idDeputado FROM Deputado")
    deputados_db = cursor.fetchall()
except mysql.connector.Error as err:
    print(f"Erro ao buscar deputados: {err}")
    cursor.close()
    db.close()
    sys.exit(1)

print(f"Total de deputados no banco: {len(deputados_db)}")
print("Iniciando importação de despesas...\n")


ANO_INICIAL = 2025
ANO_FINAL = 2025
MES_INICIAL = 9
MES_FINAL = 11

total_despesas = 0
deputados_processados = 0


for (id_deputado,) in deputados_db:
    despesas_deputado = 0

    for ano in range(ANO_INICIAL, ANO_FINAL + 1):
        for mes in range(MES_INICIAL, MES_FINAL + 1):

            
            url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_deputado}/despesas"
            params = {
                "ano": ano,
                "mes": mes,
                "itens": 100,
                "ordem": "ASC",
                "ordenarPor": "dataDocumento"
            }

            pagina = 1

            while True:
                params["pagina"] = pagina

                try:
                    response = requests.get(url, params=params)

                    if response.status_code == 200:
                        json_response = response.json()
                        dados = json_response["dados"]

                        if not dados:
                            break  

                        
                        valores_a_inserir = []
                        for despesa in dados:
                           
                            valores = (
                                id_deputado,
                                despesa.get('ano'),
                                despesa.get('cnpjCpfFornecedor'),
                                despesa.get('codDocumento'),
                                despesa.get('codLote'),
                                despesa.get('codTipoDocumento'),
                                despesa.get('dataDocumento'),
                                despesa.get('mes'),
                                despesa.get('nomeFornecedor'),
                                despesa.get('numDocumento'),
                                despesa.get('numRessarcimento'),
                                despesa.get('parcela'),
                                despesa.get('tipoDespesa'),
                                despesa.get('tipoDocumento'),
                                despesa.get('urlDocumento'),
                                despesa.get('valorDocumento'),
                                despesa.get('valorGlosa'),
                                despesa.get('valorLiquido')
                            )
                            valores_a_inserir.append(valores)

                        sql = """
                            INSERT IGNORE INTO Despesa
                            (idDeputado, ano, cnpjCpfFornecedor, codDocumento, codLote,
                            codTipoDocumento, dataDocumento, mes, nomeFornecedor, numDocumento,
                            numRessarcimento, parcela, tipoDespesa, tipoDocumento,
                            urlDocumento, valorDocumento, valorGlosa, valorLiquido)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """
                        cursor.executemany(sql, valores_a_inserir)

                        despesas_deputado += len(dados)
                        total_despesas += len(dados)

                       
                        links = {link['rel']: link['href'] for link in json_response.get('links', [])}
                        if 'next' not in links:
                            break 
                        pagina += 1
                        time.sleep(0.05) 

                    else:
                        print(f"Erro ao buscar despesas do deputado {id_deputado} ({ano}/{mes}): Status {response.status_code}")
                        break

                except Exception as e:
                    print(f"Erro ao processar deputado {id_deputado} ({ano}/{mes}): {e}")
                    break

            time.sleep(0.05) 

    db.commit()

    deputados_processados += 1

    if despesas_deputado > 0:
        print(f"Deputado ID {id_deputado}: {despesas_deputado} despesa(s) importada(s)")

    if deputados_processados % 10 == 0:
        print(f"    Progresso: {deputados_processados}/{len(deputados_db)} deputados processados")


print("\n" + "="*80)
print(f"Importação concluída!")
print(f"    • Deputados processados: {deputados_processados}")
print(f"    • Total de despesas importadas: {total_despesas}")
print("="*80)

cursor.close()
db.close()
