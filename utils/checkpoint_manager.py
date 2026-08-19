import logging

logger = logging.getLogger("CheckpointManager")

class CheckpointManager:
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = self.db.cursor()

    def obter(self, nome_script, default_value="1", reiniciar_se_concluido=False):
        query = "SELECT ultimoParametro, status FROM etlCheckpoint WHERE nomeScript = %s"
        self.cursor.execute(query, (nome_script,))
        resultado = self.cursor.fetchone()
        if not resultado:
            return default_value

        parametro, status = resultado
        if reiniciar_se_concluido and status == "CONCLUIDO":
            logger.info(f"Checkpoint de '{nome_script}' está CONCLUIDO; recomeçando do início (refresh completo).")
            return default_value

        logger.info(f"Checkpoint recuperado para '{nome_script}': {parametro} ({status})")
        return parametro

    def salvar(self, nome_script, parametro):
        query = """
            INSERT INTO etlCheckpoint (nomeScript, ultimoParametro, status)
            VALUES (%s, %s, 'EM_PROGRESSO')
            ON DUPLICATE KEY UPDATE
                ultimoParametro = VALUES(ultimoParametro),
                status = 'EM_PROGRESSO',
                dataAtualizacao = CURRENT_TIMESTAMP
        """
        self.cursor.execute(query, (nome_script, str(parametro)))
        self.db.commit()
        logger.debug(f"Checkpoint '{nome_script}' atualizado para: {parametro}")

    def concluir(self, nome_script):
        """Marca o script como concluído sem destruir o cursor de progresso.

        Um script concluído que for reexecutado com reiniciar_se_concluido=True
        recomeça do início (refresh completo, seguro porque as cargas são upserts).
        """
        query = """
            INSERT INTO etlCheckpoint (nomeScript, ultimoParametro, status)
            VALUES (%s, 'FIM', 'CONCLUIDO')
            ON DUPLICATE KEY UPDATE
                status = 'CONCLUIDO',
                dataAtualizacao = CURRENT_TIMESTAMP
        """
        self.cursor.execute(query, (nome_script,))
        self.db.commit()
        logger.info(f"Checkpoint '{nome_script}' marcado como CONCLUIDO.")
