import hashlib
import os
from datetime import datetime
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.execucao import ExecucaoEtl

db, cursor = get_connection()
print("[+] Conexão com o banco de dados estabelecida.\n")

chk_manager = CheckpointManager(db)

ANO_INICIO = int(os.getenv("ANO_INICIO_ETL", 2025))
MES_INICIO = int(os.getenv("MES_INICIO_ETL", 5))
ANO_ATUAL = datetime.now().year
MES_ATUAL = datetime.now().month

ANOS_BUSCA = list(range(ANO_INICIO, ANO_ATUAL + 1))

script_senado = "popular/despesas.py#senado_dinamico_v2"
execucao = ExecucaoEtl(db, script_senado)
interrompido = False

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Senador(a)'")
mapa_parlamentares = {str(p[0]): p[1] for p in cursor.fetchall()}

total_inserido = 0

def chave_natural_despesa(d):
    """Chave natural do documento: o campo `id` da API CEAPS; na ausência dele,
    um hash determinístico dos campos estáveis (idempotente entre execuções)."""
    id_doc = d.get("id")
    if id_doc:
        return f"SEN_{id_doc}"
    base = "|".join(str(v) for v in (
        d.get("codSenador"), d.get("data"), d.get("cpfCnpj"),
        d.get("valorReembolsado"), d.get("documento"), d.get("tipoDespesa"),
    ))
    return "SENH_" + hashlib.sha1(base.encode("utf-8")).hexdigest()

def processar_despesas_senado_em_bloco(ano):
    url = f"https://adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/{ano}"
    print(f" -> Baixando lote anual do Senado para o ano {ano}...")
    try:
        r = http_client.get_safe(url, timeout=90)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f" [!] Erro ao baixar lote do Senado ({ano}): {e}")
        return []

try:
    for ano in ANOS_BUSCA:
        print(f"\n--- INICIANDO PROCESSAMENTO DO ANO {ano} ---")

        mes_inicial_do_ano = MES_INICIO if ano == ANO_INICIO else 1
        mes_final_do_ano = MES_ATUAL if ano == ANO_ATUAL else 12
        meses_filtrados = list(range(mes_inicial_do_ano, mes_final_do_ano + 1))

        if not meses_filtrados:
            continue

        print(f"[SENADO] Analisando lote de despesas dos senadores...")
        checkpoint_senado_atual = chk_manager.obter(script_senado, default_value="0")

        if ano <= int(checkpoint_senado_atual):
            print(f" [i] Lote anual do Senado para {ano} já foi processado anteriormente nesta execução. Pulando.")
            continue

        lote_senado = processar_despesas_senado_em_bloco(ano)
        # A conexão pode ter caído durante o download do lote anual
        garantir_conexao(db)
        batch_senado = []

        if lote_senado:
            for d in tqdm(lote_senado, desc=f"Senadores {ano}"):
                id_api_sen = str(d.get("codSenador"))
                data_despesa_str = d.get("data")

                if data_despesa_str:
                    mes_despesa = int(data_despesa_str.split("-")[1])
                    if mes_despesa not in meses_filtrados:
                        continue

                if id_api_sen in mapa_parlamentares:
                    id_interno = mapa_parlamentares[id_api_sen]
                    batch_senado.append((
                        chave_natural_despesa(d),
                        id_interno, data_despesa_str, d.get("valorReembolsado"),
                        d.get("fornecedor"), d.get("cpfCnpj"), None, d.get("tipoDespesa"),
                    ))

            if db.in_transaction: db.commit()
            db.start_transaction()

            if batch_senado:
                cursor.executemany('''
                    INSERT IGNORE INTO despesa
                    (idApi, idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', batch_senado)
                total_inserido += len(batch_senado)

            chk_manager.salvar(script_senado, str(ano))
            db.commit()
            execucao.incrementar(processados=1, registros=len(batch_senado))

except KeyboardInterrupt:
    if db.in_transaction: db.rollback()
    print("\n[!] Execução interrompida pelo usuário via KeyboardInterrupt.")
    interrompido = True

execucao.finalizar("INTERROMPIDO" if interrompido else "SUCESSO")

print("\n" + "=" * 50)
print(f"IMPORTAÇÃO FINALIZADA: {total_inserido} novos registros salvos nesta chamada.")
print("=" * 50)
cursor.close()
db.close()
print("[+] Conexão encerrada com segurança. [FIM]")
