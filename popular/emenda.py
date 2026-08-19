import os
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

API_KEY = os.getenv("PORTAL_TRANSPARENCIA_API_KEY")
SLEEP_SECONDS = float(os.getenv("EMENDAS_SLEEP", "0.7"))

if not API_KEY:
    print("Erro: defina PORTAL_TRANSPARENCIA_API_KEY no .env")
    exit(1)

db, cursor = get_connection()
print("Conexão estabelecida para Emendas Parlamentares.")

chk_manager = CheckpointManager(db)

script_checkpoint = "popular/emenda.py#dinamico_v2"
fila_erros = EtlErro(db, script_checkpoint)
execucao = ExecucaoEtl(db, script_checkpoint)

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

ANO_INICIO = int(os.getenv("ANO_INICIO_ETL", 2025))
MES_INICIO = int(os.getenv("MES_INICIO_ETL", 5))
ANO_ATUAL = datetime.now().year
MES_ATUAL = datetime.now().month

ANOS_ESCOPO = list(range(ANO_INICIO, ANO_ATUAL + 1))

print("=" * 50)
print(f"Buscando emendas parlamentares ({MES_INICIO}/{ANO_INICIO} a {MES_ATUAL}/{ANO_ATUAL})...")
print("=" * 50)

checkpoint_atual = chk_manager.obter(script_checkpoint, default_value=f"{ANO_INICIO}_1")
ano_atual_chk, pagina = map(int, checkpoint_atual.split("_"))

contador_emendas = 0
contador_documentos = 0
start_time = time.time()
interrompido = False

pagina_limite_teste = pagina + 2

try:
    for ano in ANOS_ESCOPO:
        if ano < ano_atual_chk:
            continue
        if ano > ano_atual_chk:
            pagina = 1
            pagina_limite_teste = pagina + 2

        while True:
            if is_test_mode and pagina > pagina_limite_teste:
                print(f"\n[MODO TESTE] Parando na página {pagina} do ano {ano}.")
                interrompido = True
                break

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                print(f"\n[LIMITE DE TEMPO] Interrompido na página {pagina} do ano {ano}.")
                interrompido = True
                break

            url_emendas = "https://api.portaldatransparencia.gov.br/api-de-dados/emendas"
            parametros = {
                "ano": ano,
                "pagina": pagina
            }

            time.sleep(SLEEP_SECONDS)
            
            response = http_client.get_safe(url_emendas, headers=headers, params=parametros, timeout=30)

            print(f"\n[Fila] Lendo Ano: {ano} | Página: {pagina} | URL: {response.url}")
            print("Status:", response.status_code)

            if response.status_code != 200:
                print(f"Erro na página {pagina} do ano {ano}: {response.status_code}")
                interrompido = True
                break

            emendas = response.json()
            if not emendas:
                print(f"Não há mais emendas para o ano {ano}.")
                break

            print(f"Ano {ano} | Página {pagina}: {len(emendas)} emenda(s) encontrada(s)")

            for emenda in emendas:
                codigo_emenda = emenda.get("codigoEmenda")
                if not codigo_emenda:
                    continue

                try:
                    url_documentos = f"https://api.portaldatransparencia.gov.br/api-de-dados/emendas/documentos/{codigo_emenda}"
                    time.sleep(SLEEP_SECONDS)
                    
                    response_documentos = http_client.get_safe(url_documentos, headers=headers, timeout=30)

                    documentos_validos = []
                    if response_documentos.status_code == 200:
                        documentos = response_documentos.json()
                        if isinstance(documentos, dict):
                            documentos = [documentos]

                        for documento in documentos:
                            data_doc = converter_data(documento.get("data"))
                            if data_doc:
                                if ano == ANO_INICIO and data_doc.month < MES_INICIO:
                                    continue
                                if ano == ANO_ATUAL and data_doc.month > MES_ATUAL:
                                    continue
                                
                                documentos_validos.append((documento, data_doc))

                    if not documentos_validos and ano in ANOS_ESCOPO:
                        if ano == ANO_INICIO:
                            continue

                    garantir_conexao(db)
                    if db.in_transaction:
                        db.commit()
                    db.start_transaction()

                    sql_emenda = '''
                        INSERT INTO emenda (
                            codigoEmenda, ano, tipoEmenda, autor, nomeAutor, numeroEmenda,
                            localidadeDoGasto, funcao, subfuncao, valorEmpenhado,
                            valorLiquidado, valorPago, valorRestoInscrito, valorRestoCancelado, valorRestoPago
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            tipoEmenda = VALUES(tipoEmenda),
                            autor = VALUES(autor),
                            nomeAutor = VALUES(nomeAutor),
                            numeroEmenda = VALUES(numeroEmenda),
                            localidadeDoGasto = VALUES(localidadeDoGasto),
                            funcao = VALUES(funcao),
                            subfuncao = VALUES(subfuncao),
                            valorEmpenhado = VALUES(valorEmpenhado),
                            valorLiquidado = VALUES(valorLiquidado),
                            valorPago = VALUES(valorPago),
                            valorRestoInscrito = VALUES(valorRestoInscrito),
                            valorRestoCancelado = VALUES(valorRestoCancelado),
                            valorRestoPago = VALUES(valorRestoPago)
                    '''
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
                        db.commit()
                        continue

                    id_emenda = resultado_emenda[0]
                    contador_emendas += 1

                    for documento, data_doc in documentos_validos:
                        codigo_documento = documento.get("codigoDocumento")
                        if not codigo_documento:
                            continue

                        sql_documento = '''
                            INSERT IGNORE INTO emendaDocumento (
                                idEmenda, idApi, codigoEmenda, data, fase,
                                codigoDocumento, codigoDocumentoResumido, especieTipo, tipoEmenda
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        '''
                        valores_documento = (
                            id_emenda,
                            documento.get("id"),
                            codigo_emenda,
                            data_doc,
                            documento.get("fase"),
                            codigo_documento,
                            documento.get("codigoDocumentoResumido"),
                            documento.get("especieTipo"),
                            documento.get("tipoEmenda")
                        )
                        cursor.execute(sql_documento, valores_documento)
                        contador_documentos += 1

                    db.commit()
                    execucao.incrementar(processados=1, registros=1 + len(documentos_validos))

                except Exception as e:
                    print(f" Erro ao processar emenda {codigo_emenda}: {e}")
                    if db.in_transaction:
                        db.rollback()
                    fila_erros.registrar(codigo_emenda, e)
                    execucao.incrementar(erros=1)
            
            if db.in_transaction:
                db.commit()
            db.start_transaction()
            chk_manager.salvar(script_checkpoint, f"{ano}_{pagina}")
            db.commit()
            
            pagina += 1

    if not interrompido:
        chk_manager.salvar(script_checkpoint, f"{ANO_ATUAL}_1")
        print(f"\n[i] Carga completa. Próxima execução fará refresh do ano {ANO_ATUAL}.")

except KeyboardInterrupt:
    print(f"\n[!] Execução interrompida. O par {ano}_{pagina} será reprocessado.")
    execucao.finalizar("INTERROMPIDO")
except Exception as e:
    print(f"Erro geral no processamento: {e}")
    execucao.finalizar("FALHA", str(e))

execucao.finalizar("INTERROMPIDO" if interrompido else "SUCESSO")

print("\n" + "=" * 50)
print("Importação de emendas concluída!")
print(f"Total de emendas nesta execução: {contador_emendas}")
print(f"Total de documentos nesta execução: {contador_documentos}")
print("=" * 50)

cursor.close()
db.close()
