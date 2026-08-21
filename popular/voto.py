import time
import sys
import os

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.paralelo import buscar_lote, em_lotes

logger = get_logger("VotoETL")

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
logger.info("Conexão estabelecida para Votos da Câmara.")

chk_manager = CheckpointManager(db)

script_camara = "popular/voto.py#camara_logs_ausencia_justificada"
execucao = ExecucaoEtl(db, script_camara)

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado(a)'")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

SQL_VOTO = """
    INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
    VALUES (%s, %s, %s, %s)
"""


def mapear_voto(voto_txt):
    voto_txt = (voto_txt or "").strip().lower()
    if voto_txt == "sim":
        return "SIM"
    if voto_txt in ("não", "nao"):
        return "NAO"
    if "absten" in voto_txt:
        return "ABSTENCAO"
    if "obstru" in voto_txt:
        return "OBSTRUCAO"
    if any(p in voto_txt for p in ("justificad", "licença", "missão", "afastament")):
        return "AUSENCIA JUSTIFICADA"
    if voto_txt == "ausente" or "ausência" in voto_txt:
        return "AUSENTE"
    # "Artigo 17", "Branco", votações secretas
    return "NAO REGISTRADO"


def buscar_votos(item):
    id_api_votacao, _ = item
    return http_client.get_safe(f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{id_api_votacao}/votos", timeout=30)


def importar_votos_camara():
    logger.info("=" * 50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTOS DA CÂMARA")
    logger.info("=" * 50)

    cursor.execute("SELECT idApi, idVotacao FROM votacao WHERE casa = 'Camara' ORDER BY idVotacao ASC")
    votacoes = cursor.fetchall()
    checkpoint_atual = int(chk_manager.obter(script_camara, default_value="0"))
    total_votos = 0
    start_time = time.time()

    # Reprocesso: votações que falharam em execuções anteriores voltam à fila,
    # mesmo estando atrás do checkpoint.
    fila_erros = EtlErro(db, script_camara)
    pendentes = set(fila_erros.listar_pendentes())
    if pendentes:
        logger.info(f"{len(pendentes)} votações com erro pendente serão reprocessadas.")

    fila = [
        (id_api_votacao, id_votacao) for id_api_votacao, id_votacao in votacoes
        if id_votacao > checkpoint_atual or str(id_api_votacao) in pendentes
    ]
    logger.info(f"{len(fila)} votações na fila.")
    processadas = 0

    try:
        # Fetch em paralelo por lote; gravação e checkpoint sequenciais, em ordem.
        for lote in em_lotes(fila):
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                logger.warning("Tempo limite atingido para Votos da Câmara.")
                execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
                break

            respostas = buscar_lote(lote, buscar_votos)
            garantir_conexao(db)

            for (id_api_votacao, id_votacao), res in zip(lote, respostas):
                try:
                    if isinstance(res, Exception):
                        raise res
                    if res.status_code != 200:
                        continue

                    dados = res.json().get("dados", [])
                    batch = []
                    for v in dados:
                        id_dep_api = str((v.get("deputado_") or {}).get("id"))
                        if id_dep_api in map_parlamentares:
                            batch.append((
                                map_parlamentares[id_dep_api], id_votacao,
                                f"{id_api_votacao}_{id_dep_api}", mapear_voto(v.get("tipoVoto")),
                            ))

                    if batch:
                        cursor.executemany(SQL_VOTO, batch)
                        total_votos += len(batch)

                    # Um item reprocessado da fila de erros não pode regredir o cursor
                    if id_votacao > checkpoint_atual:
                        chk_manager.salvar(script_camara, id_votacao)
                    db.commit()
                    if str(id_api_votacao) in pendentes:
                        fila_erros.resolver(str(id_api_votacao))
                    execucao.incrementar(processados=1, registros=len(batch))

                except Exception as e:
                    logger.error(f"Erro ao buscar votos da votação {id_votacao}: {e}")
                    if db.in_transaction:
                        db.rollback()
                    fila_erros.registrar(str(id_api_votacao), e)
                    execucao.incrementar(erros=1)

            processadas += len(lote)
            if processadas % 200 < len(lote):
                logger.info(f"Processadas {processadas}/{len(fila)} votações...")

    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário.")
        if db.in_transaction:
            db.rollback()
        execucao.finalizar("INTERROMPIDO")
        sys.exit(0)

    execucao.finalizar("SUCESSO")
    logger.info(f"Concluído: {total_votos} votos da Câmara inseridos no total.")


if __name__ == "__main__":
    importar_votos_camara()
    cursor.close()
    db.close()
