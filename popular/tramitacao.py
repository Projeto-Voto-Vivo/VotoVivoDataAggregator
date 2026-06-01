import os
import requests
import mysql.connector
import time
import sys
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
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

script_checkpoint = "popular/tramitacao.py#camara"

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

cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE idApi IS NOT NULL")
proposicoes_banco = sorted(cursor.fetchall(), key=lambda x: x[0])
map_proposicao = {str(row[1]): row[0] for row in proposicoes_banco}

cursor.execute("SELECT idTipoTramitacao, idApi FROM tipoTramitacao")
map_tipo = {str(row[1]): row[0] for row in cursor.fetchall()}

orgaos_cache = {}
cursor.execute("SELECT idOrgao, idApi FROM orgao")
for id_, idApi in cursor.fetchall():
    orgaos_cache[str(idApi)] = id_

def extrair_id(uri):
    try:
        return str(int(uri.split("/")[-1]))
    except:
        return None

def garantizar_orgao(id_api_orgao, casa='Camara'):
    if not id_api_orgao:
        return None
    id_api_str = str(id_api_orgao)
    if id_api_str in orgaos_cache:
        return orgaos_cache[id_api_str]

    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = %s", (id_api_str, casa))
    res = cursor.fetchone()
    if res:
        orgaos_cache[id_api_str] = res[0]
        return res[0]

    cursor.execute(
        "INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
        (id_api_str, "N/A", f"Órgão não mapeado ({id_api_str})", casa)
    )
    db.commit()
    id_novo = cursor.lastrowid
    orgaos_cache[id_api_str] = id_novo
    return id_novo

def importar_tramitacao():
    checkpoint_atual = int(obter_ultimo_checkpoint(script_checkpoint, default_value="0"))
    fila_proposicoes = [p for p in proposicoes_banco if p[0] > checkpoint_atual]
    
    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Importando tramitações"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=30)

            if res.status_code != 200:
                salvar_checkpoint_transacao(script_checkpoint, id_interno)
                db.commit()
                continue

            dados = res.json().get("dados", [])
            dados.sort(key=lambda x: x.get("sequencia") or 0)

            for t in dados:
                sequencia = t.get("sequencia")
                if not sequencia:
                    continue

                dataHora = t.get("dataHora")
                descricao = t.get("descricaoTramitacao")
                situacao = t.get("descricaoSituacao")
                despacho = t.get("despacho")
                codTipo = t.get("codTipoTramitacao")
                uriOrgao = t.get("uriOrgao")

                id_proposicao = map_proposicao.get(str(id_api))
                id_tipo = map_tipo.get(str(codTipo))

                id_orgao_api = extrair_id(uriOrgao)
                id_orgao = garantizar_orgao(id_orgao_api, 'Camara')

                if id_tipo is None:
                    continue

                id_api_tramitacao = f"{id_api}_{sequencia}"

                cursor.execute("""
                    INSERT INTO tramitacao (
                        idApi,
                        idProposicao,
                        idTipoTramitacao,
                        idOrgao,
                        dataHora,
                        sequencia,
                        descricaoTramitacao,
                        descricaoSituacao,
                        despacho
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        idTipoTramitacao = VALUES(idTipoTramitacao),
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        descricaoSituacao = VALUES(descricaoSituacao),
                        despacho = VALUES(despacho)
                """, (
                    id_api_tramitacao,
                    id_proposicao,
                    id_tipo,
                    id_orgao,
                    dataHora,
                    sequencia,
                    descricao,
                    situacao,
                    despacho
                ))

            salvar_checkpoint_transacao(script_checkpoint, id_interno)
            db.commit()
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_tramitacao()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()