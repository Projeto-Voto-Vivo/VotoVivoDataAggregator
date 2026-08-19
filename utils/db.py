import logging
import os
import time

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("DB")


def get_connection(**cursor_kwargs):
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votovivo"),
    )
    return db, db.cursor(**cursor_kwargs)


def garantir_conexao(db, tentativas=3, espera_segundos=5):
    """Reconecta ao MySQL se a conexão caiu (wait_timeout, queda de rede).

    Chamar em pontos seguros: depois de uma espera longa (chamada HTTP, pausa de
    rate limit) e ANTES de abrir a transação do próximo lote — nunca no meio de
    uma transação, pois a reconexão descarta a transação em curso.
    """
    try:
        db.ping(reconnect=True, attempts=tentativas, delay=espera_segundos)
    except mysql.connector.Error as err:
        logger.warning(f"Ping/reconexão falhou ({err}); tentando reconectar manualmente...")
        for tentativa in range(1, tentativas + 1):
            try:
                db.reconnect(attempts=1, delay=0)
                logger.info("Reconexão com o MySQL restabelecida.")
                return
            except mysql.connector.Error as err_reconexao:
                logger.warning(f"Tentativa {tentativa}/{tentativas} de reconexão falhou: {err_reconexao}")
                time.sleep(espera_segundos)
        raise
