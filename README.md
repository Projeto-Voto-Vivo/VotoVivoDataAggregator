# VotoVivoDataAggregator

Scripts de coleta e importação de dados públicos do Congresso Nacional (Câmara dos Deputados e Senado Federal) para o banco de dados do projeto VotoVivo.

## Pré-requisitos

- Python 3.10+
- MySQL 8+

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

O `pip install -e .` instala o pacote `utils` em modo editável, o que permite executar qualquer script de `popular/` diretamente (`python popular/algum_script.py`) sem erros de import.

## Configuração

Copie o arquivo de exemplo e preencha as credenciais do banco:

```bash
cp .env.example .env
```

| Variável      | Descrição              | Padrão      |
|---------------|------------------------|-------------|
| `DB_HOST`     | Host do MySQL          | `localhost` |
| `DB_USER`     | Usuário do MySQL       | —           |
| `DB_PASSWORD` | Senha do MySQL         | —           |
| `DB_NAME`     | Nome do banco de dados | `votovivo`  |
| `PORTAL_TRANSPARENCIA_API_KEY` | Token da API do Portal da Transparência. Obrigatório para importar emendas. | |

> **Importante:** o script `emenda.py` precisa do token `PORTAL_TRANSPARENCIA_API_KEY` para consultar a API do Portal da Transparência.

## Banco de dados

### Banco de produção

Execute o schema para criar as tabelas antes de rodar qualquer script:

```bash
mysql -u <usuario> -p < popular/schema.sql
```

### Migrações

Bancos criados com uma versão anterior do `schema.sql` devem aplicar as migrações de `popular/migrations/` (em ordem cronológica):

```bash
mysql -u <usuario> -p < popular/migrations/2026-08-19_integridade.sql
mysql -u <usuario> -p < popular/migrations/2026-08-19_integridade_parte2.sql
mysql -u <usuario> -p < popular/migrations/2026-08-19_metricas.sql
mysql -u <usuario> -p < popular/migrations/2026-08-19_dados_mandato.sql
mysql -u <usuario> -p < popular/migrations/2026-08-20_orgao_nome.sql
```

### Banco de testes

Cria o schema e executa todos os scripts com `TEST_MODE=True`, gerando um dataset reduzido para desenvolvimento:

```bash
python popular/seed_teste.py                 # tempo padrão de 60s por script
python popular/seed_teste.py --tempo 30      # limita cada script a 30s
python popular/seed_teste.py --force         # recria o schema sem perguntar
python popular/seed_teste.py --sem-schema    # pula a criação do schema
```

Cada script respeita `TEST_MODE` limitando o número de registros processados por iteração e `MAX_TIME_SECONDS` interrompendo o loop quando o tempo máximo é atingido.

## Como popular o banco de dados

Para realizar a carga completa e automatizada de todos os dados do projeto (parlamentares, despesas, proposições, tramitações, votações, etc.), execute o orquestrador principal. Ele roda os scripts abaixo em sequência, garantindo a integridade das chaves e relacionamentos, e retoma de onde parou se for interrompido:

```bash
python popular/principal.py
```

## Scripts

