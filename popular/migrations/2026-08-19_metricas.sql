-- Migração 2026-08-19 (parte 3) — tabela de métricas por execução
-- Aplicar DEPOIS das partes 1 e 2:
--   mysql -u <usuario> -p < popular/migrations/2026-08-19_metricas.sql
USE votovivo;

CREATE TABLE IF NOT EXISTS etlExecucao (
    idEtlExecucao INT AUTO_INCREMENT PRIMARY KEY,
    nomeScript VARCHAR(100) NOT NULL,
    dataInicio DATETIME NOT NULL,
    dataFim DATETIME NULL,
    status ENUM('EM_EXECUCAO', 'SUCESSO', 'FALHA', 'INTERROMPIDO') NOT NULL DEFAULT 'EM_EXECUCAO',
    itensProcessados INT NOT NULL DEFAULT 0,
    registrosGravados INT NOT NULL DEFAULT 0,
    erros INT NOT NULL DEFAULT 0,
    detalhe VARCHAR(500) NULL,
    INDEX idx_etl_execucao_script (nomeScript, dataInicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
