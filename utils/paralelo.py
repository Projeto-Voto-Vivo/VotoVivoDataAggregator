"""Busca HTTP em paralelo com escrita sequencial.

Só o *fetch* é paralelizado. A gravação no banco e o checkpoint continuam na
ORDEM ORIGINAL da fila — o checkpoint guarda "último id concluído" assumindo
ordem crescente; gravar fora de ordem faria uma retomada pular itens.

Configuração por ambiente:
    ETL_WORKERS        threads simultâneas de fetch (padrão 4; 1 desliga o paralelismo)
    ETL_TAMANHO_LOTE   itens buscados por rodada (padrão 40)

Paralelismo amplifica a chance de HTTP 429; o ResilientSession já trata isso
com pausas crescentes, mas comece com poucos workers e observe o log.
"""

import os
from concurrent.futures import ThreadPoolExecutor

WORKERS_PADRAO = int(os.getenv("ETL_WORKERS", "4"))
TAMANHO_LOTE_PADRAO = int(os.getenv("ETL_TAMANHO_LOTE", "40"))


def em_lotes(itens, tamanho=None):
    tamanho = tamanho or TAMANHO_LOTE_PADRAO
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def buscar_lote(itens, buscar, workers=None):
    """Executa `buscar(item)` em paralelo e devolve os resultados NA ORDEM ORIGINAL.

    Uma exceção em um item não derruba o lote: ela é devolvida no lugar do
    resultado daquele item (o chamador testa `isinstance(r, Exception)`).
    RateLimitAbort herda de BaseException e continua atravessando — é o
    comportamento desejado: parar o script em vez de martelar a API.
    """
    def seguro(item):
        try:
            return buscar(item)
        except Exception as e:
            return e

    workers = workers or WORKERS_PADRAO
    if workers <= 1 or len(itens) <= 1:
        return [seguro(item) for item in itens]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(seguro, itens))
