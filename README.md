# VotoVivoDataAggregator

### Chave da API do Portal da Transparência para 'emenda' e 'emendaDocumento'

- `/api-de-dados/emendas`
- `/api-de-dados/emendas/documentos/{codigoEmenda}`

Para executar este script, é obrigatório possuir uma chave válida da API do Portal da Transparência.

Essa chave é individual e deve ser obtida por quem for executar o script, através do GOV (só assim é possível consumir esses dados)

Ela não deve ser compartilhada, versionada ou incluída diretamente no código.

A chave deve ser configurada apenas no arquivo `.env` local da máquina ou do ambiente de execução:

SIGA O .ENV.EXAMPLE

```env
PORTAL_TRANSPARENCIA_API_KEY=sua_chave_da_api