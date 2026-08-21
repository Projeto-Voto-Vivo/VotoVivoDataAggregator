-- Migração 2026-08-20 — orgao.nome: 500 -> 1000 caracteres
-- Comissões especiais da Câmara têm nomes de até ~800 caracteres; com o
-- catálogo completo (camara/orgao_camara.py) 45 órgãos estouravam VARCHAR(500)
-- ("Data too long for column 'nome'").
--   mysql -u <usuario> -p < popular/migrations/2026-08-20_orgao_nome.sql
USE votovivo;

ALTER TABLE orgao MODIFY nome VARCHAR(1000);
