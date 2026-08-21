"""Importa a orientação das bancadas/lideranças em cada votação da Câmara e
RESOLVE cada bancada até bloco/federação (idBloco) ou partido (siglaPartido).

Fonte: dump anual votacoesOrientacoes-{ano}.json (dadosabertos.camara.leg.br/arquivos).
A `siglaBancada` do dump vem abreviada/truncada ("Bl UniPpPsd...", "Fdr PSDB-CIDADAN",
"Solidaried"); a resolução é determinística (ver utils/bancadas.py) e usa a
composição gravada por camara/bloco_camara.py — sem inferir nada de letras soltas.

Depende de camara/votacao_camara.py (vincula por votacao.idApi), partidos.py e
camara/bloco_camara.py.
"""

import os
import sys
import time
from datetime import datetime
from utils.bancadas import normalizar, parsear_nome_bloco, resolver_bloco, resolver_partido, tipo_bancada
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Orientacao_Camara")

BASE_ARQUIVOS = 'https://dadosabertos.camara.leg.br/arquivos'
BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))
TAMANHO_LOTE = 500


def baixar_dump_anual(ano, ano_atual):
    url = f"{BASE_ARQUIVOS}/votacoesOrientacoes/json/votacoesOrientacoes-{ano}.json"
    logger.info(f"Baixando dump anual: {url}")
    resp = http_client.get_safe(url, timeout=600)

    if resp.status_code == 404 and ano == ano_atual:
        logger.warning(f"Dump votacoesOrientacoes-{ano} ainda não publicado; seguindo sem ele.")
        return []
    if resp.status_code != 200:
        logger.error(f"Falha ao baixar {url} (HTTP {resp.status_code})")
        return None

    dados = resp.json().get('dados', [])
    logger.info(f"   └─ {len(dados)} registros em votacoesOrientacoes-{ano}.")
    return dados


# ---------------------------------------------------------
# RESOLUÇÃO DAS BANCADAS
# ---------------------------------------------------------
def carregar_legislaturas():
    """[(id, dataInicio, dataFim)] — para escolher os blocos da legislatura da votação."""
    resp = http_client.get_safe(f"{BASE_URL}/legislaturas?itens=100&ordem=DESC", headers={'accept': 'application/json'})
    if resp.status_code != 200:
        return []
    return [(int(l['id']), l.get('dataInicio') or '0000-00-00', l.get('dataFim') or '9999-12-31')
            for l in resp.json().get('dados', [])]


def carregar_blocos(cursor):
    """{idLegislatura: [ {idBloco, federacao, sequencia} ]} a partir de bloco/blocoPartido."""
    cursor.execute("SELECT idBloco, nome, idLegislatura, federacao FROM bloco WHERE casa = 'Camara'")
    blocos = cursor.fetchall()
    cursor.execute("SELECT idBloco, siglaPartido, ordem FROM blocoPartido ORDER BY idBloco, ordem IS NULL, ordem")
    partidos = {}
    for id_bloco, sigla, ordem in cursor.fetchall():
        partidos.setdefault(id_bloco, []).append(sigla)

    # federações por nome normalizado (por legislatura) para expandir "Federação ..." nos nomes
    federacoes = {}
    for id_bloco, nome, leg, fed in blocos:
        if fed:
            federacoes.setdefault(leg, {})[normalizar(nome)] = partidos.get(id_bloco, [])

    por_legislatura = {}
    for id_bloco, nome, leg, fed in blocos:
        if fed:
            sequencia = [{"federacao": False, "siglas": [s]} for s in partidos.get(id_bloco, [])]
        else:
            sequencia = parsear_nome_bloco(nome, federacoes.get(leg, {}))
        por_legislatura.setdefault(leg, []).append({"idBloco": id_bloco, "federacao": bool(fed), "sequencia": sequencia})
    return por_legislatura


