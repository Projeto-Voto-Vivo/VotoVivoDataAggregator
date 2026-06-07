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
```

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
| `PORTAL_TRANSPARENCIA_API_KEY` | Token da API do Portal da Transparência. Obrigatório para importar emendas. | 

> **Importante:** o script `emenda.py` precisa do token `PORTAL_TRANSPARENCIA_API_KEY` para consultar a API do Portal da Transparência.

## Banco de dados

### Banco de produção

Cria o schema e executa todos os 19 scripts de população na ordem correta:

```bash
python popular/setup_banco.py                # cria schema + popula
python popular/setup_banco.py --force        # recria o schema sem perguntar
python popular/setup_banco.py --sem-schema   # pula a criação do schema
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

## Scripts

Execute os scripts na ordem abaixo para garantir que as dependências entre tabelas sejam respeitadas.

| Ordem | Script                           | Depende de                          | O que faz                                                                       |
|-------|----------------------------------|-------------------------------------|---------------------------------------------------------------------------------|
| 1     | `parlamentar.py`                 | —                                   | Importa deputados e senadores                                                   |
| 2     | `redeSocial.py`                  | `parlamentar`                       | Importa redes sociais dos parlamentares                                         |
| 3     | `gabinete.py`                    | `parlamentar`                       | Importa dados de gabinete dos parlamentares                                     |
| 4     | `tipoProposicao.py`              | —                                   | Importa os tipos de proposição (PL, PEC, MPV…)                                 |
| 5     | `proposicao.py`                  | `tipoProposicao`                    | Importa proposições legislativas da Câmara e do Senado                          |
| 6     | `autoriaProposicao.py`           | `parlamentar`, `proposicao`         | Vincula autores (parlamentares) às proposições                                  |
| 7     | `tema.py`                        | —                                   | Importa o catálogo de temas da Câmara                                           |
| 8     | `vincular_tema.py`               | `tema`, `proposicao`                | Vincula temas às proposições (e cria temas do Senado on-the-fly)                |
| 9     | `votacao.py`                     | `proposicao`                        | Importa votações nominais                                                       |
| 10    | `voto.py`                        | `votacao`, `parlamentar`            | Importa os votos individuais de cada parlamentar                                |
| 11    | `despesas.py`                    | `parlamentar`                       | Importa despesas do mandato (CEAP/verba de gabinete)                            |
| 12    | `tipoTramitacao.py`              | —                                   | Importa os tipos de tramitação a partir do histórico                            |
| 13    | `orgao.py`                       | `proposicao`                        | Importa os órgãos (comissões, plenário…) das tramitações                        |
| 14    | `tramitacao.py`                  | `proposicao`, `tipoTramitacao`, `orgao` | Importa o histórico de tramitação das proposições                           |
| 15    | `emenda.py`                      | —                                   | Emendas parlamentares importadas do Portal da Transparência                     |
| 16    | `relacionarEmendaParlamentar.py` | `emenda`, `parlamentar`             | Relaciona emendas aos parlamentares encontrados                                 |
| 17    | `presenca.py`                    | `parlamentar`, `orgao`              | Importa presenças em sessões plenárias (Câmara) e reuniões de comissão (Senado) |

Para executar todos os scripts em sequência:

```bash
python popular/parlamentar.py
python popular/redeSocial.py
python popular/gabinete.py
python popular/tipoProposicao.py
python popular/proposicao.py
python popular/autoriaProposicao.py
python popular/tema.py
python popular/vincular_tema.py
python popular/votacao.py
python popular/voto.py
python popular/despesas.py
python popular/tipoTramitacao.py
python popular/orgao.py
python popular/tramitacao.py
python popular/emenda.py
python popular/relacionarEmendaParlamentar.py
python popular/presenca.py
```

Cada script também pode ser executado individualmente:

```bash
python popular/parlamentar.py
```

## Checkpoints

Os scripts de longa duração utilizam um sistema de checkpoints para tolerar interrupções. O progresso é salvo na tabela `etlCheckpoint` do banco de dados após cada lote processado, permitindo que o script seja reiniciado do ponto onde parou sem reprocessar registros já importados.

Scripts com suporte a checkpoint: `parlamentar`, `redeSocial`, `autoriaProposicao`, `vincular_tema`, `votacao`, `voto`, `despesas`, `orgao`, `tramitacao`, `presenca` e `emenda`.

Para forçar a reexecução completa de um script, apague o checkpoint correspondente antes de rodá-lo:

```sql
DELETE FROM etlCheckpoint WHERE nomeScript LIKE 'popular/nome_do_script.py%';
```

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
