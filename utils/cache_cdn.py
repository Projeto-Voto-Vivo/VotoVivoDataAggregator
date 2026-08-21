"""
Purga do cache do CDN (Cloudflare) ao fim de uma carga.

POR QUE ISTO EXISTE

A API do backend responde com `Cache-Control: s-maxage=86400`, ou seja, a
Cloudflare guarda cada resposta por 24 horas. É isso que tira a carga do
servidor — a máquina só é consultada uma vez por URL por dia, em vez de a cada
visita.

O preço desse TTL longo é que ele precisa de invalidação explícita: sem a purga,
uma carga que corrige dados no banco continua invisível no site por até um dia.
Dado novo no banco com página velha na borda é a pior combinação possível, e é
justamente o que acontece depois de uma correção como a do mapeamento de órgãos.

Uso manual (útil para forçar a atualização sem esperar a próxima carga):

    python -m utils.cache_cdn
"""

import os
import sys

import requests
from dotenv import load_dotenv

from utils.logging_config import get_logger

load_dotenv()

logger = get_logger("CacheCDN")

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
TIMEOUT_SEGUNDOS = 30


def purgar_cdn() -> bool:
    """
    Invalida todo o cache da zona. Devolve True se a Cloudflare confirmou.

    NUNCA levanta excecao. Quando esta funcao roda, os dados ja foram gravados
    no banco: uma falha de rede aqui nao pode transformar uma carga
    bem-sucedida em pipeline com erro. O pior caso de um insucesso e o site
    continuar servindo o cache antigo ate o s-maxage expirar — ruim, mas nao e
    motivo para reprovar a carga.
    """
    zona = os.getenv("CLOUDFLARE_ZONE_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    if not zona or not token:
        # Ausencia de credenciais nao e erro: em desenvolvimento nao ha CDN na
        # frente da API.
        logger.info(
            "CLOUDFLARE_ZONE_ID/CLOUDFLARE_API_TOKEN nao configurados; purga do CDN ignorada."
        )
        return False

    try:
        resposta = requests.post(
            f"{CLOUDFLARE_API}/zones/{zona}/purge_cache",
            headers={"Authorization": f"Bearer {token}"},
            # Purga total em vez de por URL: os filtros vivem na query string, o
            # que torna o conjunto de URLs efetivamente ilimitado. E uma carga
            # do ETL mexe em tudo mesmo.
            json={"purge_everything": True},
            timeout=TIMEOUT_SEGUNDOS,
        )
    except requests.exceptions.RequestException as erro:
        logger.error(
            f"Falha de rede ao purgar o CDN: {erro}. "
            f"O site segue servindo o cache antigo ate o s-maxage expirar."
        )
        return False

    try:
        corpo = resposta.json()
    except ValueError:
        logger.error(
            f"Resposta nao-JSON da Cloudflare (HTTP {resposta.status_code}): "
            f"{resposta.text[:200]}"
        )
        return False

    if resposta.status_code == 200 and corpo.get("success"):
        logger.info("Cache do CDN purgado; a proxima requisicao vai a origem.")
        return True

    # Loga alto: o sintoma de uma purga que falhou (site mostrando dado velho)
    # e invisivel para quem operou a carga.
    logger.error(
        f"Cloudflare recusou a purga (HTTP {resposta.status_code}): "
        f"{corpo.get('errors') or corpo}"
    )
    return False


if __name__ == "__main__":
    sys.exit(0 if purgar_cdn() else 1)
