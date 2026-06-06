import requests
import mysql.connector
import time
import sys
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD","senha123"),
        auth_plugin="mysql_native_password",
        database=os.getenv("DB_NAME","votovivo")
    )
    cursor = db.cursor()
    print("Conexão estabelecida.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)

def get_com_retry(url, headers=None, params=None, tentativas=3):
    for tentativa in range(tentativas):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            if response.status_code == 200:
                return response
        except requests.exceptions.RequestException:
            pass
        time.sleep(2 * (tentativa + 1))
    return None

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

script_senado = "popular/autoriaProposicao.py#senado"
script_camara = "popular/autoriaProposicao.py#camara"

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
mapa_parlamentares = {str(id_api): id_parlamentar for id_api, id_parlamentar in cursor.fetchall()}

print(f"Parlamentares mapeados: {len(mapa_parlamentares)}")

def buscar_autores_senado(id_api_prop):
    url = f"https://legis.senado.leg.br/dadosabertos/processo/{id_api_prop}"
    headers = {"Accept": "application/json"}
    params = {"v": 1}
    autores = []

    response = get_com_retry(url, headers=headers, params=params)
    if not response:
        return []

    try:
        dados = response.json()

        for autor in dados.get("autoriaIniciativa", []):
            codigo = autor.get("codigoParlamentar")
            if codigo and str(codigo) in mapa_parlamentares:
                autores.append(mapa_parlamentares[str(codigo)])

        for autor in dados.get("autoria", []):
            codigo = autor.get("codigoParlamentar")
            if codigo and str(codigo) in mapa_parlamentares:
                autores.append(mapa_parlamentares[str(codigo)])

        documento = dados.get("documento", {})
        for autor in documento.get("autoria", []):
            codigo = autor.get("codigoParlamentar")
            if codigo and str(codigo) in mapa_parlamentares:
                autores.append(mapa_parlamentares[str(codigo)])

    except:
        return []

    time.sleep(1)
    return list(set(autores))

def buscar_autores_camara(id_api_prop):
    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_api_prop}/autores"
    autores = []

    response = get_com_retry(url)
    if not response:
        return []

    try:
        dados = response.json().get("dados", [])
        for autor in dados:
            uri = autor.get("uri", "")
            if "deputados/" in uri:
                id_api = uri.split("/")[-1]
                if id_api in mapa_parlamentares:
                    autores.append(mapa_parlamentares[id_api])
    except:
        return []

    time.sleep(0.2)
    return list(set(autores))

checkpoint_senado_atual = int(obter_ultimo_checkpoint(script_senado, default_value="0"))

cursor.execute("""
    SELECT p.idProposicao, p.idApi, t.sigla, p.numero, p.ano
    FROM proposicao p
    JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
    WHERE t.casa = 'Senado'
    ORDER BY p.idProposicao ASC
""")

proposicoes = cursor.fetchall()
total = 0

try:
    for i, (id_prop, id_api, sigla, numero, ano) in enumerate(proposicoes, 1):
        if id_prop <= checkpoint_senado_atual:
            continue

        autores = buscar_autores_senado(id_api)

        if db.in_transaction:
            db.commit()

        db.start_transaction()

        for autor in autores:
            cursor.execute(
                "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)",
                (autor, id_prop)
            )
            total += 1

        salvar_checkpoint_transacao(script_senado, id_prop)
        db.commit()

        if i % 10 == 0:
            print(f"Senado {i}/{len(proposicoes)} {total}")

except KeyboardInterrupt:
    if db.in_transaction:
        db.rollback()
    print("Execução interrompida no Senado.")
    cursor.close()
    db.close()
    sys.exit(0)

checkpoint_camara_atual = int(obter_ultimo_checkpoint(script_camara, default_value="0"))

cursor.execute("""
    SELECT p.idProposicao, p.idApi, t.sigla, p.numero, p.ano
    FROM proposicao p
    JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
    WHERE t.casa = 'Camara'
    ORDER BY p.idProposicao ASC
""")

proposicoes = cursor.fetchall()
total_camara = 0

try:
    for i, (id_prop, id_api, sigla, numero, ano) in enumerate(proposicoes, 1):
        if id_prop <= checkpoint_camara_atual:
            continue

        autores = buscar_autores_camara(id_api)

        if db.in_transaction:
            db.commit()

        db.start_transaction()

        for autor in autores:
            cursor.execute(
                "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)",
                (autor, id_prop)
            )
            total_camara += 1

        salvar_checkpoint_transacao(script_camara, id_prop)
        db.commit()

        if i % 200 == 0:
            print(f"Camara {i}/{len(proposicoes)} {total_camara}")

except KeyboardInterrupt:
    if db.in_transaction:
        db.rollback()
    print("Execução interrompida na Câmara.")
    cursor.close()
    db.close()
    sys.exit(0)

print("Finalizado")
print(f"Senado {total}")
print(f"Camara {total_camara}")

cursor.close()
db.close()
print("Conexao encerrada")
