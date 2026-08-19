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
| 3     | `tipoProposicao.py`                      | —                                    | Importa os tipos de proposição (PL, PEC, MPV…)                                                        |
| 4     | `camara/tema_camara.py`                  | —                                    | Importa o catálogo de temas da Câmara                                                                 |
| 5     | `senado/tema_senado.py`                  | —                                    | Importa os assuntos do Senado                                                                         |
| 6     | `camara/orgao_camara.py`                 | `camara/parlamentar_camara.py`       | Importa órgãos/comissões da Câmara e a relação de membros                                             |
| 7     | `senado/orgao_senado.py`                 | `senado/parlamentar_senado.py`       | Importa órgãos/comissões do Senado e a relação de membros                                             |
| 8     | `camara/proposicao_camara.py`            | `tipoProposicao`, `parlamentar_camara` | Importa proposições da Câmara, autores (incluindo coautores) e temas vinculados                     |
| 9     | `senado/proposicao_senado.py`            | `tipoProposicao`, `parlamentar_senado` | Importa proposições do Senado, autores (incluindo coautores) e assuntos vinculados                  |
| 10    | `camara/despesas_camara.py`              | `camara/parlamentar_camara.py`       | Importa despesas do mandato dos deputados (CEAP/verba de gabinete)                                    |
| 11    | `senado/despesas_senado.py`              | `senado/parlamentar_senado.py`       | Importa despesas do mandato dos senadores (CEAPS)                                                     |
| 12    | `emenda.py`                              | —                                    | Emendas parlamentares importadas do Portal da Transparência                                           |
| 13    | `tipoTramitacao.py`                      | `proposicao_camara`, `proposicao_senado` | Importa os tipos de tramitação a partir do histórico das proposições já importadas                |
| 14    | `camara/tramitacao_camara.py`            | `proposicao_camara`, `tipoTramitacao` | Importa o histórico de tramitação das proposições da Câmara                                         |
| 15    | `senado/tramitacao_senado.py`            | `proposicao_senado`, `tipoTramitacao` | Importa o histórico de tramitação das proposições do Senado                                         |
| 16    | `camara/evento_camara.py`                | `parlamentar_camara`, `orgao_camara` | Importa eventos e presenças em plenário/comissões da Câmara                                           |
| 17    | `senado/votacao_presenca_senado.py`      | `parlamentar_senado`, `proposicao_senado` | Importa votações nominais, votos e presenças do Senado                                            |
| 18    | `camara/votacao_camara.py`               | `proposicao_camara`, `orgao_camara`  | Importa votações nominais da Câmara                                                                   |
| 19    | `voto.py`                                | `camara/votacao_camara.py`, `parlamentar` | Importa os votos individuais de cada deputado                                                    |
| 20    | `relacionarEmendaParlamentar.py`         | `emenda`, `parlamentar`              | Vincula emendas a parlamentares por correspondência de nome (autor da emenda não vem com FK na API)   |

> Esta é exatamente a ordem usada em `popular/principal.py` (`PIPELINE_SCRIPTS`) e em `popular/seed_teste.py` (`SCRIPTS_ORDEM`) — respeita as dependências entre tabelas (ex.: `tipoTramitacao.py` só roda depois das proposições estarem importadas).

Cada script pode ser executado individualmente:

```bash
python popular/camara/parlamentar_camara.py
```

## Infraestrutura compartilhada (`utils/`)

Toda a lógica repetida entre scripts vive em `utils/`, em vez de duplicada em cada arquivo:

| Módulo | Responsabilidade |
|--------|-------------------|
| `utils/db.py` | `get_connection(**cursor_kwargs)` — abre a conexão MySQL a partir das variáveis de ambiente e devolve `(conexao, cursor)`. |
| `utils/logging_config.py` | `get_logger(nome)` — logger com formato padronizado. |
| `utils/checkpoint_manager.py` | `CheckpointManager` — lê/grava o progresso na tabela `etlCheckpoint` (ver seção Checkpoints abaixo). |
| `utils/http_client.py` | `http_client` (instância de `ResilientSession`) — cliente HTTP com retry automático e pausas de segurança contra rate limit (ver seção abaixo). |
| `utils/orgao_cache.py` | `OrgaoCache` — resolve `idApi -> idOrgao` para uma casa, criando um registro placeholder em `orgao` quando o órgão ainda não é conhecido. |

## Checkpoints

Os scripts de longa duração utilizam um sistema de checkpoints (`utils/checkpoint_manager.py`) para tolerar interrupções. O progresso é salvo na tabela `etlCheckpoint` do banco de dados após cada lote processado, permitindo que o script seja reiniciado do ponto onde parou sem reprocessar registros já importados.

A tabela guarda duas informações separadas: o **cursor** de progresso (`ultimoParametro`) e o **estado** (`status`, `EM_PROGRESSO` ou `CONCLUIDO`):

- Se um script falha no meio (erro de rede, `RateLimitAbort`, Ctrl+C), o estado fica `EM_PROGRESSO` e a próxima execução retoma exatamente do primeiro item que falhou.
- Se um script termina com falhas parciais, ele sai com código ≠ 0 (o `principal.py` interrompe o pipeline) e o cursor fica parado no último item bem-sucedido — basta rodar de novo.
- Se um script já `CONCLUIDO` for reexecutado, ele recomeça do zero como um *refresh* completo (seguro, pois as cargas são upserts idempotentes).

Para forçar a reexecução completa de um script, apague o checkpoint correspondente antes de rodá-lo:

```sql
DELETE FROM etlCheckpoint WHERE nomeScript LIKE 'nome_do_script%';
```

## Limites de taxa (Rate Limiting)

Todas as chamadas às APIs da Câmara e do Senado passam por `http_client.get_safe(...)` (`utils/http_client.py`), que já trata automaticamente:

1. **Erros de servidor** (500/502/503/504) — retentativas automáticas com backoff, via `urllib3.Retry`.
2. **HTTP 429 (Too Many Requests)** — até 3 tentativas com pausa de 60s entre elas.
3. **429 persistente** — se mesmo após as tentativas normais a API continuar a bloquear, o script entra em pausas de segurança cada vez mais longas (5, 10 e 20 minutos). Se ainda assim continuar bloqueado, o script **é interrompido** (exceção `RateLimitAbort`) em vez de continuar a martelar a API ou ficar preso processando o resto dos dados às cegas.

Se um script parar com `RateLimitAbort`, é seguro simplesmente rodá-lo de novo mais tarde — o checkpoint garante que ele retoma de onde parou.

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
