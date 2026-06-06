import requests
import mysql.connector
import time
import sys
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

API_KEY = os.getenv("PORTAL_TRANSPARENCIA_API_KEY")
ANO = os.getenv("EMENDAS_ANO", "2024")
SLEEP_SECONDS = float(os.getenv("EMENDAS_SLEEP", "0.7"))

if not API_KEY:
    print("Erro: defina PORTAL_TRANSPARENCIA_API_KEY no .env")
    sys.exit(1)

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votovivo")
    )
    cursor = db.cursor()
    print("Conexão estabelecida para Emendas Parlamentares.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)


script_checkpoint = "popular/emenda.py"


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


def converter_valor(valor):
    if valor is None or valor == "":
        return None
    valor = str(valor).strip()
    valor = valor.replace("R$", "").replace(" ", "")
    if "," in valor and "." in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif "," in valor:
        valor = valor.replace(",", ".")
    try:
        return Decimal(valor)
    except InvalidOperation:
        return None

def converter_data(data):
    if data is None or data == "":
        return None
    data = str(data).strip()
    formatos = ["%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"]
    for formato in formatos:
        try:
            return datetime.strptime(data[:10], formato).date()
        except ValueError:
            continue
    return None


headers = {
    "chave-api-dados": API_KEY,
    "Accept": "application/json"
}

print("=" * 50)
print("Buscando emendas parlamentares...")
print("=" * 50)


pagina = int(obter_ultimo_checkpoint(script_checkpoint, default_value="1"))
contador_emendas = 0
contador_documentos = 0
start_time = time.time()


pagina_limite_teste = pagina + 2 

while True:
    if is_test_mode and pagina > pagina_limite_teste:
        print(f"\n[MODO TESTE] Limitado a processar 3 páginas por execução. Parando na página {pagina}.")
        break

    if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
        print(f"\n[LIMITE DE TEMPO] Execução interrompida após {tempo_limite_segundos}s na página {pagina}.")
        break

    url_emendas = "https://api.portaldatransparencia.gov.br/api-de-dados/emendas"
    parametros = {
        "ano": ANO,
        "pagina": pagina
    }

    try:
        time.sleep(SLEEP_SECONDS)

        response = requests.get(
            url_emendas,
            headers=headers,
            params=parametros,
            timeout=30
        )

        print(f"\n[Fila] Lendo Página: {pagina} | URL: {response.url}")
        print("Status:", response.status_code)

        if response.status_code == 429:
            print("Limite da API atingido. Aguardando 60 segundos...")
            time.sleep(60)
            response = requests.get(
                url_emendas,
                headers=headers,
                params=parametros,
                timeout=30
            )

        if response.status_code != 200:
            print(f"Erro ao buscar emendas na página {pagina}: {response.status_code}")
            break

        emendas = response.json()

        if not emendas:
            print("Não há mais emendas para buscar.")
            break

        print(f"Página {pagina}: {len(emendas)} emenda(s) encontrada(s)")

        for emenda in emendas:
            codigo_emenda = emenda.get("codigoEmenda")

            if not codigo_emenda:
                continue

            try:
                sql_emenda = """
                    INSERT IGNORE INTO emenda (
                        codigoEmenda, ano, tipoEmenda, autor, nomeAutor, numeroEmenda,
                        localidadeDoGasto, funcao, subfuncao, valorEmpenhado,
                        valorLiquidado, valorPago, valorRestoInscrito, valorRestoCancelado, valorRestoPago
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """

                valores_emenda = (
                    codigo_emenda,
                    emenda.get("ano"),
                    emenda.get("tipoEmenda"),
                    emenda.get("autor"),
                    emenda.get("nomeAutor"),
                    emenda.get("numeroEmenda"),
                    emenda.get("localidadeDoGasto"),
                    emenda.get("funcao"),
                    emenda.get("subfuncao"),
                    converter_valor(emenda.get("valorEmpenhado")),
                    converter_valor(emenda.get("valorLiquidado")),
                    converter_valor(emenda.get("valorPago")),
                    converter_valor(emenda.get("valorRestoInscrito")),
                    converter_valor(emenda.get("valorRestoCancelado")),
                    converter_valor(emenda.get("valorRestoPago"))
                )

                cursor.execute(sql_emenda, valores_emenda)

                cursor.execute("SELECT idEmenda FROM emenda WHERE codigoEmenda = %s", (codigo_emenda,))
                resultado_emenda = cursor.fetchone()

                if not resultado_emenda:
                    continue

                id_emenda = resultado_emenda[0]
                contador_emendas += 1

                
                url_documentos = f"https://api.portaldatransparencia.gov.br/api-de-dados/emendas/documentos/{codigo_emenda}"
                
                time.sleep(SLEEP_SECONDS)
                response_documentos = requests.get(url_documentos, headers=headers, timeout=30)

                if response_documentos.status_code == 429:
                    print("Limite da API atingido nos documentos. Aguardando 60 segundos...")
                    time.sleep(60)
                    response_documentos = requests.get(url_documentos, headers=headers, timeout=30)

                if response_documentos.status_code == 200:
                    documentos = response_documentos.json()

                    if isinstance(documentos, dict):
                        documentos = [documentos]

                    for documento in documentos:
                        codigo_documento = documento.get("codigoDocumento")

                        if not codigo_documento:
                            continue

                        sql_documento = """
                            INSERT IGNORE INTO emendaDocumento (
                                idEmenda, idApi, codigoEmenda, data, fase,
                                codigoDocumento, codigoDocumentoResumido, especieTipo, tipoEmenda
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """

                        valores_documento = (
                            id_emenda,
                            documento.get("id"),
                            codigo_emenda,
                            converter_data(documento.get("data")),
                            documento.get("fase"),
                            codigo_documento,
                            documento.get("codigoDocumentoResumido"),
                            documento.get("especieTipo"),
                            documento.get("tipoEmenda")
                        )

                        cursor.execute(sql_documento, valores_documento)
                        contador_documentos += 1

            except Exception as e:
                print(f" Erro ao processar emenda {codigo_emenda}: {e}")
                db.rollback()

        
        salvar_checkpoint_transacao(script_checkpoint, pagina)
        db.commit()
        
        pagina += 1

    except KeyboardInterrupt:
        print(f"\n[!] Execução interrompida via teclado (Ctrl+C). A página atual ({pagina}) será reprocessada na próxima rodada.")
        break
    except Exception as e:
        print(f"Erro geral na página {pagina}: {e}")
        break

print("\n" + "=" * 50)
print("Importação de emendas concluída!")
print(f"Páginas lidas com sucesso até: {pagina - 1}")
print(f"Total de emendas nesta execução: {contador_emendas}")
print(f"Total de documentos nesta execução: {contador_documentos}")
print("=" * 50)

cursor.close()
db.close()
