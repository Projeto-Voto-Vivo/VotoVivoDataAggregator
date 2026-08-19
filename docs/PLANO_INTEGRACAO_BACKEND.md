# Plano de Integração do Backend — Voto Vivo

> Plano de ação para alinhar o **VotoVivoBackEnd** (e os pontos do **VotoVivoFrontEnd** que dependem dele) com o banco produzido pelo **VotoVivoDataAggregator** após as correções de integridade de 2026-08-19. Ordenado por dependência: cada fase destrava a seguinte.

**Contexto.** A auditoria de 2026-08-19 encontrou três versões incompatíveis do schema em circulação (agregador, banco de produção, Prisma do backend), estatísticas calculadas com metodologia frágil (presença), dados sintéticos no frontend e rotas de escrita abertas. O agregador já foi corrigido e ganhou tabelas novas; este plano cobre o que falta nos outros dois repositórios.

| Fase | Tema | Bloqueia | Esforço estimado |
|------|------|----------|------------------|
| 0 | Contrato de dados (Prisma ↔ banco) | Todas as demais | 1 dia |
| 1 | Segurança e integridade da API | — | 0,5 dia |
| 2 | Presença correta | Dashboards de comparação | 1–2 dias |
| 3 | Expor os dados reais que o banco já tem | Perfil completo, eleições | 2–3 dias |
| 4 | Endpoints dos dashboards | Dashboards planejados | 2–3 dias |
| 5 | Correções do frontend | — | 2 dias |

---

## Fase 0 — Contrato de dados (bloqueador de tudo)

O Prisma foi gerado por introspecção de um banco que divergiu do agregador. Depois de aplicar as migrações `popular/migrations/2026-08-19_*.sql` no banco:

1. **Regenerar o schema Prisma** com `prisma db pull` e revisar o diff manualmente.
2. **Enum canônico de voto** (`votoRegistrado`): `SIM, NAO, ABSTENCAO, OBSTRUCAO, AUSENCIA JUSTIFICADA, AUSENTE, NAO REGISTRADO`. O `VoteChoice` atual não tem `OBSTRUCAO` nem `NAO REGISTRADO` — o Prisma **lança erro** ao ler linhas com valores desconhecidos, então qualquer votação com obstrução derruba o endpoint. Remover `SEM_REGISTRO` (valor do bug antigo, não existe mais no banco).
3. **Chaves compostas**: `parlamentar`, `proposicao` e `orgao` agora são únicos por `(idApi, cargo/casa)`, não por `idApi` global. Trocar todo `findUnique({ where: { apiId } })` por `findFirst` com o discriminador de casa.
4. **Mapear o que faltava**: `proposicao.casa`, `proposicao.dataApresentacao`, `parlamentar.condicao_mandato`, `despesa.idApi`, `orgao.tipoOrgao`, `etlCheckpoint.status`.
5. **Modelar as tabelas novas**: `partido`, `filiacaoPartidaria`, `mandatoExercicio`, `orientacaoVotacao`, `proposicaoRelacao`, `etlExecucao`, `etlErro`.
6. **Corrigir tipos divergentes**: `AmendmentDocument.apiId` é `Int?` no Prisma mas `VARCHAR(50)` no schema do agregador; `tipoTramitacao.idApi` idem.
7. **Sincronizar o swagger**: só 12 dos ~20 paths estão documentados; o enum de voto documentado tem 4 valores; `ProposicaoPerfil` declara campos (`resumo`, `papel`, `tema[]`, `dataApresentacao`) que a API nunca devolve.

**Aceite:** `prisma validate` limpo contra o banco migrado; teste de leitura de uma votação do Senado com voto `OBSTRUCAO` sem erro; swagger cobre 100% das rotas.

## Fase 1 — Segurança e integridade da API

O banco é alimentado exclusivamente pelo ETL; a API não precisa escrever.

