"""Importa a tramitação das matérias do Senado a partir da API nova de
processos (/processo/{id} -> autuacoes[].informesLegislativos[]).

O serviço legado /materia/movimentacoes/{codigo} foi DESATIVADO em 2026-02-01
(responde HTTP 200 com zero movimentações). A API nova é indexada pelo id do
PROCESSO, não pelo codigoMateria que guardamos em proposicao.idApi; a
resolução é feita em massa pelas listas anuais (/processo?ano=X, uma chamada
por ano, que trazem os dois identificadores), com fallback individual por
/processo?codigoMateria=X.
"""

import os
import time
from datetime import datetime
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.orgao_cache import OrgaoCache
from utils.paralelo import buscar_lote, em_lotes

logger = get_logger("ETL_Tramitacao_Senado")

BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"
HEADERS = {"Accept": "application/json"}
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Senado")

script_senado = "popular/tramitacao.py#senado_v2"
fila_erros = EtlErro(db, script_senado)
execucao = ExecucaoEtl(db, script_senado)

SQL_TRAMITACAO = """
    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
    VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        idOrgao = VALUES(idOrgao),
        dataHora = VALUES(dataHora),
        descricaoTramitacao = VALUES(descricaoTramitacao),
        descricaoSituacao = VALUES(descricaoSituacao),
        despacho = VALUES(despacho)
"""


def _truncar(valor, tamanho):
    if valor is None:
        return None
    valor = str(valor)
    return valor[:tamanho] if len(valor) > tamanho else valor


def mapear_codigo_materia_para_processo(ano_inicio, ano_atual):
    """codigoMateria -> idProcesso (e idProcesso -> idProcesso) a partir das
    listas anuais — uma chamada por ano cobre todas as matérias da base."""
    mapa = {}
    for ano in range(ano_inicio, ano_atual + 1):
        resp = http_client.get_safe(f"{BASE_URL_SENADO}/processo?ano={ano}&v=1", headers=HEADERS, timeout=300)
        if resp.status_code != 200:
            logger.warning(f"Lista anual {ano} indisponível (HTTP {resp.status_code}); matérias desse ano serão resolvidas uma a uma.")
            continue
        dados = resp.json()
        lista = dados if isinstance(dados, list) else dados.get("Processos", [])
        for pr in lista:
            id_processo = pr.get("id")
            if not id_processo:
                continue
            mapa[str(id_processo)] = str(id_processo)
            if pr.get("codigoMateria"):
                mapa[str(pr["codigoMateria"])] = str(id_processo)
    logger.info(f"{len(mapa)} identificadores de processo mapeados pelas listas anuais.")
    return mapa


def resolver_id_processo(codigo_materia, mapa):
    if codigo_materia in mapa:
        return mapa[codigo_materia]
    resp = http_client.get_safe(f"{BASE_URL_SENADO}/processo?codigoMateria={codigo_materia}&v=1", headers=HEADERS, timeout=60)
    if resp.status_code == 200:
        dados = resp.json()
        lista = dados if isinstance(dados, list) else dados.get("Processos", [])
        if lista and lista[0].get("id"):
            mapa[codigo_materia] = str(lista[0]["id"])
            return mapa[codigo_materia]
    return None


def buscar_processo(item):
    """item = (idProposicao, codigoMateria, idProcesso)."""
    _, _, id_processo = item
    return http_client.get_safe(f"{BASE_URL_SENADO}/processo/{id_processo}?v=1", headers=HEADERS, timeout=60)


def extrair_informes(detalhe):
    """Achata autuacoes[].informesLegislativos[] em ordem cronológica."""
    informes = []
    for autuacao in detalhe.get("autuacoes") or []:
        for inf in autuacao.get("informesLegislativos") or []:
            if inf.get("id"):
                informes.append(inf)
    informes.sort(key=lambda i: (i.get("data") or "", i.get("id")))
    return informes


