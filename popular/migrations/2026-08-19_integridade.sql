-- Migração 2026-08-19 — correções de integridade
-- Aplicar em bancos criados com a versão anterior do schema.sql:
--   mysql -u <usuario> -p < popular/migrations/2026-08-19_integridade.sql
USE votovivo;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. etlCheckpoint: separa o estado (EM_PROGRESSO/CONCLUIDO) do cursor de
--    progresso. Antes o valor "CONCLUIDO_<data>" era gravado no próprio cursor,
--    o que quebrava a lógica de retomada dos scripts.
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE etlCheckpoint
    ADD COLUMN status ENUM('EM_PROGRESSO', 'CONCLUIDO') NOT NULL DEFAULT 'EM_PROGRESSO'
    AFTER ultimoParametro;

UPDATE etlCheckpoint
SET status = 'CONCLUIDO'
WHERE ultimoParametro LIKE 'CONCLUIDO%';

-- Os checkpoints antigos abaixo usavam cursores com semântica incompatível com
-- a nova (comparação lexicográfica sobre idApi em listas não ordenadas). Os
-- scripts novos usam nomes versionados (_v2/_v3), então os antigos viram lixo:
DELETE FROM etlCheckpoint WHERE nomeScript IN (
    'parlamentar_camara_v1',
    'parlamentar_senado_v1',
    'proposicao_camara_v2',
    'proposicao_senado_v1',
    'orgao_camara_v1',
    'orgao_senado_v1',
    'evento_presenca_camara',
    'votacao_presenca_senado',
    'popular/despesas.py#camara_dinamico_v2'
);

-- ────────────────────────────────────────────────────────────────────────────
-- 2. voto: o ENUM não continha os valores que o ETL da Câmara gerava
--    ("AUSENCIA JUSTIFICADA", "AUSENTE", "SEM REGISTRO"); com INSERT IGNORE o
--    MySQL gravou '' silenciosamente. Remove os registros corrompidos, amplia
--    o ENUM e reseta o checkpoint de votos para os reimportar (os votos válidos
--    são preservados — a reimportação usa INSERT IGNORE).
-- ────────────────────────────────────────────────────────────────────────────
DELETE FROM voto WHERE votoRegistrado = '';

ALTER TABLE voto
    MODIFY votoRegistrado ENUM('SIM', 'NAO', 'ABSTENCAO', 'OBSTRUCAO', 'AUSENCIA JUSTIFICADA', 'AUSENTE', 'NAO REGISTRADO') NOT NULL;

DELETE FROM etlCheckpoint WHERE nomeScript = 'popular/voto.py#camara_logs_ausencia_justificada';

-- ────────────────────────────────────────────────────────────────────────────
-- 3. proposicao: os ids da Câmara e os codigoMateria do Senado são numerações
--    independentes que podem colidir. Adiciona a coluna casa (preenchida a
--    partir do tipo já vinculado) e troca a unicidade global de idApi por
--    (idApi, casa).
--    Obs.: proposições sem idTipoProposicao ficam com casa NULL; elas serão
--    corrigidas na próxima execução dos scripts de proposição.
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE proposicao
    ADD COLUMN casa ENUM('Camara', 'Senado', 'Congresso') NULL AFTER idApi;

UPDATE proposicao p
JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
SET p.casa = t.casa;

ALTER TABLE proposicao
    DROP INDEX idApi,
    ADD UNIQUE KEY unique_proposicao_api (idApi, casa);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. orgao e parlamentar: mesma correção de namespace por casa.
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE orgao
    DROP INDEX idApi,
    ADD UNIQUE KEY unique_orgao_api (idApi, casa);

ALTER TABLE parlamentar
    DROP INDEX idApi,
    ADD UNIQUE KEY unique_parlamentar_api (idApi, cargo);
