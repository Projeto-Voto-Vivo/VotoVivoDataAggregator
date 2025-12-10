CREATE DATABASE IF NOT EXISTS votovivo;
USE votovivo;

CREATE TABLE IF NOT EXISTS Deputado (
    idDeputado      INT PRIMARY KEY,
    nomeCivil        VARCHAR(200) NOT NULL,
    cpf              VARCHAR(14)  UNIQUE,
    sexo             CHAR(1),
    dataNascimento   DATE,
    ufNascimento     CHAR(2),
    municipioNascimento VARCHAR(100),
    dataFalecimento  DATE,
    escolaridade     VARCHAR(100),
    urlDetalhes      VARCHAR(300),
    urlWebsite       VARCHAR(300)
);

CREATE TABLE IF NOT EXISTS RedeSocial (
    idRede INT PRIMARY KEY AUTO_INCREMENT,
    idDeputado INT NOT NULL,
    linkRedeSocial VARCHAR(300) NOT NULL,
    FOREIGN KEY (idDeputado) REFERENCES Deputado(idDeputado)
);

CREATE TABLE IF NOT EXISTS Despesa (
    idDeputado INT NOT NULL,
    codDocumento INT NOT NULL,
    ano INT,
    cnpjCpfFornecedor VARCHAR(20),
    codLote INT,
    codTipoDocumento INT,
    dataDocumento DATE,
    mes TINYINT,
    nomeFornecedor VARCHAR(200),
    numDocumento VARCHAR(50),
    numRessarcimento VARCHAR(50),
    parcela VARCHAR(20),
    tipoDespesa VARCHAR(200),
    tipoDocumento VARCHAR(200),
    urlDocumento VARCHAR(300),
    valorDocumento DECIMAL(10,2),
    valorGlosa DECIMAL(10,2),
    valorLiquido DECIMAL(10,2),
    
    PRIMARY KEY (idDeputado, codDocumento),
    FOREIGN KEY (idDeputado) REFERENCES Deputado(idDeputado)
);

CREATE TABLE IF NOT EXISTS Partido (
    idPartido INT PRIMARY KEY AUTO_INCREMENT,
    siglaPartido VARCHAR(300),
    uriPartido        VARCHAR(200),
    nome  VARCHAR(300) 
);

CREATE TABLE IF NOT EXISTS Gabinete (
    idGabinete INT PRIMARY KEY AUTO_INCREMENT,
    andar      VARCHAR(10),
    emailGabinete VARCHAR(150),
    nomeGabinete  VARCHAR(200),
    predio        VARCHAR(100),
    sala          VARCHAR(10),
    telefone      VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS Historico (
    idHistorico INT PRIMARY KEY AUTO_INCREMENT, 
    idDeputado INT NOT NULL,
    idPartido INT NOT NULL,
    idGabinete INT NOT NULL,
    
    uriHistorico VARCHAR(200),
    condicaoEleitoral VARCHAR(100),
    urlFoto VARCHAR(100),
    emailHistorico VARCHAR(100),
    dataHistorico DATE, 
    descricaoStatus VARCHAR(300),
    nomeParlamentar VARCHAR(200),
    situacao VARCHAR(100), 
    nomeEleitoral VARCHAR(200),
    siglaUF CHAR(2),
    
    UNIQUE KEY uk_historico (idDeputado, dataHistorico, situacao),

    FOREIGN KEY (idGabinete) REFERENCES Gabinete(idGabinete),
    FOREIGN KEY (idDeputado) REFERENCES Deputado(idDeputado),
    FOREIGN KEY (idPartido) REFERENCES Partido(idPartido)
);
