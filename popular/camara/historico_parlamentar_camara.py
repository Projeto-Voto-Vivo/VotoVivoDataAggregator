"""Importa o histórico de status dos deputados (/deputados/{id}/historico) e o
converte em dois conjuntos de períodos:

- filiacaoPartidaria: em que partido o deputado esteve, e quando (trocas de
  partido são informação cidadã central em ano eleitoral);
- mandatoExercicio: quando efetivamente exerceu o mandato (posse, afastamentos,
  retornos) — base para taxas de presença justas e comparações normalizadas.

Depende de camara/parlamentar_camara.py.
"""

import sys
import time
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Historico_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'


def derivar_periodos(eventos):
    """Converte a linha do tempo de status em períodos de filiação e exercício.

    Filiação: abre quando siglaPartido muda; fecha na troca seguinte (a última
    fica aberta, dataFim NULL). Exercício: abre em situacao='Exercício'; fecha
    em qualquer outra situacao informada (Afastamento, Vacância, Suplência...).
    """
    eventos = sorted(eventos, key=lambda e: e.get('dataHora') or '')
    filiacoes, exercicios = [], []
    partido_atual, inicio_partido = None, None
    inicio_exercicio, participacao = None, None

    for ev in eventos:
        data = (ev.get('dataHora') or '')[:10] or None
        sigla = (ev.get('siglaPartido') or '').strip()
        situacao = (ev.get('situacao') or '').strip()

        if sigla and sigla != partido_atual:
            if partido_atual:
                filiacoes.append((partido_atual, inicio_partido, data))
            partido_atual, inicio_partido = sigla, data

        if situacao == 'Exercício':
            if inicio_exercicio is None:
                inicio_exercicio = data
                participacao = ev.get('condicaoEleitoral')
        elif situacao:
            if inicio_exercicio is not None:
                exercicios.append((inicio_exercicio, data, participacao))
                inicio_exercicio, participacao = None, None

    if partido_atual:
        filiacoes.append((partido_atual, inicio_partido, None))
    if inicio_exercicio is not None:
        exercicios.append((inicio_exercicio, None, participacao))

    return filiacoes, exercicios


def processar_historico_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "historico_camara_v1"
    execucao = ExecucaoEtl(conexao, nome_script)
    fila_erros = EtlErro(conexao, nome_script)

    cursor.execute("""
        SELECT idParlamentar, idApi, nomeUrna FROM parlamentar
        WHERE cargo = 'Deputado(a)' ORDER BY idParlamentar ASC
    """)
    deputados = cursor.fetchall()
    total = len(deputados)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    sucesso_total = True

    for i, (id_parlamentar, id_api, nome_urna) in enumerate(deputados, 1):
        if id_parlamentar <= ultimo_processado:
            continue

        logger.info(f"[{i}/{total}] Histórico de: {nome_urna}")
        resp = http_client.get_safe(f"{BASE_URL}/deputados/{id_api}/historico", headers={'accept': 'application/json'})
        garantir_conexao(conexao)

        if resp.status_code != 200:
            logger.error(f"Erro HTTP {resp.status_code} no histórico do deputado {id_api}")
            fila_erros.registrar(str(id_parlamentar), f"HTTP {resp.status_code}")
            execucao.incrementar(erros=1)
            sucesso_total = False
            continue

        try:
            eventos = resp.json().get('dados', [])
            filiacoes, exercicios = derivar_periodos(eventos)

            # Períodos são derivados do histórico completo: substituir em vez de
            # acumular mantém a tabela idempotente frente a correções na fonte.
            cursor.execute("DELETE FROM filiacaoPartidaria WHERE idParlamentar = %s", (id_parlamentar,))
            cursor.execute("DELETE FROM mandatoExercicio WHERE idParlamentar = %s", (id_parlamentar,))

            for sigla, inicio, fim in filiacoes:
                cursor.execute(
                    "INSERT IGNORE INTO filiacaoPartidaria (idParlamentar, siglaPartido, dataInicio, dataFim) VALUES (%s, %s, %s, %s)",
                    (id_parlamentar, sigla[:50], inicio, fim),
                )
            for inicio, fim, participacao in exercicios:
                if not inicio:
                    continue
                cursor.execute(
                    "INSERT IGNORE INTO mandatoExercicio (idParlamentar, dataInicio, dataFim, descricaoParticipacao) VALUES (%s, %s, %s, %s)",
                    (id_parlamentar, inicio, fim, participacao),
                )

            conexao.commit()
            execucao.incrementar(processados=1, registros=len(filiacoes) + len(exercicios))
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao salvar histórico do deputado {id_api}: {e}")
            fila_erros.registrar(str(id_parlamentar), e)
            execucao.incrementar(erros=1)
            sucesso_total = False

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
        time.sleep(0.2)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Histórico de deputados sincronizado com SUCESSO.")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total


if __name__ == "__main__":
    if not processar_historico_camara():
        sys.exit(1)
