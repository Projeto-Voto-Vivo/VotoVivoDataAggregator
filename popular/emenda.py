import requests
import mysql.connector
import time
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


API_KEY = os.getenv("PORTAL_TRANSPARENCIA_API_KEY")
ANO = os.getenv("EMENDAS_ANO", "2025")
SLEEP_SECONDS = float(os.getenv("EMENDAS_SLEEP", "0.7"))

if not API_KEY:
    print("Erro: defina PORTAL_TRANSPARENCIA_API_KEY no .env")
    exit(1)


db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo")
)
cursor = db.cursor()


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


pagina = 1
contador_emendas = 0
contador_documentos = 0

while True:
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
            print(response.text[:300])
            break

        emendas = response.json()

        if not emendas:
            print("Não há mais emendas para buscar.")
            break

        print(f"Página {pagina}: {len(emendas)} emenda(s) encontrada(s)")

        for emenda in emendas:
            codigo_emenda = emenda.get("codigoEmenda")

            if not codigo_emenda:
                print("Emenda ignorada sem codigoEmenda")
                continue

            try:
                sql_emenda = """
                    INSERT IGNORE INTO emenda (
                        codigoEmenda,
                        ano,
                        tipoEmenda,
                        autor,
                        nomeAutor,
                        numeroEmenda,
                        localidadeDoGasto,
                        funcao,
                        subfuncao,
                        valorEmpenhado,
                        valorLiquidado,
                        valorPago,
                        valorRestoInscrito,
                        valorRestoCancelado,
                        valorRestoPago
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
                contador_emendas += 1

                url_documentos = (
                    "https://api.portaldatransparencia.gov.br/api-de-dados/"
                    f"emendas/documentos/{codigo_emenda}"
                )

                time.sleep(SLEEP_SECONDS)

                response_documentos = requests.get(
                    url_documentos,
                    headers=headers,
                    timeout=30
                )

                if response_documentos.status_code == 429:
                    print("Limite da API atingido ao buscar documentos. Aguardando 60 segundos...")
                    time.sleep(60)

                    response_documentos = requests.get(
                        url_documentos,
                        headers=headers,
                        timeout=30
                    )

                if response_documentos.status_code == 200:
                    documentos = response_documentos.json()

                    if isinstance(documentos, dict):
                        documentos = [documentos]

                    for documento in documentos:
                        codigo_documento = documento.get("codigoDocumento")

                        if not codigo_documento:
                            print(f" Documento ignorado sem codigoDocumento na emenda {codigo_emenda}")
                            continue

                        sql_documento = """
                            INSERT IGNORE INTO emendaDocumento (
                                idApi,
                                codigoEmenda,
                                data,
                                fase,
                                codigoDocumento,
                                codigoDocumentoResumido,
                                especieTipo,
                                tipoEmenda
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """

                        valores_documento = (
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

                    print(
                        f" Emenda {codigo_emenda}: "
                        f"{len(documentos)} documento(s) encontrado(s)"
                    )

                else:
                    print(
                        f" Erro ao buscar documentos da emenda {codigo_emenda}: "
                        f"{response_documentos.status_code}"
                    )

                db.commit()

            except Exception as e:
                print(f" Erro ao processar emenda {codigo_emenda}: {e}")

        pagina += 1

    except Exception as e:
        print(f"Erro geral na página {pagina}: {e}")
        break


print("\n" + "=" * 50)
print("Importação de emendas concluída!")
print(f"Total de emendas processadas: {contador_emendas}")
print(f"Total de documentos processados: {contador_documentos}")
print("=" * 50)

cursor.close()
db.close()