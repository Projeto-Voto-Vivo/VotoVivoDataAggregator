import logging

logger = logging.getLogger("EtlErro")

class EtlErro:
    """Fila de erros (dead-letter) persistida na tabela etlErro.

    Uso típico num script de carga:
        erros = EtlErro(db, "popular/voto.py#camara")
        pendentes = erros.listar_pendentes()      # chaves que falharam antes
        ...
        erros.registrar(chave, exc)               # dentro do except do item
        erros.resolver(chave)                     # quando o item finalmente passa

    IMPORTANTE: registrar()/resolver() fazem commit na conexão. Chame-os DEPOIS
    do rollback da transação de dados que falhou, nunca no meio dela.
    """

    def __init__(self, db_connection, nome_script):
        self.db = db_connection
        self.cursor = self.db.cursor()
        self.nome_script = nome_script

    def registrar(self, chave_item, erro, payload=None):
        try:
            self.cursor.execute("""
                INSERT INTO etlErro (nomeScript, chaveItem, erro, payload)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    erro = VALUES(erro),
                    payload = VALUES(payload),
                    tentativas = tentativas + 1,
                    resolvido = 0
            """, (self.nome_script, str(chave_item)[:255], str(erro), payload))
            self.db.commit()
        except Exception as e:
            # A fila de erros nunca pode derrubar a carga principal
            logger.error(f"Falha ao registrar erro de '{chave_item}' na etlErro: {e}")

    def resolver(self, chave_item):
        try:
            self.cursor.execute(
                "UPDATE etlErro SET resolvido = 1 WHERE nomeScript = %s AND chaveItem = %s",
                (self.nome_script, str(chave_item)[:255]),
            )
            self.db.commit()
        except Exception as e:
            logger.error(f"Falha ao resolver erro de '{chave_item}' na etlErro: {e}")

    def listar_pendentes(self):
        self.cursor.execute(
            "SELECT chaveItem FROM etlErro WHERE nomeScript = %s AND resolvido = 0",
            (self.nome_script,),
        )
        return [row[0] for row in self.cursor.fetchall()]
