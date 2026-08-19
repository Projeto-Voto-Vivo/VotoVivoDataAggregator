"""Importa a orientação das bancadas/lideranças em cada votação da Câmara.

Fonte: dump anual votacoesOrientacoes-{ano}.json (dadosabertos.camara.leg.br/arquivos).
Permite responder "o parlamentar votou conforme a orientação do seu partido?".
Depende de camara/votacao_camara.py (vincula por votacao.idApi).
"""

import os
import sys
import time
from datetime import datetime
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Orientacao_Camara")

BASE_ARQUIVOS = 'https://dadosabertos.camara.leg.br/arquivos'

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

    if sucesso_total and not interrompido:
        chk_manager.salvar(nome_script, str(ano_atual - 1))
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Orientações de bancada da Câmara sincronizadas com SUCESSO.")
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
