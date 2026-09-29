-- Migração 2026-09-28 — corrige candidaturaTse.fotoUrl: usava CD_ELEICAO (6257/6259) no lugar do id da eleição no DivulgaCandContas
--   mysql -u <usuario> -p < popular/migrations/2026-09-28_candidatura_foto_fix.sql
USE votovivo;

UPDATE candidaturaTse
SET fotoUrl = CONCAT('https://divulgacandcontas.tse.jus.br/divulga/rest/arquivo/img/20322002026/', sqCandidato, '/', uf)
WHERE anoEleicao = 2026;
