"""Vincula a MESMA matéria entre Câmara e Senado (tipoRelacao = MESMA_MATERIA).

Desde 2019 a numeração dos projetos é unificada entre as casas: um PL mantém a
mesma sigla/número/ano ao migrar de casa. Este script casa as proposições das
duas casas por (sigla, número, ano) — apenas para as siglas de numeração
unificada — e grava o vínculo nos dois sentidos em proposicaoRelacao,
conectando a jornada bicameral da lei.

Depende de camara/proposicao_camara.py e senado/proposicao_senado.py.
"""

import sys
from utils.db import get_connection
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Relacao_Casas")

# Siglas com numeração unificada entre as casas (Resolução CN nº 1/2019).
SIGLAS_UNIFICADAS = ('PL', 'PLP', 'PDL', 'PEC', 'MPV', 'PLV')

SQL_VINCULAR = """
    INSERT IGNORE INTO proposicaoRelacao (idProposicao, idProposicaoRelacionada, tipoRelacao)
    SELECT pc.idProposicao, ps.idProposicao, 'MESMA_MATERIA'
    FROM proposicao pc
    JOIN tipoProposicao tc ON pc.idTipoProposicao = tc.idTipoProposicao AND tc.casa = 'Camara'
    JOIN tipoProposicao ts ON ts.casa = 'Senado' AND ts.sigla = tc.sigla
    JOIN proposicao ps ON ps.idTipoProposicao = ts.idTipoProposicao
        AND ps.casa = 'Senado'
        AND ps.ano = pc.ano
        AND ps.numero IS NOT NULL AND pc.numero IS NOT NULL
        AND CAST(ps.numero AS UNSIGNED) = CAST(pc.numero AS UNSIGNED)
    WHERE pc.casa = 'Camara' AND tc.sigla IN ({siglas})
"""

SQL_VINCULAR_INVERSO = """
    INSERT IGNORE INTO proposicaoRelacao (idProposicao, idProposicaoRelacionada, tipoRelacao)
    SELECT idProposicaoRelacionada, idProposicao, 'MESMA_MATERIA'
    FROM proposicaoRelacao WHERE tipoRelacao = 'MESMA_MATERIA'
"""


def relacionar_casas():
    db, cursor = get_connection()
    execucao = ExecucaoEtl(db, "popular/relacionarProposicaoCasas.py")

    try:
        placeholders = ", ".join(["%s"] * len(SIGLAS_UNIFICADAS))
        cursor.execute(SQL_VINCULAR.format(siglas=placeholders), SIGLAS_UNIFICADAS)
        novos = cursor.rowcount
        cursor.execute(SQL_VINCULAR_INVERSO)
        novos += cursor.rowcount
        db.commit()

        cursor.execute("SELECT COUNT(*) FROM proposicaoRelacao WHERE tipoRelacao = 'MESMA_MATERIA'")
        total = cursor.fetchone()[0]
        logger.info(f"Vínculos MESMA_MATERIA: {novos} novos nesta execução; {total} no total.")
        execucao.incrementar(processados=total, registros=novos)
        execucao.finalizar("SUCESSO")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao vincular matérias entre as casas: {e}")
        execucao.finalizar("FALHA", str(e))
        return False
    finally:
        cursor.close()
        db.close()


if __name__ == "__main__":
    if not relacionar_casas():
        sys.exit(1)
