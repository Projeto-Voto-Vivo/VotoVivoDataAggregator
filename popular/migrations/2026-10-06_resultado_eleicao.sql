-- Migração 2026-10-06 — resultados da eleição (popular/resultadoEleicao.py)
--   mysql -u <usuario> -p < popular/migrations/2026-10-06_resultado_eleicao.sql
USE votovivo;

-- Resultado da totalização do TSE por turno/cargo/abrangência
-- (popular/resultadoEleicao.py). uf = 'BR' é o total nacional e 'ZZ' o exterior,
-- ambos só para PRESIDENTE — que também tem uma linha por UF, então some
-- presidente por uf = 'BR' OU pelas UFs, nunca os dois. Os demais cargos só
-- existem por UF.
CREATE TABLE IF NOT EXISTS eleicaoResultado (
    idEleicaoResultado INT AUTO_INCREMENT PRIMARY KEY,
    anoEleicao INT NOT NULL,
    turno TINYINT NOT NULL,
    cargo VARCHAR(100) NOT NULL,
    uf CHAR(2) NOT NULL,
    vagas INT NULL,
    quocienteEleitoral INT NULL,
    eleitorado INT NULL,
    comparecimento INT NULL,
    abstencoes INT NULL,
    percentualComparecimento DECIMAL(7, 4) NULL,
    percentualAbstencao DECIMAL(7, 4) NULL,
    -- Senador com duas vagas: cada eleitor dá dois votos, logo votosTotais = 2x comparecimento
    votosTotais INT NULL,
    votosValidos INT NULL,
    votosNominais INT NULL,
    votosLegenda INT NULL,
    votosBrancos INT NULL,
    votosNulos INT NULL,
    votosAnuladosSubJudice INT NULL,
    percentualValidos DECIMAL(7, 4) NULL,
    percentualBrancos DECIMAL(7, 4) NULL,
    percentualNulos DECIMAL(7, 4) NULL,
    secoes INT NULL,
    secoesTotalizadas INT NULL,
    percentualSecoesTotalizadas DECIMAL(7, 4) NULL,
    totalizacaoFinal TINYINT(1) NOT NULL DEFAULT 0,
    haSegundoTurno TINYINT(1) NOT NULL DEFAULT 0,
    dataTotalizacao DATETIME NULL,
    dataGeracao DATETIME NULL,
    UNIQUE KEY unique_eleicao_resultado (anoEleicao, turno, cargo, uf)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- percentualVotos é sobre os votos válidos. eleito = conquistou a vaga
-- (situacao "Eleito", "Eleito por QP", "Eleito por média"); quem só passou ao
-- 2º turno fica com segundoTurno = 1 e eleito = 0.
CREATE TABLE IF NOT EXISTS eleicaoResultadoCandidato (
    idEleicaoResultadoCandidato INT AUTO_INCREMENT PRIMARY KEY,
    idEleicaoResultado INT NOT NULL,
    idCandidaturaTse INT NULL,
    sqCandidato VARCHAR(50) NOT NULL,
    numeroCandidato VARCHAR(20),
    nomeUrna VARCHAR(255),
    nomeCivil VARCHAR(255),
    siglaPartido VARCHAR(50),
    coligacao VARCHAR(500),
    nomeVice VARCHAR(255),
    posicao INT NULL,
    votos INT NULL,
    percentualVotos DECIMAL(7, 4) NULL,
    situacao VARCHAR(100),
    eleito TINYINT(1) NOT NULL DEFAULT 0,
    segundoTurno TINYINT(1) NOT NULL DEFAULT 0,
    situacaoVotos VARCHAR(100),
    FOREIGN KEY (idEleicaoResultado) REFERENCES eleicaoResultado(idEleicaoResultado) ON DELETE CASCADE,
    FOREIGN KEY (idCandidaturaTse) REFERENCES candidaturaTse(idCandidaturaTse) ON DELETE SET NULL,
    UNIQUE KEY unique_resultado_candidato (idEleicaoResultado, sqCandidato),
    INDEX idx_resultado_candidato_posicao (idEleicaoResultado, posicao),
    INDEX idx_resultado_candidato_sq (sqCandidato)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Cadeiras e votos de cada partido na disputa. A bancada nacional (Câmara,
-- Senado) é SUM(cadeiras) das linhas do cargo agrupadas por siglaPartido.
CREATE TABLE IF NOT EXISTS eleicaoResultadoPartido (
    idEleicaoResultadoPartido INT AUTO_INCREMENT PRIMARY KEY,
    idEleicaoResultado INT NOT NULL,
    siglaPartido VARCHAR(50) NOT NULL,
    numeroPartido VARCHAR(10),
    nomePartido VARCHAR(255),
    federacao VARCHAR(100) NULL,
    coligacao VARCHAR(500),
    cadeiras INT NOT NULL DEFAULT 0,
    votosNominais INT NULL,
    votosLegenda INT NULL,
    votos INT NULL,
    percentualVotos DECIMAL(7, 4) NULL,
    FOREIGN KEY (idEleicaoResultado) REFERENCES eleicaoResultado(idEleicaoResultado) ON DELETE CASCADE,
    UNIQUE KEY unique_resultado_partido (idEleicaoResultado, siglaPartido),
    INDEX idx_resultado_partido_sigla (siglaPartido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
