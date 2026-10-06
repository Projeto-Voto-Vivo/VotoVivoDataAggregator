-- Migração 2026-10-05 — índices para as consultas de leitura do backend
--   mysql -u <usuario> -p < popular/migrations/2026-10-05_indices.sql
--
-- As FKs já são indexadas pelo InnoDB; o que faltava eram os índices compostos
-- e de filtro/ordenação usados pela API (listagens paginadas, agregações) e por
-- alguns passos do ETL.
-- Reexecutável: cada índice só é criado se ainda não existir um com o mesmo nome
-- (o banco de produção já tinha alguns, e uma execução interrompida pode ser retomada).
USE votovivo;

DROP PROCEDURE IF EXISTS criar_indice;
DELIMITER //
CREATE PROCEDURE criar_indice(IN tabela VARCHAR(64), IN indice VARCHAR(64), IN colunas VARCHAR(255))
BEGIN
    -- BINARY evita "Illegal mix of collations" contra o information_schema
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND BINARY LOWER(table_name) = BINARY LOWER(tabela)
          AND BINARY LOWER(index_name) = BINARY LOWER(indice)
    ) THEN
        SET @sql_indice = CONCAT('ALTER TABLE `', tabela, '` ADD INDEX `', indice, '` (', colunas, ')');
        PREPARE comando FROM @sql_indice;
        EXECUTE comando;
        DEALLOCATE PREPARE comando;
    END IF;
END//
DELIMITER ;

-- Listagem por casa em ordem alfabética e filtros de partido/UF.
CALL criar_indice('parlamentar', 'idx_parlamentar_cargo_nome', 'cargo, nomeUrna');
CALL criar_indice('parlamentar', 'idx_parlamentar_partido', 'partidoAtual');
CALL criar_indice('parlamentar', 'idx_parlamentar_uf', 'uf');

-- Listagem paginada em ordem de nomeUrna e filtros de eleição/UF/cargo/partido.
CALL criar_indice('candidaturaTse', 'idx_candidatura_nome_urna', 'nomeUrna');
CALL criar_indice('candidaturaTse', 'idx_candidatura_ano_uf_cargo', 'anoEleicao, uf, cargo');
CALL criar_indice('candidaturaTse', 'idx_candidatura_partido', 'siglaPartido');

-- Listagem ordenada por ano (a PK vem implícita no índice e desempata),
-- filtros de casa/tipo/ano, contagens por ano e por situação, e o JOIN por
-- tipo+ano de relacionarProposicaoCasas.py.
CALL criar_indice('proposicao', 'idx_proposicao_ano', 'ano');
CALL criar_indice('proposicao', 'idx_proposicao_casa_ano', 'casa, ano');
CALL criar_indice('proposicao', 'idx_proposicao_tipo_ano', 'idTipoProposicao, ano');
CALL criar_indice('proposicao', 'idx_proposicao_status', 'statusAtual');

-- Ranking por comissão compara nome OU sigla por igualdade. nome é
-- VARCHAR(1000): só cabe no índice com prefixo.
CALL criar_indice('orgao', 'idx_orgao_sigla', 'sigla');
CALL criar_indice('orgao', 'idx_orgao_nome', 'nome(191)');

-- Listagem de votações e histórico de votos, ambos da mais recente para a mais antiga.
CALL criar_indice('votacao', 'idx_votacao_data', 'dataHora');

-- Resolução de bancada do ETL (orientacao_camara.py) atualiza por siglaBancada.
CALL criar_indice('orientacaoVotacao', 'idx_orientacao_bancada', 'siglaBancada');

-- Alinhamento: votos de um parlamentar filtrados por tipo de voto e ligados à
-- votação (índice cobre a consulta). Placar: contagem por voto de uma votação.
CALL criar_indice('voto', 'idx_voto_parlamentar_voto', 'idParlamentar, votoRegistrado, idVotacao');
CALL criar_indice('voto', 'idx_voto_votacao_voto', 'idVotacao, votoRegistrado');

-- Despesas de um parlamentar por período, da mais recente para a mais antiga.
CALL criar_indice('despesa', 'idx_despesa_parlamentar_data', 'idParlamentar, dataDespesa');

-- Linha do tempo da proposição (ORDER BY sequencia, dataHora) e o UPDATE por
-- idOrgao da fusão de órgãos do ETL — idOrgao não tem FK, logo não tinha índice.
CALL criar_indice('tramitacao', 'idx_tramitacao_proposicao_seq', 'idProposicao, sequencia, dataHora');
CALL criar_indice('tramitacao', 'idx_tramitacao_orgao', 'idOrgao');

-- Ranking e opções de filtro por função e por localidade do gasto.
CALL criar_indice('emenda', 'idx_emenda_funcao', 'funcao');
CALL criar_indice('emenda', 'idx_emenda_localidade', 'localidadeDoGasto');

DROP PROCEDURE criar_indice;