1. **Remover** `POST /votacoes`, `POST /proposicoes`, `POST /votos`, `DELETE /votos/:id` (ou proteger com autenticação, se houver caso de uso real). Hoje qualquer pessoa pode inserir votações falsas com CORS `*`.
2. Restringir CORS ao domínio do frontend; adicionar rate limit básico.
3. Validar parâmetros: `Number(req.params.id)` com `NaN` chega ao Prisma e vira **500** (deveria ser 400); `GET /proposicoes/:id` inexistente devolve **200 com `null`** (deveria ser 404).
4. Paginação obrigatória em `GET /votacoes` e `GET /proposicoes` (hoje fazem `findMany` da tabela inteira).
5. Cumprir o contrato de `GET /parlamentares/:id/emendas`: o swagger promete `{data, meta}` paginado; o service devolve array puro sem paginação.

**Aceite:** rotas de escrita ausentes do router; `id` inválido → 400; recurso inexistente → 404; nenhuma listagem sem `take`.

## Fase 2 — Presença correta

A taxa de presença é a estatística-vitrine do produto e hoje sai errada por quatro causas somadas (`parliamentarian.service.ts:482-573`):

1. **Ler a tabela `presenca` para as duas casas.** O agregador agora grava presença real do Senado (eventos `SESSAO_*`, com `PRESENTE/JUSTIFICADA/AUSENTE` derivados das siglas oficiais do painel e ausência restrita ao exercício do mandato). **Remover o ramo sintético** que inventa presença por votos/dia — mantê-lo junto com os dados reais causa contagem dupla.
2. **Corrigir a classificação de sessão**: `includes('deliberativa')` marca "Sessão **Não** Deliberativa Solene" como deliberativa, e `descricaoTipo` nulo cai em deliberativa por default. Classificar por igualdade normalizada, com lista explícita.
3. **Separar plenário de comissão** (via `evento.idOrgao`/`orgao.tipoOrgao`): são taxas distintas; somá-las produz um número sem significado.
4. **Denominador por exercício**: contar apenas eventos dentro dos períodos de `mandatoExercicio` do parlamentar — sem isso, quem assumiu no meio do mandato é punido injustamente.
5. `NAO REGISTRADO`/`OBSTRUCAO` não são ausência; incluir eventos da casa `Congresso`.
6. **Não comparar taxas entre casas** sem rotular a metodologia de cada uma na UI.

**Aceite:** teste com senador que assumiu em 2025 (zero ausências antes da posse); teste com "Sessão Não Deliberativa Solene" fora do denominador deliberativo; taxa de plenário ≠ taxa de comissão no payload.

## Fase 3 — Expor os dados reais que o banco já tem

Cada item abaixo substitui um placeholder ou dado inventado por dado oficial já carregado:

| Expor | Fonte no banco | Substitui |
|-------|----------------|-----------|
| Situação do mandato | `parlamentar.condicao_mandato` | Hardcode `'Em exercício'` no front |
| Comissões do parlamentar | `membroOrgao` + `orgao` (novo `GET /parlamentares/:id/comissoes`) | Comissões **sintéticas** geradas por módulo do ID |
| Data de apresentação e temas da proposição | `proposicao.dataApresentacao`, `temaProposicao` | Campos prometidos no swagger e nunca devolvidos |
| Histórico partidário | `filiacaoPartidaria` (novo endpoint ou bloco do perfil) | Apenas `partidoAtual` |
| Períodos de mandato | `mandatoExercicio` | Nada (não existia) |
| "Seguiu a orientação do partido?" | `orientacaoVotacao` × `voto` × `filiacaoPartidaria` (partido na data da votação) | `alinhamento: null` hardcoded |
| Jornada bicameral da proposição | `proposicaoRelacao` (`MESMA_MATERIA`, `PRINCIPAL/ANTERIOR/POSTERIOR`) | Proposições desconexas entre casas |
| Confiança do vínculo de emenda | `confiancaVinculo`/`metodoVinculo` (API já devolve) | UI omite que o vínculo é heurístico |
| Filtro por casa | `parlamentar.cargo` como query param em `GET /parlamentares` | Fan-out de ~30 requisições no front para filtrar em memória |

