CREATE DATABASE IF NOT EXISTS votoVivo;
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
    enderecoGabinete VARCHAR(500),
    INDEX idx_parlamentar_idApi (idApi)
) ;

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
        ON DELETE SET NULL,
    INDEX idx_proposicao_idApi (idApi),
    INDEX idx_proposicao_tipo (idTipoProposicao)
);

CREATE TABLE votacao ( 
    idVotacao INT AUTO_INCREMENT PRIMARY KEY,
    idApi VARCHAR(50) UNIQUE NOT NULL,
    idProposicao INT NULL,
    dataVotacao DATE NULL,
    resumoMateria TEXT,
    resultadoFinal VARCHAR(100),
    tipoVotacao ENUM('NOMINAL', 'SIMBOLICA', 'SECRETA'),
    FOREIGN KEY (idProposicao) 
        REFERENCES proposicao(idProposicao) 
        ON DELETE SET NULL,
    INDEX idx_votacao_idApi (idApi),
    INDEX idx_votacao_data (dataVotacao),
    INDEX idx_votacao_proposicao (idProposicao)
);

CREATE TABLE voto (
    idVoto INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    idVotacao INT NOT NULL,
    idApi VARCHAR(100) UNIQUE NOT NULL,
    votoRegistrado ENUM('SIM', 'NAO', 'ABSTENCAO', 'AUSENTE'),
    FOREIGN KEY (idParlamentar) 
        REFERENCES parlamentar(idParlamentar) 
        ON DELETE CASCADE,
    FOREIGN KEY (idVotacao) 
        REFERENCES votacao(idVotacao) 
        ON DELETE CASCADE,
    INDEX idx_voto_parlamentar (idParlamentar),
    INDEX idx_voto_votacao (idVotacao)
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
        ON DELETE CASCADE,
    INDEX idx_autoria_prop (idProposicao)
);

CREATE TABLE redeSocial (
    idRedeSocial INT AUTO_INCREMENT PRIMARY KEY,
    idParlamentar INT NOT NULL,
    plataforma VARCHAR(50),
    url VARCHAR(500),
    FOREIGN KEY (idParlamentar) 
        REFERENCES parlamentar(idParlamentar) 
        ON DELETE CASCADE,
    INDEX idx_rede_parlamentar (idParlamentar)
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
        ON DELETE CASCADE,
    INDEX idx_despesa_parlamentar (idParlamentar),
    INDEX idx_despesa_data (dataDespesa)
);

CREATE TABLE IF NOT EXISTS emenda (
    idEmenda INT AUTO_INCREMENT PRIMARY KEY,
    codigoEmenda VARCHAR(100) NOT NULL UNIQUE,
    ano INT,
    tipoEmenda VARCHAR(100),
    autor VARCHAR(255),
    nomeAutor VARCHAR(255),
    numeroEmenda VARCHAR(100),
    localidadeDoGasto VARCHAR(255),
    funcao VARCHAR(255),
    subfuncao VARCHAR(255),
    valorEmpenhado DECIMAL(15, 2),
    valorLiquidado DECIMAL(15, 2),
    valorPago DECIMAL(15, 2),
    valorRestoInscrito DECIMAL(15, 2),
    valorRestoCancelado DECIMAL(15, 2),
    valorRestoPago DECIMAL(15, 2),

    INDEX idx_emenda_codigo (codigoEmenda),
    INDEX idx_emenda_ano (ano),
    INDEX idx_emenda_autor (autor),
    INDEX idx_emenda_tipo (tipoEmenda)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS emendaDocumento (
    idEmendaDocumento INT AUTO_INCREMENT PRIMARY KEY,
    idEmenda INT NOT NULL,
    idApi INT,
    codigoEmenda VARCHAR(100) NOT NULL,
    data DATE,
    fase VARCHAR(100),
    codigoDocumento VARCHAR(100),
    codigoDocumentoResumido VARCHAR(100),
    especieTipo VARCHAR(255),
    tipoEmenda VARCHAR(100),

    FOREIGN KEY (idEmenda)
        REFERENCES emenda(idEmenda)
        ON DELETE CASCADE,

    UNIQUE KEY unique_emenda_documento (idEmenda, codigoDocumento),
    INDEX idx_emenda_documento_id_emenda (idEmenda),
    INDEX idx_emenda_documento_codigo_emenda (codigoEmenda),
    INDEX idx_emenda_documento_data (data),
    INDEX idx_emenda_documento_fase (fase)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS emendaParlamentar (
    idEmendaParlamentar INT AUTO_INCREMENT PRIMARY KEY,
    idEmenda INT NOT NULL,
    codigoEmenda VARCHAR(100) NOT NULL,
    idParlamentar INT NOT NULL,

    nomeAutorPortal VARCHAR(255),
    nomeAutorNormalizado VARCHAR(255),

    metodoVinculo VARCHAR(100),
    confiancaVinculo DECIMAL(5, 2),

    FOREIGN KEY (idEmenda)
        REFERENCES emenda(idEmenda)
        ON DELETE CASCADE,

    FOREIGN KEY (idParlamentar)
        REFERENCES parlamentar(idParlamentar)
        ON DELETE CASCADE,

    UNIQUE KEY unique_emenda_parlamentar (
        idEmenda,
        idParlamentar
    ),

    INDEX idx_emenda_parlamentar_emenda (idEmenda),
    INDEX idx_emenda_parlamentar_codigo (codigoEmenda),
    INDEX idx_emenda_parlamentar_parlamentar (idParlamentar)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;