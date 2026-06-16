import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
import mysql.connector
from utils.http_client import http_client
from tqdm import tqdm

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
        database=os.getenv("DB_NAME", "votoVivo"),
    )
    cursor = db.cursor()
    print("[+] Conexão com o banco de dados estabelecida com sucesso.\n")
except mysql.connector.Error as err:
    print(f"[!] Erro ao conectar ao banco: {err}")
    sys.exit(1)


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


script_camara = "popular/orgao.py#camara_25_26"
script_senado = "popular/orgao.py#senado_25_26"


def extrair_id_orgao(uri):
    try:
        return str(int(uri.split("/")[-1]))
    except:
        return None


def buscar_nome_orgao(id_api):
    try:
        url = f"{BASE_URL_CAMARA}/orgaos/{id_api}"
        r = http_client.get_safe(url, timeout=10)
        if r.status_code == 200:
            nome = r.json().get("dados", {}).get("nome")
            return nome[:500] if nome else None
    except:
        pass
    return None


def importar_orgaos_camara():
    print("[*] Iniciando análise de órgãos pela Câmara dos Deputados...")

    ultimo_id_proposicao_chk = int(
        obter_ultimo_checkpoint(script_camara, default_value="0")
    )

    cursor.execute("""
        SELECT p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE (t.casa = 'Camara' OR t.casa = 'Congresso') AND p.ano IN (2025, 2026)
        ORDER BY p.idApi ASC
    """)
    proposicoes = cursor.fetchall()

    total = len(proposicoes)
    print(f"Total de proposições a avaliar no banco: {total}")
    if ultimo_id_proposicao_chk > 0:
        print(f" [i] Checkpoint ativo: Pulando automaticamente até a proposição da API de ID {ultimo_id_proposicao_chk}...")

    start_time = time.time()
    contador_novos_orgaos = 0

    try:
        for (id_api,) in tqdm(proposicoes, desc="Processando órgãos (Câmara)", unit="proposição"):

            if int(id_api) <= int(ultimo_id_proposicao_chk):
                continue

            if (
                tempo_limite_segundos > 0
                and (time.time() - start_time) > tempo_limite_segundos
            ):
                print(
                    f"\n [!] Limite de tempo esgotado ({tempo_limite_segundos}s). Interrompendo processamento da Câmara."
                )
                break

            try:
                url = f"{BASE_URL_CAMARA}/proposicoes/{id_api}/tramitacoes"
                res = http_client.get_safe(url, timeout=10)

                if res.status_code != 200:
                    if db.in_transaction:
                        db.commit()
                    db.start_transaction()
                    salvar_checkpoint_transacao(script_camara, id_api)
                    db.commit()
                    continue

                dados = res.json().get("dados", [])
                orgaos_unicos = {}

                for t in dados:
                    uri = t.get("uriOrgao")
                    sigla = t.get("siglaOrgao")
                    id_orgao_api = extrair_id_orgao(uri)

                    if id_orgao_api and id_orgao_api not in orgaos_unicos:
                        orgaos_unicos[id_orgao_api] = (sigla or "N/A")[:50]

                if db.in_transaction:
                    db.commit()

                db.start_transaction()

                for id_orgao_api, sigla in orgaos_unicos.items():
                    cursor.execute(
                        "SELECT idOrgao, nome FROM orgao WHERE idApi = %s AND casa = 'Camara'",
                        (id_orgao_api,),
                    )
                    existente = cursor.fetchone()

                    if existente:
                        if not existente[1]:
                            nome = buscar_nome_orgao(id_orgao_api)
                            if nome:
                                cursor.execute(
                                    """
                                    UPDATE orgao SET nome = %s WHERE idApi = %s AND casa = 'Camara'
                                """,
                                    (nome, id_orgao_api),
                                )
                    else:
                        nome = buscar_nome_orgao(id_orgao_api)
                        cursor.execute(
                            """
                            INSERT IGNORE INTO orgao (idApi, sigla, nome, casa)
                            VALUES (%s, %s, %s, 'Camara')
                        """,
                            (id_orgao_api, sigla, nome),
                        )
                        contador_novos_orgaos += 1

                salvar_checkpoint_transacao(script_camara, id_api)
                db.commit()
                time.sleep(0.1)

            except Exception as e:
                print(
                    f"\n [!] Erro de processamento na proposição {id_api}: {e}"
                )
                if db.in_transaction:
                    db.rollback()

    except KeyboardInterrupt:
        print(
            "\n [!] Execução interrompida via teclado (Ctrl+C). Aplicando Rollback de segurança nos lotes da Câmara..."
        )
        if db.in_transaction:
            db.rollback()


def importar_orgaos_senado():
    print("\n[*] Iniciando importação dos colegiados/comissões do Senado...")

    checkpoint_senado_atual = obter_ultimo_checkpoint(
        script_senado, default_value="PENDENTE"
    )
    data_hoje = datetime.now().strftime("%Y-%m-%d")

    if checkpoint_senado_atual == f"CONCLUIDO_{data_hoje}":
        print(" [i] Carga de comissões do Senado já realizada com sucesso hoje. Pulando etapa.")
        return

    url = f"{BASE_URL_SENADO}/comissao/lista/colegiados"
    headers = {"Accept": "application/json"}

    try:
        res = http_client.get_safe(url, headers=headers, timeout=15)
        if res.status_code == 200:
            dados = res.json()
            comissoes = (
                dados.get("ListaColegiados", {})
                .get("Colegiados", {})
                .get("Colegiado", [])
            )

            if isinstance(comissoes, dict):
                comissoes = [comissoes]
                
            if db.in_transaction:
                db.commit()

            db.start_transaction()

            for c in comissoes:
                id_api = str(c.get("Codigo"))
                sigla = c.get("Sigla", "N/A")[:50]
                nome = c.get("Nome", "Sem Nome")[:500]
                sigla_casa = c.get("SiglaCasa", "")

                if sigla_casa == "CN":
                    casa = "Congresso"
                elif sigla_casa == "SF":
                    casa = "Senado"
                else:
                    casa = (
                        "Congresso"
                        if "Mista" in nome or "Misto" in nome
                        else "Senado"
                    )

                cursor.execute(
                    """
                    INSERT INTO orgao (idApi, sigla, nome, casa)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        sigla = VALUES(sigla),
                        nome = VALUES(nome),
                        casa = VALUES(casa)
                """,
                    (id_api, sigla, nome, casa),
                )

            salvar_checkpoint_transacao(
                script_senado, f"CONCLUIDO_{data_hoje}"
            )
            db.commit()
            print(
                f" [+] Sucesso! {len(comissoes)} órgãos do Senado Federal gravados."
            )
        else:
            print(
                f" [!] Erro ao buscar comissões do Senado: Status {res.status_code}"
            )

    except Exception as e:
        print(f" [!] Erro na importação do Senado: {e}")
        if db.in_transaction:
            db.rollback()
    except KeyboardInterrupt:
        print(
            "\n [!] Interrupção detectada durante a carga do Senado. Cancelando lote..."
        )
        if db.in_transaction:
            db.rollback()


if __name__ == "__main__":
    importar_orgaos_camara()
    importar_orgaos_senado()
    cursor.close()
    db.close()
    print("\n[+] Conexão encerrada com segurança. [FIM]")
