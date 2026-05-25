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

Execute o schema para criar as tabelas antes de rodar qualquer script:

```bash
mysql -u <usuario> -p < popular/schema.sql
```

## Scripts

Execute os scripts na ordem abaixo para garantir que as dependências entre tabelas sejam respeitadas.

| Ordem | Script                  | O que faz                                                         |
|-------|-------------------------|-------------------------------------------------------------------|
| 1     | `parlamentar.py`        | Importa deputados e senadores                                     |
| 2     | `partidos.py`           | Importa partidos políticos                                        |
| 3     | `redeSocial.py`         | Importa redes sociais dos parlamentares                           |
| 4     | `gabinete.py`           | Importa dados de gabinete dos parlamentares                       |
| 5     | `tipoProposicao.py`     | Importa os tipos de proposição (PL, PEC, MPV…)                   |
| 6     | `proposicao.py`         | Importa proposições legislativas da Câmara e do Senado            |
| 7     | `autoriaProposicao.py`  | Vincula autores (parlamentares) às proposições                    |
| 8     | `tema.py`               | Importa o catálogo de temas da Câmara                             |
| 9     | `vincular_tema.py`      | Vincula temas às proposições (e cria temas do Senado on-the-fly)  |
| 10    | `votacao.py`            | Importa votações nominais                                         |
| 11    | `voto.py`               | Importa os votos individuais de cada parlamentar                  |
| 12    | `despesas.py`           | Importa despesas do mandato (CEAP/verba de gabinete)              |
| 13    | `tipoTramitacao.py`     | Importa os tipos de tramitação a partir do histórico              |
| 14    | `orgao.py`              | Importa os órgãos (comissões, plenário…) das tramitações          |
| 15    | `tramitacao.py`         | Importa o histórico de tramitação das proposições                 |
| 16    | `historico.py`          | Importa histórico complementar de situações                       |
| 17    | `emenda.py`             | Emendas parlamentares importadas do Portal da Transparência       |
| 18    | `relacionarEmendaParlamentar.py` | Relacionamento entre emendas e parlamentares encontrados |

Cada script pode ser executado individualmente:

```bash
python popular/parlamentar.py
```

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
