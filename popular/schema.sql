CREATE DATABASE votoVivo;
USE votoVivo;

CREATE TABLE parlamentar (
    idParlamentar INT AUTO_INCREMENT PRIMARY KEY,
    idApi INT UNIQUE NOT NULL,
    cargo VARCHAR(50),
    nomeCivil VARCHAR(255),
    nomeUrna VARCHAR(255),
    partidoAtual VARCHAR(50),
    uf CHAR(2),
    fotoUrl VARCHAR(500),
    dataNascimento DATE,
    email VARCHAR(255),
    telefone VARCHAR(20),
    enderecoGabinete VARCHAR(500)
);

CREATE TABLE tipoProposicao (
    idTipoProposicao INT AUTO_INCREMENT PRIMARY KEY,
    sigla VARCHAR(10) NOT NULL,
    nome VARCHAR(255) NOT NULL,
    casa ENUM('Camara', 'Senado', 'Congresso') NOT NULL,
    UNIQUE KEY unique_sigla_casa (sigla, casa)
);

CREATE TABLE proposicao (
    idProposicao INT AUTO_INCREMENT PRIMARY KEY,
    idApi INT UNIQUE NOT NULL,
    idTipoProposicao INT,
    numero VARCHAR(20),
    ano INT,
    ementa TEXT,
    statusAtual VARCHAR(255),
    FOREIGN KEY (idTipoProposicao)
        REFERENCES tipoProposicao(idTipoProposicao)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tema (
    idTema INT AUTO_INCREMENT PRIMARY KEY,
    codigoExterno INT NOT NULL, 
    casa ENUM('Camara', 'Senado') NOT NULL,
    descricao VARCHAR(255) NOT NULL,
    nivel ENUM('UNICO', 'GERAL', 'ESPECIFICO') DEFAULT 'UNICO',
    idTemaPai INT DEFAULT NULL,
    FOREIGN KEY (idTemaPai) REFERENCES tema(idTema),
    UNIQUE KEY unique_tema_casa (codigoExterno, casa, nivel)
);

CREATE TABLE IF NOT EXISTS temaProposicao (
    idProposicao INT NOT NULL,
    idTema INT NOT NULL,
    PRIMARY KEY (idProposicao, idTema),
    FOREIGN KEY (idProposicao) REFERENCES proposicao(idProposicao) ON DELETE CASCADE,
    FOREIGN KEY (idTema) REFERENCES tema(idTema) ON DELETE CASCADE
);

CREATE TABLE votacao ( 
    idVotacao INT AUTO_INCREMENT PRIMARY KEY,
    idApi VARCHAR(50) UNIQUE NOT NULL,
    idProposicao INT NULL,
    idOrgao INT NULL,
    dataHora DATETIME NULL,
    resumoMateria TEXT NULL,
    resultadoFinal VARCHAR(100) NULL,
    tipoVotacao ENUM('NOMINAL', 'SIMBOLICA', 'SECRETA') NULL,

    FOREIGN KEY (idProposicao)
        REFERENCES proposicao(idProposicao)
        ON DELETE SET NULL,

    FOREIGN KEY (idOrgao)
        REFERENCES orgao(idOrgao)
        ON DELETE SET NULL
);

CREATE TABLE voto (
    idVoto INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    idVotacao INT NOT NULL,
    idApi VARCHAR(50) UNIQUE NOT NULL,
    votoRegistrado ENUM('SIM', 'NAO', 'ABSTENCAO', 'AUSENTE'),

    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE,

    FOREIGN KEY (idVotacao)
        REFERENCES votacao(idVotacao)
        ON DELETE CASCADE
);

CREATE TABLE autoriaProposicao (
    idParlamentar INT NOT NULL,
    idProposicao INT NOT NULL,

    PRIMARY KEY (idParlamentar, idProposicao),

    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE,

    FOREIGN KEY (idProposicao)
        REFERENCES proposicao(idProposicao)
        ON DELETE CASCADE
);

CREATE TABLE redeSocial (
    idRedeSocial INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    plataforma VARCHAR(50),
    url VARCHAR(500),

    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE
);

CREATE TABLE despesa (
    idDespesa INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    dataDespesa DATE,
    valor DECIMAL(10, 2),
    fornecedorNome VARCHAR(255),
    fornecedorCnpjCpf VARCHAR(20),
    notaFiscalUrl VARCHAR(500),
    categoria VARCHAR(100),

    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE
);

CREATE TABLE tipoTramitacao (
    idTipoTramitacao INT AUTO_INCREMENT PRIMARY KEY,
    idApi INT UNIQUE NOT NULL,
    descricao VARCHAR(255),
    regime VARCHAR(100)
);

CREATE TABLE tramitacao (
    idTramitacao INT AUTO_INCREMENT PRIMARY KEY,

    idApi VARCHAR(50) NOT NULL,

    idProposicao INT NOT NULL,
    idTipoTramitacao INT,
    idOrgao INT,

    dataHora DATETIME,
    sequencia INT,

    descricaoTramitacao VARCHAR(255),
    descricaoSituacao VARCHAR(255),

    despacho TEXT,

    CONSTRAINT unique_tramitacao
        UNIQUE (idApi, sequencia),

    CONSTRAINT fk_tramitacao_proposicao
        FOREIGN KEY (idProposicao)
        REFERENCES proposicao(idProposicao)
        ON DELETE CASCADE,
    INDEX idx_despesa_parlamentar (idParlamentar),
    INDEX idx_despesa_data (dataDespesa)
);
