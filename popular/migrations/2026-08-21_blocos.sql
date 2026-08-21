-- Migração 2026-08-21 — blocos/federações da Câmara e resolução de bancada
--   mysql -u <usuario> -p < popular/migrations/2026-08-21_blocos.sql
USE votovivo;

CREATE TABLE IF NOT EXISTS bloco (
    idBloco INT AUTO_INCREMENT PRIMARY KEY,
    idApi VARCHAR(50) NOT NULL,
    casa ENUM('Camara', 'Senado', 'Congresso') NOT NULL DEFAULT 'Camara',
    nome VARCHAR(255),
    idLegislatura INT NULL,
    federacao TINYINT(1) NOT NULL DEFAULT 0,
    UNIQUE KEY unique_bloco_api (idApi, casa)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS blocoPartido (
    idBlocoPartido INT AUTO_INCREMENT PRIMARY KEY,
    idBloco INT NOT NULL,
    siglaPartido VARCHAR(50) NOT NULL,
    idApiPartido VARCHAR(50) NULL,
    ordem INT NULL,
    FOREIGN KEY (idBloco) REFERENCES bloco(idBloco) ON DELETE CASCADE,
    UNIQUE KEY unique_bloco_partido (idBloco, siglaPartido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE orientacaoVotacao
    ADD COLUMN idBloco INT NULL AFTER orientacao,
    ADD COLUMN siglaPartido VARCHAR(50) NULL AFTER idBloco,
    ADD CONSTRAINT fk_orientacao_bloco FOREIGN KEY (idBloco) REFERENCES bloco(idBloco) ON DELETE SET NULL;

-- A resolução é feita pelo camara/orientacao_camara.py (passo pós-carga,
-- idempotente); reabrir o checkpoint faz a próxima execução recarregar e
-- resolver todas as orientações.
DELETE FROM etlCheckpoint WHERE nomeScript = 'orientacao_camara_v1';
