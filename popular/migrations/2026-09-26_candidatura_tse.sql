-- Migração 2026-09-26 — candidaturas do TSE (popular/candidaturaTse.py)
--   mysql -u <usuario> -p < popular/migrations/2026-09-26_candidatura_tse.sql
USE votovivo;

-- idParlamentar é opcional: candidaturas sem parlamentar vinculado são mantidas.
CREATE TABLE IF NOT EXISTS candidaturaTse (
    idCandidaturaTse INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT,
    sqCandidato VARCHAR(50) NOT NULL,
    anoEleicao INT NOT NULL,
    descricaoEleicao VARCHAR(255),
    uf CHAR(2),
    cargo VARCHAR(100),
    numeroCandidato VARCHAR(20),
    nomeUrna VARCHAR(255),
    nomeCivil VARCHAR(255),
    siglaPartido VARCHAR(50),
    situacaoCandidatura VARCHAR(100),
    resultadoEleicao VARCHAR(100),
    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE,
    UNIQUE KEY unique_candidatura_ano_sq (anoEleicao, sqCandidato),
    INDEX idx_candidatura_parlamentar (idParlamentar),
    INDEX idx_candidatura_ano (anoEleicao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Caso a tabela já exista de uma versão anterior com idParlamentar NOT NULL.
ALTER TABLE candidaturaTse MODIFY idParlamentar INT NULL;