def importar_tramitacao_senado():
    checkpoint_atual = int(chk_manager.obter(script_senado, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        WHERE p.casa = 'Senado' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila = cursor.fetchall()

    # Reprocesso: matérias que falharam em execuções anteriores voltam à fila
    # mesmo estando atrás do checkpoint.
    pendentes = {int(c) for c in fila_erros.listar_pendentes() if str(c).isdigit()}
    if pendentes:
        placeholders = ",".join(["%s"] * len(pendentes))
        cursor.execute(f"""
            SELECT p.idProposicao, p.idApi FROM proposicao p
            WHERE p.idProposicao IN ({placeholders}) AND p.idApi IS NOT NULL
        """, tuple(pendentes))
        fila = cursor.fetchall() + fila
        logger.info(f"{len(pendentes)} matérias com erro pendente serão reprocessadas.")

    if is_test_mode:
        fila = fila[:5]
    if not fila:
        logger.info("Nada a processar.")
        return

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    mapa_processo = mapear_codigo_materia_para_processo(ano_inicio, datetime.now().year)

    start_time = time.time()
    barra = tqdm(total=len(fila), desc="Tramitações Senado")

    # Fetch em paralelo por lote; gravação e checkpoint sequenciais, na ordem
    # original da fila (o checkpoint assume ordem crescente de idProposicao).
    for lote in em_lotes(fila):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
            break

        itens = []
        for id_interno, codigo_materia in lote:
            id_processo = resolver_id_processo(str(codigo_materia), mapa_processo)
            itens.append((id_interno, str(codigo_materia), id_processo))

        respostas = buscar_lote([it for it in itens if it[2]], buscar_processo)
        respostas_por_interno = {it[0]: r for it, r in zip([it for it in itens if it[2]], respostas)}
        garantir_conexao(db)

        for id_interno, codigo_materia, id_processo in itens:
            try:
                if not id_processo:
                    # Matéria sem processo resolvível: avança o cursor e registra
                    logger.warning(f"Matéria {codigo_materia} sem id de processo na API nova.")
                    fila_erros.registrar(id_interno, "idProcesso não resolvido")
                    if id_interno > checkpoint_atual:
                        chk_manager.salvar(script_senado, id_interno)
                    db.commit()
                    continue

                res = respostas_por_interno[id_interno]
                if isinstance(res, Exception):
                    raise res
                if res.status_code != 200:
                    if id_interno > checkpoint_atual:
                        chk_manager.salvar(script_senado, id_interno)
                    db.commit()
                    continue

                informes = extrair_informes(res.json())
                linhas = []
                for seq, inf in enumerate(informes, start=1):
                    colegiado = inf.get("colegiado") or {}
                    ente = inf.get("enteAdministrativo") or {}
                    id_orgao = orgaos.garantir(colegiado.get("codigo"), colegiado.get("sigla"), colegiado.get("nome"))
                    linhas.append((
                        f"SEN_{codigo_materia}_{inf['id']}",
                        id_interno,
                        id_orgao,
                        inf.get("data"),
                        seq,
                        _truncar(colegiado.get("nome") or ente.get("nome") or "Senado", 255),
                        _truncar(inf.get("siglaSituacaoIniciada"), 255),
                        inf.get("descricao"),
                    ))

                if linhas:
                    cursor.executemany(SQL_TRAMITACAO, linhas)

                # Um item reprocessado da fila de erros não pode regredir o cursor
                if id_interno > checkpoint_atual:
                    chk_manager.salvar(script_senado, id_interno)
                db.commit()
                if id_interno in pendentes:
                    fila_erros.resolver(id_interno)
                execucao.incrementar(processados=1, registros=len(linhas))

            except Exception as e:
                db.rollback()
                logger.error(f"Erro ao importar tramitações da matéria {id_interno} ({codigo_materia}): {e}")
                fila_erros.registrar(id_interno, e)
                execucao.incrementar(erros=1)

        barra.update(len(lote))

    barra.close()


if __name__ == "__main__":
    try:
        importar_tramitacao_senado()
        execucao.finalizar("SUCESSO")
    except KeyboardInterrupt:
        execucao.finalizar("INTERROMPIDO")
    finally:
        orgaos.fechar()
        cursor.close()
        db.close()
