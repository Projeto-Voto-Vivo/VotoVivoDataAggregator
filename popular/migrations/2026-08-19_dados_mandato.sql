-- Migração 2026-08-19 (parte 4) — novas tabelas de contexto político e recarga
-- da janela completa do mandato (ANO_INICIO_ETL=2023).
-- Aplicar DEPOIS das partes 1, 2 e 3:
--   mysql -u <usuario> -p < popular/migrations/2026-08-19_dados_mandato.sql
USE votovivo;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Novas tabelas: partidos, filiações, exercícios de mandato, orientação de
--    bancada e relações entre proposições.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partido (
    idPartido INT AUTO_INCREMENT PRIMARY KEY,
    idApi VARCHAR(50) NULL,
    sigla VARCHAR(50) UNIQUE NOT NULL,
    nome VARCHAR(255),
    urlLogo VARCHAR(500) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS filiacaoPartidaria (
    idFiliacao INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    siglaPartido VARCHAR(50) NOT NULL,
    dataInicio DATE NULL,
    dataFim DATE NULL,
    FOREIGN KEY (idParlamentar) REFERENCES parlamentar(idParlamentar) ON DELETE CASCADE,
    UNIQUE KEY unique_filiacao (idParlamentar, siglaPartido, dataInicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mandatoExercicio (
    idMandatoExercicio INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    dataInicio DATE NOT NULL,
    dataFim DATE NULL,
    descricaoParticipacao VARCHAR(100) NULL,
    FOREIGN KEY (idParlamentar) REFERENCES parlamentar(idParlamentar) ON DELETE CASCADE,
    UNIQUE KEY unique_exercicio (idParlamentar, dataInicio)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS orientacaoVotacao (
    idOrientacaoVotacao INT AUTO_INCREMENT PRIMARY KEY,
    idVotacao INT NOT NULL,
    siglaBancada VARCHAR(100) NOT NULL,
    orientacao VARCHAR(50) NULL,
    FOREIGN KEY (idVotacao) REFERENCES votacao(idVotacao) ON DELETE CASCADE,
    UNIQUE KEY unique_orientacao_votacao (idVotacao, siglaBancada)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS proposicaoRelacao (
    idProposicaoRelacao INT AUTO_INCREMENT PRIMARY KEY,
    idProposicao INT NOT NULL,
    idProposicaoRelacionada INT NOT NULL,
    tipoRelacao ENUM('PRINCIPAL', 'ANTERIOR', 'POSTERIOR', 'MESMA_MATERIA') NOT NULL,
    FOREIGN KEY (idProposicao) REFERENCES proposicao(idProposicao) ON DELETE CASCADE,
    FOREIGN KEY (idProposicaoRelacionada) REFERENCES proposicao(idProposicao) ON DELETE CASCADE,
    UNIQUE KEY unique_proposicao_relacao (idProposicao, idProposicaoRelacionada, tipoRelacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Recarga da janela completa do mandato (2023+): os checkpoints abaixo
--    guardam cursores baseados na janela antiga (2025/5) e impediriam a carga
--    dos períodos anteriores. Apagá-los faz a próxima execução do pipeline
--    preencher 2023-2024 — as cargas são upserts idempotentes, nada duplica.
--    (Os demais scripts recarregam sozinhos: seus checkpoints CONCLUIDO
--    reiniciam do zero na próxima execução.)
-- ────────────────────────────────────────────────────────────────────────────
DELETE FROM etlCheckpoint WHERE nomeScript IN (
    'popular/despesas.py#camara_dinamico_v3',
    'popular/despesas.py#senado_dinamico_v2',
    'popular/votacao.py#camara_logs',
    'popular/emenda.py#dinamico_v2',
    'proposicao_camara_v4',
    'proposicao_senado_v2'
);
