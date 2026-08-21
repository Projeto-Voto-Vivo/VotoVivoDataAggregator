-- Migração 2026-08-21 — troca de fontes indisponíveis/desativadas
--   mysql -u <usuario> -p < popular/migrations/2026-08-21_fontes_substitutas.sql
USE votovivo;

-- 1. Tramitação do Senado: o serviço legado /materia/movimentacoes foi
--    desativado em 2026-02-01. O script passa a usar /processo/{id}
--    (informes legislativos), cuja numeração de eventos é outra — as linhas
--    antigas (chave SEN_{materia}_{CodigoTramitacao}) seriam duplicadas pelas
--    novas (SEN_{materia}_{idInforme}). Remove o histórico legado do Senado e
--    os checkpoints/erros do script antigo; a recarga é completa.
DELETE t FROM tramitacao t
  JOIN proposicao p ON p.idProposicao = t.idProposicao
WHERE p.casa = 'Senado';

DELETE FROM etlCheckpoint WHERE nomeScript = 'popular/tramitacao.py#senado';
DELETE FROM etlErro       WHERE nomeScript = 'popular/tramitacao.py#senado';

-- 2. Despesas da Câmara: a API /deputados/{id}/despesas está devolvendo vazio;
--    o script passa a usar os arquivos oficiais anuais da Cota. O checkpoint
--    antigo avançou sem dados e não deve ser reaproveitado.
DELETE FROM etlCheckpoint WHERE nomeScript IN (
    'popular/despesas.py#camara_dinamico_v2',
    'popular/despesas.py#camara_dinamico_v3'
);
