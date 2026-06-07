import os
import requests
import mysql.connector
import time
import sys
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

BASE_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"
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

script_camara = "popular/tramitacao.py#camara"
script_senado = "popular/tramitacao.py#senado"

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

def importar_tramitacao_camara():
    checkpoint_atual = int(obter_ultimo_checkpoint(script_camara, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Camara' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila_proposicoes = cursor.fetchall()
    
    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Tramitações Câmara"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL_CAMARA}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=30)

            if res.status_code != 200:
                salvar_checkpoint_transacao(script_camara, id_interno)
                db.commit()
                continue

            dados = res.json().get("dados", [])
            dados.sort(key=lambda x: x.get("sequencia") or 0)

            for t in dados:
                sequencia = t.get("sequencia")
                if not sequencia:
                    continue

                id_tipo = map_tipo.get(str(t.get("codTipoTramitacao")))
                if id_tipo is None:
                    continue

                id_orgao_api = extrair_id(t.get("uriOrgao"))
                id_orgao = garantizar_orgao(id_orgao_api, 'Camara')
                id_api_tramitacao = f"{id_api}_{sequencia}"

                cursor.execute("""
                    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        idTipoTramitacao = VALUES(idTipoTramitacao),
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        descricaoSituacao = VALUES(descricaoSituacao),
                        despacho = VALUES(despacho)
                """, (id_api_tramitacao, id_interno, id_tipo, id_orgao, t.get("dataHora"), sequencia, t.get("descricaoTramitacao"), t.get("descricaoSituacao"), t.get("despacho")))

            salvar_checkpoint_transacao(script_camara, id_interno)
            db.commit()
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

def importar_tramitacao_senado():
    checkpoint_atual = int(obter_ultimo_checkpoint(script_senado, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Senado' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila_proposicoes = cursor.fetchall()

    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()
    headers = {"Accept": "application/json"}

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Tramitações Senado"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL_SENADO}/materia/movimentacoes/{id_api}"
            res = requests.get(url, headers=headers, timeout=30)

            if res.status_code != 200:
                salvar_checkpoint_transacao(script_senado, id_interno)
                db.commit()
                continue

            dados_materia = res.json().get("MovimentacaoMateria", {}).get("Materia", {})
            historico = dados_materia.get("HistoricoMovimentacoes", {}).get("Movimentacao", [])

            if isinstance(historico, dict):
                historico = [historico]

            historico.sort(key=lambda x: int(x.get("CodigoTramitacao") or 0))

            for seq, m in enumerate(historico, start=1):
                cod_tramitacao = m.get("CodigoTramitacao")
                if not cod_tramitacao:
                    continue

                data_hora = m.get("DataTramitacao")
                if data_hora and len(data_hora) == 10:
                    data_hora = f"{data_hora} 00:00:00"

                descr_tramitacao = m.get("DescricaoComissao") or m.get("IdentificacaoOrgao") or "Senado"
                id_orgao = garantizar_orgao(m.get("CodigoComissao") or m.get("CodigoOrgao"), 'Senado')
                
                id_api_tramitacao = f"SEN_{id_api}_{cod_tramitacao}"

                cursor.execute("""
                    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
                    VALUES (%s, %s, NULL, %s, %s, %s, %s, NULL, %s)
                    ON DUPLICATE KEY UPDATE
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        despacho = VALUES(despacho)
                """, (id_api_tramitacao, id_interno, id_orgao, data_hora, seq, descr_tramitacao, m.get("TextoParecer") or m.get("DescricaoUltimaSituacao")))

            salvar_checkpoint_transacao(script_senado, id_interno)
            db.commit()
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_tramitacao_camara()
        importar_tramitacao_senado()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()