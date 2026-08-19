import hashlib
import os
import time
from datetime import datetime
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager

tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
print("[+] Conexão com o banco de dados estabelecida.\n")

chk_manager = CheckpointManager(db)

ANO_INICIO = int(os.getenv("ANO_INICIO_ETL", 2025))
MES_INICIO = int(os.getenv("MES_INICIO_ETL", 5))
ANO_ATUAL = datetime.now().year
MES_ATUAL = datetime.now().month

ANOS_BUSCA = list(range(ANO_INICIO, ANO_ATUAL + 1))

script_camara = "popular/despesas.py#camara_dinamico_v3"

# ORDER BY idParlamentar: o checkpoint compara ids na ordem da fila, então a
# ordenação precisa ser estável e crescente para a retomada funcionar.
cursor.execute("""
    SELECT idApi, idParlamentar FROM parlamentar
    WHERE cargo = 'Deputado(a)' ORDER BY idParlamentar ASC
""")
deputados = cursor.fetchall()

total_inserido = 0

def chave_natural_despesa(id_api_dep, d):
    """Chave natural do documento: codDocumento quando existe; caso contrário,
    um hash determinístico dos campos estáveis (idempotente entre execuções)."""
    cod_doc = d.get("codDocumento")
    if cod_doc:
        return f"CAM_{cod_doc}"
    base = "|".join(str(v) for v in (
        id_api_dep, d.get("dataDocumento"), d.get("valorLiquido"),
        d.get("cnpjCpfFornecedor"), d.get("numDocumento"), d.get("tipoDespesa"),
    ))
    return "CAMH_" + hashlib.sha1(base.encode("utf-8")).hexdigest()

def buscar_despesas_deputado(id_api_dep, ano, meses):
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_api_dep}/despesas"
    resultado = []
    for mes in meses:
        pagina = 1
        while True:
            params = {"ano": ano, "mes": mes, "itens": 100, "pagina": pagina}
            try:
                r = http_client.get_safe(url, params=params, timeout=20)
                if r.status_code != 200: break
                data = r.json()
                dados = data.get("dados", [])
                if not dados: break
                resultado.extend(dados)
                if not any(l["rel"] == "next" for l in data.get("links", [])): break
                pagina += 1
                time.sleep(0.1)
            except Exception:
                break
    return resultado

try:
    for ano in ANOS_BUSCA:
        print(f"\n--- INICIANDO PROCESSAMENTO DO ANO {ano} ---")

        mes_inicial_do_ano = MES_INICIO if ano == ANO_INICIO else 1
        mes_final_do_ano = MES_ATUAL if ano == ANO_ATUAL else 12
        meses_filtrados = list(range(mes_inicial_do_ano, mes_final_do_ano + 1))

        if not meses_filtrados:
            continue

        print(f"[CÂMARA] Analisando despesas para {len(deputados)} deputados...")
        checkpoint_camara_atual = chk_manager.obter(script_camara, default_value="0_0")
        ano_chk, id_interno_chk = map(int, checkpoint_camara_atual.split('_'))

        start_time = time.time()
        for id_api, id_interno in tqdm(deputados, desc=f"Deputados {ano}"):
            if ano < ano_chk: continue
            if ano == ano_chk and id_interno <= id_interno_chk: continue

            despesas = buscar_despesas_deputado(id_api, ano, meses_filtrados)
            batch = []
            for d in despesas:
                batch.append((
                    chave_natural_despesa(id_api, d),
                    id_interno, d.get("dataDocumento"), d.get("valorLiquido"),
                    d.get("nomeFornecedor"), d.get("cnpjCpfFornecedor"),
                    d.get("urlDocumento"), d.get("tipoDespesa"),
                ))

            if db.in_transaction: db.commit()
            db.start_transaction()

            if batch:
                cursor.executemany('''
                    INSERT IGNORE INTO despesa
                    (idApi, idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', batch)
                total_inserido += len(batch)

            chk_manager.salvar(script_camara, f"{ano}_{id_interno}")
            db.commit()

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                break

except KeyboardInterrupt:
    if db.in_transaction: db.rollback()
    print("\n[!] Execução interrompida pelo usuário via KeyboardInterrupt.")

print("\n" + "=" * 50)
print(f"IMPORTAÇÃO FINALIZADA: {total_inserido} novos registros salvos nesta chamada.")
print("=" * 50)
cursor.close()
db.close()
print("[+] Conexão encerrada com segurança. [FIM]")
