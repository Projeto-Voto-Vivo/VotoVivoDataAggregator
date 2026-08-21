"""Importa as despesas da Cota Parlamentar (CEAP) dos deputados a partir dos
ARQUIVOS OFICIAIS anuais da Câmara (www.camara.leg.br/cotas/Ano-{ano}.json.zip).

Um arquivo por ano (~9 MB zipado, ~100 mil lançamentos) substitui as milhares
de chamadas por deputado/mês à API /deputados/{id}/despesas — que além de
lentas ficaram indisponíveis (HTTP 200 com `dados: []` para tudo). O arquivo
traz numeroDeputadoID (= id do deputado na API) e idDocumento (chave natural).

Checkpoint = último ano concluído; ao concluir, o cursor é reposicionado em
ano_atual-1 para que execuções seguintes façam apenas o refresh do ano
corrente (o arquivo do ano em curso é atualizado pela Câmara continuamente).
"""

import hashlib
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Despesas_Camara")

URL_ARQUIVO = "https://www.camara.leg.br/cotas/Ano-{ano}.json.zip"
TAMANHO_LOTE = 1000

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))
ANO_INICIO = int(os.getenv("ANO_INICIO_ETL", 2023))
MES_INICIO = int(os.getenv("MES_INICIO_ETL", 1))

SQL_DESPESA = """
    INSERT INTO despesa
    (idApi, idParlamentar, dataDespesa, valor, fornecedorNome, fornecedorCnpjCpf, notaFiscalUrl, categoria)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        valor = VALUES(valor),
        fornecedorNome = VALUES(fornecedorNome),
        fornecedorCnpjCpf = VALUES(fornecedorCnpjCpf),
        notaFiscalUrl = VALUES(notaFiscalUrl),
        categoria = VALUES(categoria)
"""


def baixar_arquivo_anual(ano, ano_atual):
    """Baixa e descompacta o JSON anual da Cota. Devolve a lista de lançamentos,
    [] se o arquivo do ano corrente ainda não existe, ou None em falha real.
    Usa http_client.get (sem o cache de texto): o conteúdo é binário."""
    url = URL_ARQUIVO.format(ano=ano)
    logger.info(f"Baixando arquivo anual da Cota: {url}")
    resp = http_client.get(url, timeout=600)

    if resp.status_code == 404 and ano == ano_atual:
        logger.warning(f"Arquivo da Cota de {ano} ainda não publicado; seguindo sem ele.")
        return []
    if resp.status_code != 200:
        logger.error(f"Falha ao baixar {url} (HTTP {resp.status_code})")
        return None

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        nome = next((n for n in zf.namelist() if n.lower().endswith(".json")), zf.namelist()[0])
        dados = json.loads(zf.read(nome).decode("utf-8-sig"))

    lancamentos = dados.get("dados", []) if isinstance(dados, dict) else dados
    logger.info(f"   └─ {len(lancamentos)} lançamentos em {ano}.")
    return lancamentos


def converter_valor(valor):
    if valor is None or valor == "":
        return None
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def converter_data(data):
    if not data:
        return None
    texto = str(data).strip()[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def chave_natural_despesa(d):
    """idDocumento é o código do documento fiscal (o mesmo usado na URL do PDF e
    no codDocumento da API). Sem ele, hash determinístico dos campos estáveis."""
    id_doc = d.get("idDocumento")
    if id_doc:
        return f"CAM_{id_doc}"
    base = "|".join(str(v) for v in (
        d.get("numeroDeputadoID"), d.get("dataEmissao"), d.get("valorLiquido"),
        d.get("cnpjCPF"), d.get("numero"), d.get("descricao"),
    ))
    return "CAMH_" + hashlib.sha1(base.encode("utf-8")).hexdigest()


def processar_despesas_camara():
    db, cursor = get_connection()
    chk_manager = CheckpointManager(db)
    nome_script = "popular/despesas.py#camara_arquivos_v4"
    execucao = ExecucaoEtl(db, nome_script)
    fila_erros = EtlErro(db, nome_script)

    cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado(a)'")
    map_deputados = {str(r[0]): r[1] for r in cursor.fetchall()}
    logger.info(f"{len(map_deputados)} deputados carregados para vinculação.")

    ano_atual = datetime.now().year
    mes_atual = datetime.now().month
    try:
        ultimo_ano = int(chk_manager.obter(nome_script, str(ANO_INICIO - 1)))
    except ValueError:
        ultimo_ano = ANO_INICIO - 1

    sucesso_total = True
    interrompido = False
    start_time = time.time()
    total_gravado = 0

    for ano in range(max(ANO_INICIO, ultimo_ano + 1), ano_atual + 1):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            logger.warning(f"Tempo limite atingido; parando antes do ano {ano}.")
            interrompido = True
            break

        logger.info(f"=== Despesas da Câmara — ano {ano} ===")
        try:
            lancamentos = baixar_arquivo_anual(ano, ano_atual)
            if lancamentos is None:
                sucesso_total = False
                break
            if is_test_mode:
                lancamentos = lancamentos[:2000]

            mes_min = MES_INICIO if ano == ANO_INICIO else 1
            mes_max = mes_atual if ano == ano_atual else 12

            linhas, ignorados_sem_deputado, fora_janela = [], 0, 0
            for d in lancamentos:
                id_parlamentar = map_deputados.get(str(d.get("numeroDeputadoID") or ""))
                if not id_parlamentar:
                    ignorados_sem_deputado += 1   # lideranças, ex-deputados fora da base etc.
                    continue
                mes = int(d.get("mes") or 0)
                if mes and not (mes_min <= mes <= mes_max):
                    fora_janela += 1
                    continue
                linhas.append((
                    chave_natural_despesa(d),
                    id_parlamentar,
                    converter_data(d.get("dataEmissao")),
                    converter_valor(d.get("valorLiquido")),
                    (d.get("fornecedor") or None),
                    (str(d.get("cnpjCPF")).strip() or None) if d.get("cnpjCPF") else None,
                    (d.get("urlDocumento") or None),
                    (d.get("descricao") or None),
                ))

            garantir_conexao(db)
            for i in range(0, len(linhas), TAMANHO_LOTE):
                cursor.executemany(SQL_DESPESA, linhas[i:i + TAMANHO_LOTE])
                db.commit()

            total_gravado += len(linhas)
            logger.info(
                f"   └─ {len(linhas)} despesas gravadas/atualizadas "
                f"({ignorados_sem_deputado} de não-deputados ignoradas, {fora_janela} fora da janela)."
            )
            execucao.incrementar(processados=len(lancamentos), registros=len(linhas))
            chk_manager.salvar(nome_script, str(ano))

        except Exception as e:
            if db.in_transaction:
                db.rollback()
            logger.error(f"Erro ao processar despesas de {ano}: {e}")
            fila_erros.registrar(f"ano_{ano}", e)
            execucao.incrementar(erros=1)
            sucesso_total = False
            break

    if sucesso_total and not interrompido:
        chk_manager.salvar(nome_script, str(ano_atual - 1))
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info(f"Despesas da Câmara sincronizadas com SUCESSO ({total_gravado} lançamentos nesta execução).")
    elif interrompido:
        execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Importação terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    db.close()
    return sucesso_total or interrompido


if __name__ == "__main__":
    if not processar_despesas_camara():
        sys.exit(1)
