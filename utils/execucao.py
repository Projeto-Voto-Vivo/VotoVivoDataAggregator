import logging

logger = logging.getLogger("ExecucaoEtl")

class ExecucaoEtl:
    """Registra métricas de uma execução de script na tabela etlExecucao.

    Uso:
        execucao = ExecucaoEtl(db, "popular/voto.py#camara")
        execucao.incrementar(processados=1, registros=len(batch))
        ...
        execucao.finalizar("SUCESSO")   # ou "FALHA" / "INTERROMPIDO"

    Os contadores ficam em memória e são gravados só no finalizar(). Se o
    processo morrer sem finalizar, a linha fica com status EM_EXECUCAO — o que
    por si só sinaliza uma queda abrupta.

    IMPORTANTE: __init__ e finalizar() fazem commit na conexão. Chame-os fora
    de transações de dados em curso (início do script / após commit/rollback).
    """

    def __init__(self, db, nome_script):
        self.db = db
        self.cursor = db.cursor()
        self.nome_script = nome_script
        self.processados = 0
        self.registros = 0
        self.erros = 0
        self.id_execucao = None
        try:
            self.cursor.execute(
                "INSERT INTO etlExecucao (nomeScript, dataInicio) VALUES (%s, NOW())",
                (nome_script,),
            )
            self.db.commit()
            self.id_execucao = self.cursor.lastrowid
        except Exception as e:
            # Métricas nunca podem derrubar a carga principal
            logger.error(f"Falha ao abrir registro em etlExecucao: {e}")

    def incrementar(self, processados=0, registros=0, erros=0):
        self.processados += processados
        self.registros += registros
        self.erros += erros

    def finalizar(self, status="SUCESSO", detalhe=None):
        if self.id_execucao is None:
            return
        id_execucao, self.id_execucao = self.id_execucao, None  # nunca finalizar duas vezes
        try:
            self.cursor.execute("""
                UPDATE etlExecucao
                SET dataFim = NOW(), status = %s, itensProcessados = %s,
                    registrosGravados = %s, erros = %s, detalhe = %s
                WHERE idEtlExecucao = %s
            """, (status, self.processados, self.registros, self.erros,
                  (detalhe or "")[:500] or None, id_execucao))
            self.db.commit()
        except Exception as e:
            logger.error(f"Falha ao finalizar registro em etlExecucao: {e}")
