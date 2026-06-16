import logging

logger = logging.getLogger("CheckpointManager")

class CheckpointManager:
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = self.db.cursor()

    def obter(self, nome_script, default_value="1"):
        query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
        self.cursor.execute(query, (nome_script,))
        resultado = self.cursor.fetchone()
        if resultado:
            logger.info(f"Checkpoint recuperado para '{nome_script}': {resultado[0]}")
            return resultado[0]
        return default_value

    def salvar(self, nome_script, parametro):
        query = """
            INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
            VALUES (%s, %s) 
            ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro), dataAtualizacao = CURRENT_TIMESTAMP
        """
        self.cursor.execute(query, (nome_script, parametro))
        self.db.commit()
        logger.debug(f"Checkpoint '{nome_script}' atualizado para: {parametro}")
