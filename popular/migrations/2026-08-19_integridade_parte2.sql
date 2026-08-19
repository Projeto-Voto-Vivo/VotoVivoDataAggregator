-- Migração 2026-08-19 (parte 2) — chave natural de despesa e fila de erros
-- Aplicar DEPOIS de 2026-08-19_integridade.sql:
--   mysql -u <usuario> -p < popular/migrations/2026-08-19_integridade_parte2.sql
USE votovivo;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. etlErro: fila de erros (dead-letter). Itens que falham durante uma carga
--    ficam registrados para reprocesso em vez de serem perdidos.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS etlErro (
    idEtlErro INT AUTO_INCREMENT PRIMARY KEY,
    nomeScript VARCHAR(100) NOT NULL,
    chaveItem VARCHAR(255) NOT NULL,
    erro TEXT,
    payload MEDIUMTEXT NULL,
    tentativas INT NOT NULL DEFAULT 1,
    resolvido TINYINT(1) NOT NULL DEFAULT 0,
    dataPrimeiroErro DATETIME DEFAULT CURRENT_TIMESTAMP,
    dataUltimoErro DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_erro_item (nomeScript, chaveItem),
    INDEX idx_etl_erro_pendentes (nomeScript, resolvido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. despesa: chave natural do documento (codDocumento na Câmara, id no Senado).
--
--    ATENÇÃO: as despesas antigas foram importadas SEM chave natural, então não
--    há como deduplicá-las com segurança nem casá-las com as reimportações.
--    Esta migração APAGA a tabela despesa e reseta os checkpoints de despesas
--    para que a próxima execução do pipeline reimporte tudo de forma limpa e
--    idempotente. A tabela não tem dependentes (nenhuma FK aponta para ela).
-- ────────────────────────────────────────────────────────────────────────────
TRUNCATE TABLE despesa;

ALTER TABLE despesa
    ADD COLUMN idApi VARCHAR(100) NOT NULL AFTER idDespesa,
    ADD UNIQUE KEY unique_despesa_api (idApi);

DELETE FROM etlCheckpoint WHERE nomeScript IN (
    'popular/despesas.py#camara_dinamico_v2',
    'popular/despesas.py#camara_dinamico_v3',
    'popular/despesas.py#senado_dinamico_v2'
);