**Aceite:** nenhum dado exibido no perfil vem de gerador sintético; `GET /parlamentares?casa=senado` pagina no servidor.

## Fase 4 — Endpoints dos dashboards

Regras transversais: agregar **em SQL** (não `reduce` em JS sobre listas completas); toda métrica monetária declara qual valor usa (`empenhado` ≠ `pago`); rankings entre casas separados ou explicitamente normalizados.

1. `GET /dashboards/emendas/total?ano=&tipo=&metrica=empenhado|liquidado|pago` — soma sobre `emenda` (inclui bancada/comissão no total nacional).
2. `GET /dashboards/emendas/top?casa=camara|senado&limit=10` — via `emendaParlamentar`; documentar que emendas sem vínculo (ambíguas/ex-parlamentares) ficam fora, e considerar filtro `confiancaVinculo >= X`.
3. `GET /dashboards/despesas/top?casa=&limit=10` — casas separadas (cotas CEAP/CEAPS não são comparáveis); opcional `normalizar=mes` dividindo pelos meses de exercício (`mandatoExercicio`).
4. `GET /parlamentares/:id/emendas` paginado + agregados no `/resumo` via `aggregate` do Prisma.
5. Comparação entre parlamentares: endpoint que devolve métricas **normalizadas** (por mês de exercício; presença com metodologia rotulada) e recusa comparação entre casas sem flag explícita.
6. `mediaMensal` de despesas: dividir pelo número de **meses com dados dentro do exercício**, nunca por 12 fixo (`parliamentarian.service.ts:287`).

**Aceite:** cada dashboard do roadmap (total de emendas, top 10 emendas por casa, top 10 despesas, gastos de emenda do mandato, comparação) tem um endpoint que responde com uma query agregada e metadados de metodologia (`metrica`, `janela`, `exclusoes`).

## Fase 5 — Correções do frontend

1. **Apagar os geradores sintéticos** (`TEMAS`, `COMISSOES`, `buildProposicoes`, `buildVotacoes` em `services/parlamentares.ts`) e a biografia montada a partir deles.
2. `parseMoney`: aceitar número e string ISO — hoje `"1234.56"` vira **123456** silenciosamente.
3. Taxa de presença ausente → "sem dados", nunca "0%"; barra limitada a 0–100.
4. Mapa de rótulos de voto completo (7 valores) — hoje `OBSTRUCAO` aparece cru para o cidadão.
5. Paginação: usar `meta.limit` do backend, não `5` literal; `mediaMensal` idem Fase 4.
6. Busca da home: enviar o termo para nome **e** partido **e** UF (hoje "PT" e "SP" caem sempre em `nome` e não acham nada).
7. "Destaques" da home: usar um critério real (ex.: maiores movimentações da semana) ou renomear — hoje são os 8 primeiros em ordem alfabética.
8. Página de educação: datar e fontear os valores monetários hardcoded (salário, CEAP, gabinete), corrigir a legenda vazia `Fonte: .`; ideal: servir esses valores do banco.

**Aceite:** grep sem resultados para `TEMAS`/`COMISSOES` sintéticos; teste de `parseMoney("1234.56") === 1234.56`; nenhum "R$ 0" para dado ausente.

---

## Referência rápida — o que o agregador passou a fornecer (2026-08-19)

- **Tabelas novas:** `partido`, `filiacaoPartidaria`, `mandatoExercicio`, `orientacaoVotacao`, `proposicaoRelacao`, `etlExecucao`, `etlErro`.
- **Colunas novas:** `proposicao.casa`, `despesa.idApi` (chave natural), `etlCheckpoint.status`.
- **Semântica corrigida:** enum de voto completo (obstrução preservada); presença do Senado real e restrita ao exercício; despesas deduplicáveis; emendas com upsert (valores financeiros atualizam); janela padrão do ETL = mandato completo (2023-01).
- **Cobertura ampliada:** proposições da Câmara via dumps anuais (universo completo) e do Senado via listagem anual (universo completo); orientações de bancada; relações entre proposições e entre casas.
