-- Migração 2026-10-05 — índices para as consultas de leitura do backend
--   mysql -u <usuario> -p < popular/migrations/2026-10-05_indices.sql
--
-- As FKs já são indexadas pelo InnoDB; o que faltava eram os índices compostos
-- e de filtro/ordenação usados pela API (listagens paginadas, agregações) e por
-- alguns passos do ETL. Um ALTER por tabela para reconstruir cada uma só uma vez.
-- Não é reexecutável: rodar duas vezes falha com "Duplicate key name".
USE votovivo;

-- Listagem por casa em ordem alfabética e filtros de partido/UF.
ALTER TABLE parlamentar
    ADD INDEX idx_parlamentar_cargo_nome (cargo, nomeUrna),
    ADD INDEX idx_parlamentar_partido (partidoAtual),
    ADD INDEX idx_parlamentar_uf (uf);

-- Listagem paginada em ordem de nomeUrna e filtros de eleição/UF/cargo/partido.
ALTER TABLE candidaturaTse
    ADD INDEX idx_candidatura_nome_urna (nomeUrna),
    ADD INDEX idx_candidatura_ano_uf_cargo (anoEleicao, uf, cargo),
    ADD INDEX idx_candidatura_partido (siglaPartido);

-- Listagem ordenada por ano (a PK vem implícita no índice e desempata),
-- filtros de casa/tipo/ano, contagens por ano e por situação, e o JOIN por
-- tipo+ano de relacionarProposicaoCasas.py.
ALTER TABLE proposicao
    ADD INDEX idx_proposicao_ano (ano),
    ADD INDEX idx_proposicao_casa_ano (casa, ano),
    ADD INDEX idx_proposicao_tipo_ano (idTipoProposicao, ano),
    ADD INDEX idx_proposicao_status (statusAtual);

-- Ranking por comissão compara nome OU sigla por igualdade. nome é
-- VARCHAR(1000): só cabe no índice com prefixo.
ALTER TABLE orgao
    ADD INDEX idx_orgao_sigla (sigla),
    ADD INDEX idx_orgao_nome (nome(191));

-- Listagem de votações e histórico de votos, ambos da mais recente para a mais antiga.
ALTER TABLE votacao
    ADD INDEX idx_votacao_data (dataHora);

-- Resolução de bancada do ETL (orientacao_camara.py) atualiza por siglaBancada.
ALTER TABLE orientacaoVotacao
    ADD INDEX idx_orientacao_bancada (siglaBancada);

-- Alinhamento: votos de um parlamentar filtrados por tipo de voto e ligados à
-- votação (índice cobre a consulta). Placar: contagem por voto de uma votação.
ALTER TABLE voto
    ADD INDEX idx_voto_parlamentar_voto (idParlamentar, votoRegistrado, idVotacao),
    ADD INDEX idx_voto_votacao_voto (idVotacao, votoRegistrado);

-- Despesas de um parlamentar por período, da mais recente para a mais antiga.
ALTER TABLE despesa
    ADD INDEX idx_despesa_parlamentar_data (idParlamentar, dataDespesa);

-- Linha do tempo da proposição (ORDER BY sequencia, dataHora) e o UPDATE por
-- idOrgao da fusão de órgãos do ETL — idOrgao não tem FK, logo não tinha índice.
ALTER TABLE tramitacao
    ADD INDEX idx_tramitacao_proposicao_seq (idProposicao, sequencia, dataHora),
    ADD INDEX idx_tramitacao_orgao (idOrgao);

-- Ranking e opções de filtro por função e por localidade do gasto.
ALTER TABLE emenda
    ADD INDEX idx_emenda_funcao (funcao),
    ADD INDEX idx_emenda_localidade (localidadeDoGasto);
