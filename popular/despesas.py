import os
import sys
import time
from dotenv import load_dotenv
import mysql.connector
import requests
from tqdm import tqdm

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo"),
    )
    cursor = db.cursor()
    print("[+] Conexão com o banco de dados estabelecida.\n")
except mysql.connector.Error as err:
    print(f"[!] Erro ao conectar ao banco: {err}")
    exit(1)


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



ANOS_BUSCA = [2025, 2026]
MESES_COMPLETOS = list(range(1, 13))


script_camara = "popular/despesas.py#camara_25_26"
script_senado = "popular/despesas.py#senado_25_26"

cursor.execute("SELECT idApi, idParlamentar, cargo FROM parlamentar")
parlamentares_db = cursor.fetchall()
mapa_parlamentares = {str(p[0]): p[1] for p in parlamentares_db}

total_inserido = 0


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
                if r.status_code != 200:
                    break
                data = r.json()
                dados = data.get("dados", [])
                if not dados:
                    break
                resultado.extend(dados)
                if not any(l["rel"] == "next" for l in data.get("links", [])):
                    break
                pagina += 1
                time.sleep(0.1)
            except Exception:
                break
    return resultado


def processar_despesas_senado_em_bloco(ano):
    """Coleta despesas de TODOS os senadores (Lote Anual)"""
    url = f"https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/{ano}"
    print(f" -> Baixando lote anual do Senado para o ano {ano}...")
    try:
        r = requests.get(url, timeout=90)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f" [!] Erro ao baixar lote do Senado ({ano}): {e}")
        return []


deputados = [p for p in parlamentares_db if p[2] == "Deputado Federal"]

try:
    for ano in ANOS_BUSCA:
        print(f"\n--- INICIANDO PROCESSAMENTO DO ANO {ano} ---")

        print(f"[CÂMARA] Analisando despesas para {len(deputados)} deputados...")

        checkpoint_camara_atual = obter_ultimo_checkpoint(script_camara, default_value="0_0")
        ano_chk, id_interno_chk = map(int, checkpoint_camara_atual.split('_'))

        start_time = time.time()
        for id_api, id_interno, _ in tqdm(deputados, desc=f"Deputados {ano}"):
            # Validação do checkpoint adaptado
            if ano < ano_chk:
                continue
            if ano == ano_chk and id_interno <= id_interno_chk:
                continue

            despesas = buscar_despesas_deputado(id_api, ano, meses_filtrados)
            batch = []
            for d in despesas:
                batch.append(
                    (
                        id_interno,
                        d.get("dataDocumento"),
                        d.get("valorLiquido"),
                        d.get("nomeFornecedor"),
                        d.get("cnpjCpfFornecedor"),
                        d.get("urlDocumento"),
                        d.get("tipoDespesa"),
                    )
                )

            if db.in_transaction:
                db.commit()

            db.start_transaction()

            if batch:
                cursor.executemany(
                    """
                    INSERT IGNORE INTO despesa 
                    (idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                    batch,
                )
                total_inserido += len(batch)

            salvar_checkpoint_transacao(script_camara, f"{ano}_{id_interno}")
            db.commit()

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                print(f"\n[LIMITE DE TEMPO] Câmara interrompida após {tempo_limite_segundos}s.")
                break

        print(f"[SENADO] Analisando lote de despesas dos senadores...")
        checkpoint_senado_atual = obter_ultimo_checkpoint(script_senado, default_value="0")
        
        
        if ano <= int(checkpoint_senado_atual):
            print(
                f" [i] Lote anual do Senado para {ano} já foi processado anteriormente nesta execução. Pulando."
            )
        else:
            lote_senado = processar_despesas_senado_em_bloco(ano)
            batch_senado = []

            if lote_senado:
                for d in tqdm(lote_senado, desc=f"Senadores {ano}"):
                    id_api_sen = str(d.get("codSenador"))

                    # Filtro estrito de data para o Senado (já que a API deles só entrega o bloco anual de uma vez)
                    data_despesa_str = d.get("data")
                    if data_despesa_str:
                        # Extrai o mês da string de data (ex: '2025-05-12' -> 5)
                        mes_despesa = int(data_despesa_str.split("-")[1])
                        if mes_despesa not in meses_filtrados:
                            continue

                    if id_api_sen in mapa_parlamentares:
                        id_interno = mapa_parlamentares[id_api_sen]
                        batch_senado.append(
                            (
                                id_interno,
                                data_despesa_str,
                                d.get("valorReembolsado"),
                                d.get("fornecedor"),
                                d.get("cpfCnpj"),
                                None,
                                d.get("tipoDespesa"),
                            )
                        )

                if db.in_transaction:
                    db.commit()

                db.start_transaction()

                if batch_senado:
                    cursor.executemany(
                        """
                        INSERT IGNORE INTO despesa 
                        (idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                        batch_senado,
                    )
                    total_inserido += len(batch_senado)

                salvar_checkpoint_transacao(script_senado, str(ano))
                db.commit()

except KeyboardInterrupt:
    if db.in_transaction:
        db.rollback()
    print("\n[!] Execução interrompida pelo usuário via KeyboardInterrupt.")

print("\n" + "=" * 50)
print(
    f"IMPORTAÇÃO FINALIZADA: {total_inserido} novos registros salvos nesta chamada."
)
print("=" * 50)

cursor.close()
db.close()
print("[+] Conexão encerrada com segurança. [FIM]")