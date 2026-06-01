import os
import requests
import mysql.connector
import time
from tqdm import tqdm
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

BASE_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor()
    cursor.execute("SELECT DATABASE()")
    print(f" [+] Banco conectado: {cursor.fetchone()[0]}")
    print(" [+] Conexão estabelecida com sucesso.")
except mysql.connector.Error as err:
    print(f" [!] Erro de conexão com o banco de dados: {err}")
    exit(1)

def obter_ultimo_checkpoint(nome_script, default_value="1"):
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

sql_check_existe = "SELECT 1 FROM parlamentar WHERE idApi = %s"

sql_insert = """
    INSERT INTO parlamentar 
    (idApi, cargo, nomeCivil, nomeUrna, partidoAtual, uf, fotoUrl, dataNascimento, email, telephone, enderecoGabinete)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

sql_update = """
    UPDATE parlamentar SET 
        cargo = %s,
        partidoAtual = %s,
        fotoUrl = %s,
        email = %s,
        telephone = %s,
        enderecoGabinete = %s,
        dataNascimento = %s
    WHERE idApi = %s
"""

print("\n[*] Iniciando importação dos Deputados...")
url_camara = "https://dadosabertos.camara.leg.br/api/v2/deputados"

script_deputados = "popular/parlamentar.py#deputados"
pagina = int(obter_ultimo_checkpoint(script_deputados, default_value="1"))
total_deputados_execucao = 0

if pagina > 1:
    print(f" [i] Registro de checkpoint localizado. Recomeçando da página: {pagina}")

try:
    while True:
        response = requests.get(url_camara, params={"pagina": pagina, "itens": 100}, timeout=20)
        dados = response.json().get("dados", [])

        if not dados:
            print(" [+] Fim da paginação da API da Câmara atingido.")
            break

        if db.in_transaction:
            db.commit()

        db.start_transaction()
        bloco_com_sucesso = True
        
        print(f" -> Lendo dados da Página {pagina} da API...")

        for dep in dados:
            id_api = dep.get("id")
            nome = dep.get("nome")
            email = dep.get("email")
            partido = dep.get("siglaPartido")
            uf = dep.get("siglaUf")
            foto = dep.get("urlFoto")

            telefone = None
            endereco = None
            data_nascimento = None

            try:
                detalhe_url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api}"
                resp = requests.get(detalhe_url, timeout=10)

                if resp.status_code == 200:
                    detalhe = resp.json().get("dados", {})
                    gabinete = detalhe.get("ultimoStatus", {}).get("gabinete", {})

                    telefone = gabinete.get("telefone")
                    data_nascimento = detalhe.get("dataNascimento")
                    predio = gabinete.get("predio")
                    sala = gabinete.get("sala")

                    if predio and sala:
                        endereco = f"Anexo {predio}, Sala {sala}"
                    elif sala:
                        endereco = f"Sala {sala}"
            except Exception as e:
                print(f" [!] Erro ao buscar detalhes adicionais do deputado {id_api}: {e}")

            cursor.execute(sql_check_existe, (id_api,))
            parlamentar_existe = cursor.fetchone()

            try:
                if parlamentar_existe:
                    valores_update = (
                        "Deputado Federal", partido, foto, email, 
                        telefone, endereco, data_nascimento, id_api
                    )
                    cursor.execute(sql_update, valores_update)
                else:
                    valores_insert = (
                        id_api, "Deputado Federal", nome, nome,
                        partido, uf, foto, data_nascimento, email, 
                        telefone, endereco
                    )
                    cursor.execute(sql_insert, valores_insert)
                
                total_deputados_execucao += 1
            except Exception as e:
                print(f" [!] Erro de persistência no deputado {id_api}: {e}")
                bloco_com_sucesso = False
                break

            time.sleep(0.1)

        if bloco_com_sucesso:
            salvar_checkpoint_transacao(script_deputados, pagina)
            db.commit()
            print(f" [+] Página {pagina} consolidada no banco! Acumulado nesta execução: {total_deputados_execucao}")
            pagina += 1
        else:
            db.rollback()
            break

except KeyboardInterrupt:
    
    if db.in_transaction:
        db.rollback()

print("\n[*] Iniciando importação dos Senadores...")
url_senado = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
headers = {"Accept": "application/json"}

script_senadores = "popular/parlamentar.py#senadores"
checkpoint_senado = obter_ultimo_checkpoint(script_senadores, default_value="PENDENTE")
data_hoje = datetime.now().strftime("%Y-%m-%d")
total_senadores_execucao = 0

if checkpoint_senado == f"CONCLUIDO_{data_hoje}":
    print(" [i] Carga diária do Senado Federal já realizada hoje. Etapa pulada.")
else:
    try:
        res = requests.get(url_senado, headers=headers, timeout=30)

        if res.status_code == 200:
            lista = res.json()["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]
            
            if db.in_transaction:
                db.commit()

            db.start_transaction()
            senado_sucesso = True

            for sen in lista:
                ident = sen["IdentificacaoParlamentar"]
                codigo = ident["CodigoParlamentar"]

                nome = ident["NomeParlamentar"]
                nome_completo = ident["NomeCompletoParlamentar"]
                partido = ident.get("SiglaPartidoParlamentar", "S/PARTIDO")
                uf = ident["UfParlamentar"]
                foto = ident.get("UrlFotoParlamentar")
                email = ident.get("EmailParlamentar")

                telefone = None
                endereco = "Senado Federal, Praça dos Três Poderes"
                data_nascimento = None

                try:
                    detalhe_url = f"https://legis.senado.leg.br/dadosabertos/senador/{codigo}"
                    resp = requests.get(detalhe_url, headers=headers, timeout=10)

                    if resp.status_code == 200:
                        detalhe = resp.json()
                        dados_basicos = detalhe.get("DetalheParlamentar", {}) \
                                               .get("Parlamentar", {}) \
                                               .get("DadosBasicosParlamentar", {})

                        data_nascimento = dados_basicos.get("DataNascimento")
                except Exception as e:
                    print(f" [!] Erro ao buscar detalhes adicionais do senador {codigo}: {e}")

                cursor.execute(sql_check_existe, (codigo,))
                senador_existe = cursor.fetchone()

                try:
                    if senador_existe:
                        valores_update = (
                            "Senador", partido, foto, email, 
                            telefone, endereco, data_nascimento, codigo
                        )
                        cursor.execute(sql_update, valores_update)
                    else:
                        valores_insert = (
                            codigo, "Senador", nome_completo, nome,
                            partido, uf, foto, data_nascimento, email, 
                            telefone, endereco
                        )
                        cursor.execute(sql_insert, valores_insert)
                    
                    total_senadores_execucao += 1
                except Exception as e:
                    print(f" [!] Erro de persistência no senador {codigo}: {e}")
                    senado_sucesso = False
                    break

                time.sleep(0.1)

            if senado_sucesso:
                salvar_checkpoint_transacao(script_senadores, f"CONCLUIDO_{data_hoje}")
                db.commit()
                print(f" [+] Carga do Senado Federal consolidada com sucesso!")
            else:
                db.rollback()

    except KeyboardInterrupt:
        print("\n [!] Execução interrompida durante a carga do Senado. Efetuando Rollback...")
        if db.in_transaction:
            db.rollback()

print("\n" + "="*50)
print("              IMPORTAÇÃO CONCLUÍDA")
print("="*50)
print(f" -> Deputados gravados nesta chamada: {total_deputados_execucao}")
print(f" -> Senadores gravados nesta chamada: {total_senadores_execucao}")
print("="*50)

print("\n[+] Efetuando teste de amostragem no banco:")
cursor.execute("""
    SELECT nomeUrna, cargo, partidoAtual, uf, dataNascimento
    FROM parlamentar
    ORDER BY idParlamentar DESC
    LIMIT 3
""")

for row in cursor.fetchall():
    print(f"  - {row}")

cursor.close()
db.close()
print("\n[+] Conexão com o SGBD encerrada de forma segura. [FIM]")