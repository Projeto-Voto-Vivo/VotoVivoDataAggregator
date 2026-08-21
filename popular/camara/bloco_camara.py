"""Importa blocos parlamentares e federações da Câmara (/blocos) com a
composição de partidos.

A composição é montada em duas fontes combinadas:
- o NOME do bloco ("UNIÃO, PP, PSD, Federação PSDB CIDADANIA, PODE"), que dá a
  ORDEM dos partidos — é essa ordem que a abreviação usada nas orientações
  segue ("Bl UniPpPsd...") — com entradas "Federação ..." expandidas pelos
  partidos da federação correspondente;
- o endpoint /blocos/{id}/partidos, que confirma/completa as siglas (para
  alguns blocos ele devolve vazio, por isso não é a única fonte).

Depende de partidos.py. Cobre todas as legislaturas que tocam a janela do ETL.
"""

import os
import sys
from datetime import datetime

from utils.bancadas import normalizar, parsear_nome_bloco
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Bloco_Camara")

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
HEADERS = {"accept": "application/json"}


def legislaturas_na_janela(ano_inicio):
    """Ids das legislaturas cujo período toca [ano_inicio-01-01, hoje]."""
    resp = http_client.get_safe(f"{BASE_URL}/legislaturas?itens=100&ordem=DESC", headers=HEADERS)
    if resp.status_code != 200:
        logger.warning("Não foi possível listar legislaturas; usando apenas a atual.")
        return []
    hoje = datetime.now().strftime("%Y-%m-%d")
    inicio = f"{ano_inicio}-01-01"
    return [
        int(l["id"]) for l in resp.json().get("dados", [])
        if (l.get("dataFim") or "9999") >= inicio and (l.get("dataInicio") or "0000") <= hoje
    ]


def listar_blocos(id_legislatura):
    blocos, pagina = [], 1
    while True:
        resp = http_client.get_safe(f"{BASE_URL}/blocos?idLegislatura={id_legislatura}&itens=100&pagina={pagina}", headers=HEADERS)
        if resp.status_code != 200:
            logger.error(f"Erro HTTP {resp.status_code} ao listar blocos da legislatura {id_legislatura}")
            return None
        dados = resp.json().get("dados", [])
        if not dados:
            break
        blocos += dados
        pagina += 1
    return blocos


def partidos_do_bloco(id_bloco_api):
    resp = http_client.get_safe(f"{BASE_URL}/blocos/{id_bloco_api}/partidos", headers=HEADERS)
    if resp.status_code != 200:
        return []
    return resp.json().get("dados", []) or []


def processar_blocos_camara():
    conexao, cursor = get_connection()
    nome_script = "bloco_camara_v1"
    execucao = ExecucaoEtl(conexao, nome_script)

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    legislaturas = legislaturas_na_janela(ano_inicio) or []
    if not legislaturas:
        resp = http_client.get_safe(f"{BASE_URL}/blocos?itens=1", headers=HEADERS)
        dados = resp.json().get("dados", []) if resp.status_code == 200 else []
        legislaturas = [int(dados[0]["idLegislatura"])] if dados else []
    logger.info(f"Legislaturas na janela: {legislaturas}")

    sucesso = True
    total_blocos = total_vinculos = 0

    for leg in legislaturas:
        blocos = listar_blocos(leg)
        if blocos is None:
            sucesso = False
            continue

        # Partidos de cada bloco pelo endpoint (confirma/completa siglas)
        partidos_por_bloco = {}
        for b in blocos:
            partidos_por_bloco[str(b["id"])] = partidos_do_bloco(b["id"])

        # Federações da legislatura, por nome normalizado -> siglas (para expandir
        # entradas "Federação ..." dentro do nome de um bloco)
        federacoes_por_nome = {}
        for b in blocos:
            if b.get("federacao"):
                siglas = [p["sigla"] for p in partidos_por_bloco[str(b["id"])] if p.get("sigla")]
                if not siglas:
                    siglas = [x for x in (b.get("nome") or "").split()[1:] if x.isupper()]
                federacoes_por_nome[normalizar(b.get("nome"))] = siglas

        garantir_conexao(conexao)
        try:
            for b in blocos:
                id_api = str(b["id"])
                cursor.execute("""
                    INSERT INTO bloco (idApi, casa, nome, idLegislatura, federacao)
                    VALUES (%s, 'Camara', %s, %s, %s)
                    ON DUPLICATE KEY UPDATE nome = VALUES(nome), idLegislatura = VALUES(idLegislatura), federacao = VALUES(federacao)
                """, (id_api, (b.get("nome") or "")[:255], int(b.get("idLegislatura") or leg), 1 if b.get("federacao") else 0))
                cursor.execute("SELECT idBloco FROM bloco WHERE idApi = %s AND casa = 'Camara'", (id_api,))
                id_bloco = cursor.fetchone()[0]

                # Composição ordenada a partir do nome, completada pelo endpoint
                ordenados = []
                for entrada in parsear_nome_bloco(b.get("nome"), federacoes_por_nome):
                    for sigla in entrada["siglas"]:
                        if sigla not in ordenados:
                            ordenados.append(sigla)
                extras = [p["sigla"] for p in partidos_por_bloco[id_api] if p.get("sigla") and p["sigla"] not in ordenados]
                id_api_partido = {p["sigla"]: str(p.get("id")) for p in partidos_por_bloco[id_api] if p.get("sigla")}

                cursor.execute("DELETE FROM blocoPartido WHERE idBloco = %s", (id_bloco,))
                linhas = [(id_bloco, s[:50], id_api_partido.get(s), i + 1) for i, s in enumerate(ordenados)]
                linhas += [(id_bloco, s[:50], id_api_partido.get(s), None) for s in extras]
                if linhas:
                    cursor.executemany(
                        "INSERT IGNORE INTO blocoPartido (idBloco, siglaPartido, idApiPartido, ordem) VALUES (%s, %s, %s, %s)",
                        linhas,
                    )
                total_blocos += 1
                total_vinculos += len(linhas)
                logger.info(f"  [{leg}] {'FED' if b.get('federacao') else 'BL '} {id_api} {b.get('nome')!r} -> {ordenados + extras}")
            conexao.commit()
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao gravar blocos da legislatura {leg}: {e}")
            sucesso = False

    execucao.incrementar(processados=total_blocos, registros=total_vinculos, erros=0 if sucesso else 1)
    execucao.finalizar("SUCESSO" if sucesso else "FALHA")
    logger.info(f"Blocos/federações: {total_blocos} gravados, {total_vinculos} vínculos partido-bloco.")
    cursor.close()
    conexao.close()
    return sucesso


if __name__ == "__main__":
    if not processar_blocos_camara():
        sys.exit(1)