| Ordem | Script                                  | Depende de                          | O que faz                                                                                            |
|-------|------------------------------------------|--------------------------------------|-------------------------------------------------------------------------------------------------------|
| 1     | `camara/parlamentar_camara.py`           | —                                    | Importa deputados, gabinete, redes sociais e condição de mandato (Titular/Suplente/Afastado)          |
| 2     | `senado/parlamentar_senado.py`           | —                                    | Importa senadores e condição de mandato                                                               |
| 3     | `partidos.py`                            | —                                    | Importa o catálogo de partidos                                                                        |
| 4     | `camara/historico_parlamentar_camara.py` | `camara/parlamentar_camara.py`       | Histórico dos deputados: filiações partidárias e períodos de exercício do mandato                     |
| 5     | `senado/mandato_senado.py`               | `senado/parlamentar_senado.py`       | Mandatos dos senadores: períodos de exercício e filiações partidárias                                 |
| 6     | `tipoProposicao.py`                      | —                                    | Importa os tipos de proposição (PL, PEC, MPV…)                                                        |
| 7     | `camara/tema_camara.py`                  | —                                    | Importa o catálogo de temas da Câmara                                                                 |
| 8     | `senado/tema_senado.py`                  | —                                    | Importa os assuntos do Senado                                                                         |
| 9     | `camara/orgao_camara.py`                 | `camara/parlamentar_camara.py`       | Carrega o **catálogo completo** de órgãos da Câmara (`/orgaos`, ~1.700 em 17 requisições — inclui Plenário e CCP, que não têm membros) e a relação de membros |
| 10    | `senado/orgao_senado.py`                 | `senado/parlamentar_senado.py`       | Importa órgãos/comissões do Senado e a relação de membros                                             |
| 11    | `camara/proposicao_camara.py`            | `tipoProposicao`, `parlamentar_camara` | Importa proposições da Câmara a partir dos **dumps anuais** oficiais (cobertura completa: inclui proposições do Executivo, de comissões e de ex-parlamentares), com autores, temas e relações entre proposições (principal/anterior/posterior) |
| 12    | `senado/proposicao_senado.py`            | `tipoProposicao`, `parlamentar_senado` | Importa **todos** os processos do Senado por ano (universo completo via listagem anual), com autores e assuntos para os de autoria de senador |
| 13    | `relacionarProposicaoCasas.py`           | `proposicao_camara`, `proposicao_senado` | Vincula a mesma matéria entre Câmara e Senado (numeração unificada) — a jornada bicameral da lei |
| 14    | `camara/despesas_camara.py`              | `camara/parlamentar_camara.py`       | Importa despesas do mandato dos deputados (CEAP/verba de gabinete)                                    |
| 15    | `senado/despesas_senado.py`              | `senado/parlamentar_senado.py`       | Importa despesas do mandato dos senadores (CEAPS)                                                     |
| 16    | `emenda.py`                              | —                                    | Emendas parlamentares importadas do Portal da Transparência                                           |
| 17    | `tipoTramitacao.py`                      | `proposicao_camara`, `proposicao_senado` | Importa os tipos de tramitação a partir do histórico das proposições já importadas                |
| 18    | `camara/tramitacao_camara.py`            | `proposicao_camara`, `tipoTramitacao` | Importa o histórico de tramitação das proposições da Câmara                                         |
| 19    | `senado/tramitacao_senado.py`            | `proposicao_senado`, `tipoTramitacao` | Importa o histórico de tramitação das proposições do Senado                                         |
| 20    | `camara/evento_camara.py`                | `parlamentar_camara`, `orgao_camara` | Importa eventos e presenças em plenário/comissões da Câmara                                           |
| 21    | `senado/votacao_presenca_senado.py`      | `parlamentar_senado`, `proposicao_senado`, `mandato_senado` | Importa votações nominais, votos e presenças do Senado (ausência só dentro do exercício do mandato) |
| 22    | `camara/votacao_camara.py`               | `proposicao_camara`, `orgao_camara`  | Importa votações nominais da Câmara                                                                   |
| 23    | `camara/orientacao_camara.py`            | `camara/votacao_camara.py`           | Importa a orientação das bancadas em cada votação da Câmara (dumps anuais) — "seguiu o partido?"      |
| 24    | `voto.py`                                | `camara/votacao_camara.py`, `parlamentar` | Importa os votos individuais de cada deputado                                                    |
| 25    | `relacionarEmendaParlamentar.py`         | `emenda`, `parlamentar`              | Vincula emendas a parlamentares por correspondência de nome (autor da emenda não vem com FK na API)   |

> Esta é exatamente a ordem usada em `popular/principal.py` (`PIPELINE_SCRIPTS`) e em `popular/seed_teste.py` (`SCRIPTS_ORDEM`) — respeita as dependências entre tabelas (ex.: `tipoTramitacao.py` só roda depois das proposições estarem importadas).

Cada script pode ser executado individualmente:

```bash
python popular/camara/parlamentar_camara.py
```

## Infraestrutura compartilhada (`utils/`)

Toda a lógica repetida entre scripts vive em `utils/`, em vez de duplicada em cada arquivo:

| Módulo | Responsabilidade |
|--------|-------------------|
| `utils/db.py` | `get_connection(**cursor_kwargs)` — abre a conexão MySQL a partir das variáveis de ambiente e devolve `(conexao, cursor)`. `garantir_conexao(db)` — reconecta se a conexão caiu (chamado pelos scripts após esperas longas de rede, antes de abrir a transação seguinte). |
| `utils/logging_config.py` | `get_logger(nome)` — logger com formato padronizado. |
| `utils/checkpoint_manager.py` | `CheckpointManager` — lê/grava o progresso na tabela `etlCheckpoint` (ver seção Checkpoints abaixo). |
| `utils/http_client.py` | `http_client` (instância de `ResilientSession`) — cliente HTTP com retry automático e pausas de segurança contra rate limit (ver seção abaixo). |
| `utils/orgao_cache.py` | `OrgaoCache` — resolve `idApi -> idOrgao` para uma casa, criando um registro placeholder em `orgao` quando o órgão ainda não é conhecido. |
| `utils/etl_erro.py` | `EtlErro` — fila de erros (dead-letter) persistida na tabela `etlErro` (ver seção Fila de erros abaixo). |
| `utils/execucao.py` | `ExecucaoEtl` — métricas por execução na tabela `etlExecucao` (ver seção Métricas abaixo). |

## Checkpoints

Os scripts de longa duração utilizam um sistema de checkpoints (`utils/checkpoint_manager.py`) para tolerar interrupções. O progresso é salvo na tabela `etlCheckpoint` do banco de dados após cada lote processado, permitindo que o script seja reiniciado do ponto onde parou sem reprocessar registros já importados.

A tabela guarda duas informações separadas: o **cursor** de progresso (`ultimoParametro`) e o **estado** (`status`, `EM_PROGRESSO` ou `CONCLUIDO`):