def resolver_bancadas(conexao, cursor):
    """Preenche orientacaoVotacao.idBloco / siglaPartido para as linhas ainda não
    resolvidas. Idempotente; roda ao fim de toda execução."""
    legislaturas = carregar_legislaturas()
    blocos_por_leg = carregar_blocos(cursor)
    cursor.execute("SELECT sigla, nome FROM partido")
    partidos = cursor.fetchall()

    if not blocos_por_leg:
        logger.warning("Tabela bloco vazia — rode camara/bloco_camara.py para resolver bancadas de bloco/federação.")

    def legislatura_de(data):
        d = str(data)[:10]
        for leg, ini, fim in legislaturas:
            if ini <= d <= fim:
                return leg
        return None

    # bancadas pendentes, com a legislatura de cada votação em que aparecem
    cursor.execute("""
        SELECT DISTINCT o.siglaBancada, DATE(v.dataHora)
        FROM orientacaoVotacao o JOIN votacao v ON v.idVotacao = o.idVotacao
        WHERE o.idBloco IS NULL AND o.siglaPartido IS NULL
    """)
    pares = {}
    for sigla, data in cursor.fetchall():
        pares.setdefault((sigla, legislatura_de(data)), 0)

    resolvidas_bloco = resolvidas_partido = nao_resolvidas = 0
    nao_resolvidas_lista = set()
    for (sigla, leg) in pares:
        tipo = tipo_bancada(sigla)
        if tipo == "lideranca":
            continue
        if tipo in ("bloco", "federacao"):
            id_bloco = resolver_bloco(sigla, blocos_por_leg.get(leg, []))
            if id_bloco is None:
                nao_resolvidas += 1
                nao_resolvidas_lista.add(sigla)
                continue
            intervalo = next(((ini, fim) for l, ini, fim in legislaturas if l == leg), ("0000-00-00", "9999-12-31"))
            cursor.execute("""
                UPDATE orientacaoVotacao o JOIN votacao v ON v.idVotacao = o.idVotacao
                SET o.idBloco = %s
                WHERE o.siglaBancada = %s AND o.idBloco IS NULL
                  AND v.dataHora >= %s AND v.dataHora < DATE_ADD(%s, INTERVAL 1 DAY)
            """, (id_bloco, sigla, intervalo[0], intervalo[1]))
            resolvidas_bloco += 1
        else:
            sigla_partido = resolver_partido(sigla, partidos)
            if sigla_partido is None:
                nao_resolvidas += 1
                nao_resolvidas_lista.add(sigla)
                continue
            cursor.execute(
                "UPDATE orientacaoVotacao SET siglaPartido = %s WHERE siglaBancada = %s AND siglaPartido IS NULL",
                (sigla_partido, sigla),
            )
            resolvidas_partido += 1
    conexao.commit()

    logger.info(f"Bancadas resolvidas: {resolvidas_bloco} de bloco/federação, {resolvidas_partido} de partido; "
                f"{nao_resolvidas} não resolvidas.")
    if nao_resolvidas_lista:
        logger.info(f"   └─ não resolvidas (ficam NULL): {sorted(nao_resolvidas_lista)}")


# ---------------------------------------------------------
# CARGA
# ---------------------------------------------------------
def processar_orientacoes_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "orientacao_camara_v1"
    execucao = ExecucaoEtl(conexao, nome_script)
    fila_erros = EtlErro(conexao, nome_script)

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year

    # Cursor = último ano concluído; ao concluir, é reposicionado em ano_atual-1
    # para que execuções seguintes façam apenas o refresh do ano corrente.
    try:
        ultimo_ano = int(chk_manager.obter(nome_script, str(ano_inicio - 1)))
    except ValueError:
        ultimo_ano = ano_inicio - 1

    cursor.execute("SELECT idApi, idVotacao FROM votacao WHERE casa = 'Camara'")
    map_votacoes = {str(r[0]): r[1] for r in cursor.fetchall()}
    logger.info(f"{len(map_votacoes)} votações da Câmara carregadas para vinculação.")

    sql_orientacao = """
        INSERT INTO orientacaoVotacao (idVotacao, siglaBancada, orientacao)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE orientacao = VALUES(orientacao)
    """

    sucesso_total = True
    interrompido = False
    start_time = time.time()

    for ano in range(max(ano_inicio, ultimo_ano + 1), ano_atual + 1):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            logger.warning(f"Tempo limite atingido; parando antes do ano {ano}.")
            interrompido = True
            break

        try:
            registros = baixar_dump_anual(ano, ano_atual)
            if registros is None:
                sucesso_total = False
                break
            if is_test_mode:
                registros = registros[:2000]

            garantir_conexao(conexao)
            linhas = {}
            ignoradas = 0
            for r in registros:
                id_votacao = map_votacoes.get(str(r.get('idVotacao') or ''))
                sigla_bancada = (r.get('siglaBancada') or '').strip()
                if not id_votacao or not sigla_bancada:
                    ignoradas += 1
                    continue
                linhas[(id_votacao, sigla_bancada[:100])] = r.get('orientacao')

            batch = [(idv, sigla, ori) for (idv, sigla), ori in linhas.items()]
            for i in range(0, len(batch), TAMANHO_LOTE):
                cursor.executemany(sql_orientacao, batch[i:i + TAMANHO_LOTE])
                conexao.commit()

            if ignoradas:
                logger.info(f"   └─ {ignoradas} orientações sem votação correspondente na base (fora da janela).")
            logger.info(f"   └─ {len(batch)} orientações gravadas para {ano}.")
            execucao.incrementar(processados=len(registros), registros=len(batch))
            chk_manager.salvar(nome_script, str(ano))

        except Exception as e:
            if conexao.in_transaction:
                conexao.rollback()
            logger.error(f"Erro ao processar orientações do ano {ano}: {e}")
            fila_erros.registrar(f"ano_{ano}", e)
            execucao.incrementar(erros=1)
            sucesso_total = False
            break

    # Resolução das bancadas roda sempre (idempotente): cobre anos novos e
    # também linhas antigas que ficaram NULL por falta de bloco na época.
    try:
        garantir_conexao(conexao)
        resolver_bancadas(conexao, cursor)
    except Exception as e:
        if conexao.in_transaction:
            conexao.rollback()
        logger.error(f"Erro na resolução das bancadas: {e}")
        fila_erros.registrar("resolver_bancadas", e)
        execucao.incrementar(erros=1)
        sucesso_total = False

    if sucesso_total and not interrompido:
        chk_manager.salvar(nome_script, str(ano_atual - 1))
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Orientações de bancada da Câmara sincronizadas e resolvidas com SUCESSO.")
    elif interrompido:
        execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total or interrompido


if __name__ == "__main__":
    if not processar_orientacoes_camara():
        sys.exit(1)
