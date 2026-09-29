-- Migração 2026-09-28 — candidaturaTse: marcadores do TSE (#NE / #NULO) viram NULL
--   mysql -u <usuario> -p < popular/migrations/2026-09-28_candidatura_marcadores_tse.sql
USE votovivo;

UPDATE candidaturaTse SET situacaoCandidatura = NULL WHERE situacaoCandidatura IN ('#NE', '#NE#', '#NULO', '#NULO#');
UPDATE candidaturaTse SET resultadoEleicao = NULL WHERE resultadoEleicao IN ('#NE', '#NE#', '#NULO', '#NULO#');
