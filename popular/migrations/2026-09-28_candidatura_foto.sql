-- Migração 2026-09-28 — candidaturaTse.fotoUrl (foto do candidato no DivulgaCandContas)
--   mysql -u <usuario> -p < popular/migrations/2026-09-28_candidatura_foto.sql
USE votovivo;

ALTER TABLE candidaturaTse ADD COLUMN fotoUrl VARCHAR(500) AFTER resultadoEleicao;
