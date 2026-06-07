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
