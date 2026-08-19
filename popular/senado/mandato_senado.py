"""Importa mandatos dos senadores (/senador/{id}/mandatos) e persiste:

- mandatoExercicio: períodos de exercício efetivo (Exercicios/Exercicio) —
  usados pela presença do Senado e por comparações normalizadas;
- filiacaoPartidaria: filiações com DataFiliacao/DataDesfiliacao.

Depende de senado/parlamentar_senado.py. O script de votações/presenças do
Senado passa a ler os exercícios desta tabela em vez de consultar a API.
"""

import sys
import time
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Mandato_Senado")

BASE_URL = 'https://legis.senado.leg.br/dadosabertos'


def _como_lista(no):
    if not no:
        return []
    return no if isinstance(no, list) else [no]


def extrair_mandatos(dados):
    """Extrai (exercicios, filiacoes) do JSON de /senador/{id}/mandatos."""
    parlamentar = ((dados.get('MandatoParlamentar') or {}).get('Parlamentar') or {})
    mandatos = _como_lista((parlamentar.get('Mandatos') or {}).get('Mandato'))

    exercicios, filiacoes = [], []
    for m in mandatos:
        participacao = m.get('DescricaoParticipacao')
        for e in _como_lista((m.get('Exercicios') or {}).get('Exercicio')):
            inicio = e.get('DataInicio')
            if inicio:
                exercicios.append((inicio, e.get('DataFim'), participacao))
        for p in _como_lista((m.get('Partidos') or {}).get('Partido')):
            sigla = (p.get('Sigla') or '').strip()
            if sigla:
                filiacoes.append((sigla, p.get('DataFiliacao'), p.get('DataDesfiliacao')))
    return exercicios, filiacoes


def processar_mandatos_senado():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "mandato_senado_v1"
    execucao = ExecucaoEtl(conexao, nome_script)
    fila_erros = EtlErro(conexao, nome_script)

    cursor.execute("""
        SELECT idParlamentar, idApi, nomeUrna FROM parlamentar
        WHERE cargo = 'Senador(a)' ORDER BY idParlamentar ASC
    """)
    senadores = cursor.fetchall()
    total = len(senadores)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    sucesso_total = True

    for i, (id_parlamentar, id_api, nome_urna) in enumerate(senadores, 1):
        if id_parlamentar <= ultimo_processado:
            continue

        logger.info(f"[{i}/{total}] Mandatos de: {nome_urna}")
        resp = http_client.get_safe(f"{BASE_URL}/senador/{id_api}/mandatos", headers={'accept': 'application/json'})
        garantir_conexao(conexao)

        if resp.status_code != 200:
            logger.error(f"Erro HTTP {resp.status_code} nos mandatos do senador {id_api}")
            fila_erros.registrar(str(id_parlamentar), f"HTTP {resp.status_code}")
            execucao.incrementar(erros=1)
            sucesso_total = False
            continue

        try:
            exercicios, filiacoes = extrair_mandatos(resp.json())

            # Substituir em vez de acumular mantém a tabela idempotente
            cursor.execute("DELETE FROM mandatoExercicio WHERE idParlamentar = %s", (id_parlamentar,))
            cursor.execute("DELETE FROM filiacaoPartidaria WHERE idParlamentar = %s", (id_parlamentar,))

            for inicio, fim, participacao in exercicios:
                cursor.execute(
                    "INSERT IGNORE INTO mandatoExercicio (idParlamentar, dataInicio, dataFim, descricaoParticipacao) VALUES (%s, %s, %s, %s)",
                    (id_parlamentar, inicio, fim, participacao),
                )
            for sigla, inicio, fim in filiacoes:
                cursor.execute(
                    "INSERT IGNORE INTO filiacaoPartidaria (idParlamentar, siglaPartido, dataInicio, dataFim) VALUES (%s, %s, %s, %s)",
                    (id_parlamentar, sigla[:50], inicio, fim),
                )

            conexao.commit()
            execucao.incrementar(processados=1, registros=len(exercicios) + len(filiacoes))
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao salvar mandatos do senador {id_api}: {e}")
            fila_erros.registrar(str(id_parlamentar), e)
            execucao.incrementar(erros=1)
            sucesso_total = False

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
        time.sleep(0.2)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("Mandatos dos senadores sincronizados com SUCESSO.")
    else:
        execucao.finalizar("FALHA")
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total


if __name__ == "__main__":
    if not processar_mandatos_senado():
        sys.exit(1)
