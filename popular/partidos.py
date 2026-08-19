"""Importa o catálogo de partidos (API da Câmara) para a tabela `partido`.

Serve de referência para filtros/comparações por partido e para o histórico de
filiações (filiacaoPartidaria referencia a sigla).
"""

import sys
import time

from utils.http_client import http_client
from utils.db import get_connection
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Partidos")

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"


def importar_partidos():
    db, cursor = get_connection()
    execucao = ExecucaoEtl(db, "popular/partidos.py")
    sucesso_total = True
    total = 0

    sql = """
        INSERT INTO partido (idApi, sigla, nome, urlLogo)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE idApi = VALUES(idApi), nome = VALUES(nome), urlLogo = VALUES(urlLogo)
    """

    try:
        pagina = 1
        while True:
            resp = http_client.get_safe(
                f"{BASE_URL}/partidos",
                params={"itens": 100, "pagina": pagina, "ordem": "ASC", "ordenarPor": "sigla"},
                headers={"accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.error(f"Erro ao listar partidos (HTTP {resp.status_code})")
                sucesso_total = False
                break

            partidos = resp.json().get("dados", [])
            if not partidos:
                break

            for p in partidos:
                id_api = p.get("id")
                detalhe = {}
                resp_detalhe = http_client.get_safe(f"{BASE_URL}/partidos/{id_api}", headers={"accept": "application/json"})
                if resp_detalhe.status_code == 200:
                    detalhe = resp_detalhe.json().get("dados", {})

                sigla = detalhe.get("sigla") or p.get("sigla")
                if not sigla:
                    continue

                cursor.execute(sql, (
                    str(id_api) if id_api else None,
                    sigla,
                    detalhe.get("nome") or p.get("nome"),
                    detalhe.get("urlLogo"),
                ))
                total += 1
                logger.info(f"[{total}] {sigla} — {detalhe.get('nome') or p.get('nome')}")
                time.sleep(0.1)

            db.commit()
            pagina += 1

        db.commit()
        execucao.incrementar(processados=total, registros=total)
        execucao.finalizar("SUCESSO" if sucesso_total else "FALHA")
        logger.info(f"Importação de partidos concluída: {total} registros.")
    except Exception as e:
        db.rollback()
        logger.error(f"Erro durante a importação de partidos: {e}")
        execucao.finalizar("FALHA", str(e))
        sucesso_total = False
    finally:
        cursor.close()
        db.close()

    return sucesso_total


if __name__ == "__main__":
    if not importar_partidos():
        sys.exit(1)
