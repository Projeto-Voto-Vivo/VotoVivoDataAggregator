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

## 🚀 Como Popular o Banco de Dados

Para realizar a carga completa e automatizada de todos os dados do projeto (parlamentares, despesas, proposições, tramitações, votações, etc.), basta executar o script orquestrador principal. 

Ele gerencia a esteira de ETL de forma sequencial, garantindo a integridade das chaves e relacionamentos.

Com o ambiente virtual ativo e as variáveis do `.env` configuradas, execute no terminal:

```bash
python principal.py
```



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



Cada script pode ser executado individualmente:

```bash
python popular/parlamentar.py
```

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
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

##  Como Popular o Banco de Dados

Para realizar a carga completa e automatizada de todos os dados do projeto (parlamentares, despesas, proposições, tramitações, votações, etc.), basta executar o script orquestrador principal. 

Ele gerencia a esteira de ETL de forma sequencial, garantindo a integridade das chaves e relacionamentos.

Com o ambiente virtual ativo e as variáveis do `.env` configuradas, execute no terminal:

```bash
python popular/principal.py
```
Cada script pode ser executado individualmente:

```bash
python popular/parlamentar.py
```

## Fontes de dados

- **Câmara dos Deputados** — [dadosabertos.camara.leg.br](https://dadosabertos.camara.leg.br)
- **Senado Federal** — [legis.senado.leg.br/dadosabertos](https://legis.senado.leg.br/dadosabertos)
- **Portal da Transparência** — [api.portaldatransparencia.gov.br](https://api.portaldatransparencia.gov.br)