- Se um script falha no meio (erro de rede, `RateLimitAbort`, Ctrl+C), o estado fica `EM_PROGRESSO` e a próxima execução retoma exatamente do primeiro item que falhou.
- Se um script termina com falhas parciais, ele sai com código ≠ 0 (o `principal.py` interrompe o pipeline) e o cursor fica parado no último item bem-sucedido — basta rodar de novo.
- Se um script já `CONCLUIDO` for reexecutado, ele recomeça do zero como um *refresh* completo (seguro, pois as cargas são upserts idempotentes). Exceções incrementais: `camara/proposicao_camara.py` e `emenda.py` reposicionam o cursor ao concluir, de modo que execuções seguintes atualizam apenas o **ano corrente** — para recarga total desses dois, apague o checkpoint.

Para forçar a reexecução completa de um script, apague o checkpoint correspondente antes de rodá-lo:

```sql
DELETE FROM etlCheckpoint WHERE nomeScript LIKE 'nome_do_script%';
```

## Fila de erros (etlErro)

Quando um item individual falha (uma votação, uma proposição, uma emenda), o erro é registrado na tabela `etlErro` em vez de ser apenas logado e perdido. Os scripts de tramitação e de votos releem os itens pendentes (`resolvido = 0`) no início da execução seguinte e os reprocessam automaticamente, marcando-os como resolvidos ao ter sucesso. Para inspecionar o que ficou para trás:

```sql
SELECT nomeScript, chaveItem, erro, tentativas, dataUltimoErro
FROM etlErro WHERE resolvido = 0;
```

## Métricas de execução (etlExecucao)

Cada execução de script grava uma linha na tabela `etlExecucao` com início, fim, status (`SUCESSO`/`FALHA`/`INTERROMPIDO`), itens processados, registros gravados e erros. Uma linha que permanece `EM_EXECUCAO` indica que o processo morreu sem finalizar. Para auditar as últimas cargas:

```sql
SELECT nomeScript, dataInicio, dataFim, status, itensProcessados, registrosGravados, erros
FROM etlExecucao ORDER BY dataInicio DESC LIMIT 30;
```

## Staging (cache HTTP)

Para proteger o progresso contra retrabalho — reprocessar transformações sem bater de novo nas APIs — o `http_client` suporta um cache em disco das respostas brutas, controlado por variáveis de ambiente:

| Valor de `ETL_HTTP_CACHE` | Comportamento |
|---------------------------|----------------|
| *(vazio, padrão)* | Desligado. |
| `gravar` | Toda resposta HTTP 200 é gravada em `ETL_HTTP_CACHE_DIR` (padrão `staging/http_cache`). |
| `ler` | Respostas já em disco são servidas de lá (sem rede); o que faltar é buscado na API e gravado. |

O `.env.example` já vem com `gravar` ligado — em produção, deixe assim. Fluxo típico: rodar a carga com `ETL_HTTP_CACHE=gravar`; se depois for preciso corrigir uma transformação e recarregar, apagar os checkpoints e rodar com `ETL_HTTP_CACHE=ler` — a recarga inteira sai do disco em minutos. O diretório `staging/` está no `.gitignore`.

> **Atenção:** em modo `ler` os dados podem estar defasados em relação à API. Use-o para reprocessos, não para atualizar dados.

## Limites de taxa (Rate Limiting)

Todas as chamadas às APIs da Câmara e do Senado passam por `http_client.get_safe(...)` (`utils/http_client.py`), que já trata automaticamente:

1. **Erros de servidor** (500/502/503/504) — retentativas automáticas com backoff, via `urllib3.Retry`.
2. **HTTP 429 (Too Many Requests)** — até 3 tentativas com pausa de 60s entre elas.
3. **429 persistente** — se mesmo após as tentativas normais a API continuar a bloquear, o script entra em pausas de segurança cada vez mais longas (5, 10 e 20 minutos). Se ainda assim continuar bloqueado, o script **é interrompido** (exceção `RateLimitAbort`) em vez de continuar a martelar a API ou ficar preso processando o resto dos dados às cegas.

Se um script parar com `RateLimitAbort`, é seguro simplesmente rodá-lo de novo mais tarde — o checkpoint garante que ele retoma de onde parou.

## Desempenho (fetch paralelo)

Os scripts de maior volume (tramitações, votos, detalhes de votações e de processos do Senado, páginas de presença) buscam as respostas HTTP **em paralelo** (`utils/paralelo.py`: `ETL_WORKERS` threads, lotes de `ETL_TAMANHO_LOTE` itens) e gravam **sequencialmente, na ordem original da fila** — o checkpoint assume ordem crescente, e gravar fora de ordem faria uma retomada pular itens. Paralelismo amplifica a chance de HTTP 429; o `http_client` já trata isso com pausas crescentes, mas comece com 4 workers e observe o log. Escritas em lote (`executemany`) e um commit por entidade (deputado/proposição/votação) completam o ganho.

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
