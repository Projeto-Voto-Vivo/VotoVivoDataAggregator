-- Migração 2026-08-19 (parte 5) — funde temas do Senado duplicados por nível
--   mysql -u <usuario> -p < popular/migrations/2026-08-19_corrige_temas_duplicados.sql
--
-- Causa: a chave única de `tema` é (codigoExterno, casa, nivel). O script
-- senado/tema_senado.py grava os assuntos com nivel='ESPECIFICO', enquanto o
-- sincronizar_temas de senado/proposicao_senado.py gravava sem informar o
-- nível — caindo no default 'UNICO'. O mesmo assunto passou a existir em duas
-- linhas, e o SELECT sem filtro de nível devolvia 2 resultados: o fetchone()
-- deixava um resultado não lido e a consulta seguinte falhava com
-- "Unread result found".
--
-- Esta migração reaponta os vínculos para a linha ESPECIFICO (a canônica) e
-- remove as linhas UNICO órfãs.
USE votovivo;

-- 1. Reaponta temaProposicao das linhas UNICO para a ESPECIFICO equivalente.
--    INSERT IGNORE + DELETE em vez de UPDATE: a proposição pode já estar
--    vinculada às duas linhas, e o UPDATE violaria a chave primária.
INSERT IGNORE INTO temaProposicao (idProposicao, idTema)
SELECT tp.idProposicao, esp.idTema
FROM temaProposicao tp
JOIN tema uni ON uni.idTema = tp.idTema AND uni.casa = 'Senado' AND uni.nivel = 'UNICO'
JOIN tema esp ON esp.codigoExterno = uni.codigoExterno
             AND esp.casa = 'Senado' AND esp.nivel = 'ESPECIFICO';

DELETE tp FROM temaProposicao tp
JOIN tema uni ON uni.idTema = tp.idTema AND uni.casa = 'Senado' AND uni.nivel = 'UNICO'
WHERE EXISTS (
    SELECT 1 FROM tema esp
    WHERE esp.codigoExterno = uni.codigoExterno
      AND esp.casa = 'Senado' AND esp.nivel = 'ESPECIFICO'
);

-- 2. Remove as linhas UNICO do Senado que têm equivalente ESPECIFICO.
--    (A subconsulta é embrulhada para o MySQL aceitar DELETE lendo a mesma tabela.)
DELETE FROM tema
WHERE casa = 'Senado' AND nivel = 'UNICO'
  AND codigoExterno IN (
      SELECT codigoExterno FROM (
          SELECT codigoExterno FROM tema WHERE casa = 'Senado' AND nivel = 'ESPECIFICO'
      ) AS especificos
  );

-- 3. Promove a ESPECIFICO os assuntos que só existem como UNICO (importados
--    apenas pelo script de proposições, antes da correção).
UPDATE tema SET nivel = 'ESPECIFICO'
WHERE casa = 'Senado' AND nivel = 'UNICO';

-- 4. Conferência: deve devolver 0 linhas.
SELECT codigoExterno, COUNT(*) AS linhas
FROM tema WHERE casa = 'Senado'
GROUP BY codigoExterno HAVING COUNT(*) > 1;
