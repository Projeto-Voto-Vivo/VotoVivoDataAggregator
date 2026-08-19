import hashlib
import json
import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class RespostaCache:
    """Objeto mínimo compatível com requests.Response, servido do cache em disco."""

    def __init__(self, url, status_code, text):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")

    def json(self):
        return json.loads(self.text)


class RateLimitAbort(BaseException):
    """
    Levantada quando a API continua a devolver HTTP 429 mesmo depois das pausas
    de segurança crescentes. Herda de BaseException (não de Exception) de propósito,
    para atravessar os `except Exception`/`except KeyboardInterrupt` espalhados pelos
    scripts e parar o processo em vez de ser engolida e continuar a martelar a API.
    """


class ResilientSession(requests.Session):
    """
    Sessão HTTP customizada que já inclui políticas de retry (retentativas)
    para erros de servidor e tratamento automático do erro 429 (Too Many Requests).
    """
    def __init__(self, retries=5, backoff_factor=1, status_forcelist=(500, 502, 503, 504),
                 max_holds=3, hold_inicial_segundos=300):
        super().__init__()

        retry_strategy = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.mount('http://', adapter)
        self.mount('https://', adapter)

        # Pausas de segurança: se mesmo depois das tentativas normais a API continuar
        # a devolver 429, em vez de desistir já e seguir para o próximo item (o que
        # numa lista com milhares de itens dá a sensação de o script estar "preso"),
        # entra-se em pausas cada vez mais longas. Se mesmo assim continuar bloqueado,
        # o script é interrompido em vez de continuar indefinidamente.
        self.max_holds = max_holds
        self.hold_inicial_segundos = hold_inicial_segundos

        # Staging (cache HTTP em disco), controlado por variáveis de ambiente:
        #   ETL_HTTP_CACHE=gravar  -> toda resposta 200 é gravada em disco
        #   ETL_HTTP_CACHE=ler     -> serve do disco quando existir (reprocesso
        #                             sem bater na API); o que faltar é buscado
        #                             na API e gravado
        #   (vazio/ausente)        -> desligado
        # Permite reprocessar transformações ilimitadas vezes sem custo de rede.
        self.cache_modo = os.getenv("ETL_HTTP_CACHE", "").strip().lower()
        self.cache_dir = os.getenv("ETL_HTTP_CACHE_DIR", "staging/http_cache")

    def _cache_caminho(self, url, params):
        chave = url
        if params:
            chave += "?" + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        nome = hashlib.sha1(chave.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{nome}.json")

    def _cache_ler(self, url, params):
        if self.cache_modo != "ler":
            return None
        caminho = self._cache_caminho(url, params)
        if not os.path.exists(caminho):
            return None
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
            return RespostaCache(dados["url"], dados["status_code"], dados["text"])
        except Exception:
            return None

    def _cache_gravar(self, url, params, response):
        if self.cache_modo not in ("gravar", "ler") or response.status_code != 200:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_caminho(url, params), "w", encoding="utf-8") as f:
                json.dump(
                    {"url": str(response.url), "status_code": response.status_code, "text": response.text},
                    f, ensure_ascii=False,
                )
        except Exception:
            pass  # staging nunca pode derrubar a carga

    def get_safe(self, url, wait_429=60, max_429_retries=3, **kwargs):
        """
        Executa um GET com tratamento específico para limites de taxa (Rate Limits).
        Se a API devolver 429, o script pausa automaticamente e tenta novamente.
        Se o limite persistir mesmo depois destas tentativas normais, entra numa
        pausa de segurança cada vez mais longa (5, 10, 20 minutos); se mesmo assim
        continuar bloqueado, levanta RateLimitAbort para parar o script em segurança.
        """
        resposta_cache = self._cache_ler(url, kwargs.get("params"))
        if resposta_cache is not None:
            return resposta_cache

        for hold in range(self.max_holds + 1):
            tentativas = 0
            response = None
            while tentativas <= max_429_retries:
                kwargs.setdefault('timeout', 30)

                try:
                    response = self.get(url, **kwargs)

                    if response.status_code == 429:
                        print(f"\n[!] Limite da API atingido (HTTP 429) em {url}.")
                        print(f"    A aguardar {wait_429} segundos antes de tentar novamente... (Tentativa {tentativas + 1}/{max_429_retries})")
                        time.sleep(wait_429)
                        tentativas += 1
                        continue

                    self._cache_gravar(url, kwargs.get("params"), response)
                    return response

                except requests.exceptions.RequestException as e:
                    print(f"\n[!] Falha de conexão ao aceder a {url}: {e}")
                    raise e

            if hold >= self.max_holds:
                break

            hold_segundos = self.hold_inicial_segundos * (2 ** hold)
            print(f"\n[!!] A API continua a devolver HTTP 429 mesmo depois das tentativas normais.")
            print(f"     Pausa de segurança {hold + 1}/{self.max_holds}: a aguardar {hold_segundos // 60} minutos antes de tentar de novo...")
            time.sleep(hold_segundos)

        raise RateLimitAbort(
            f"A API em {url} continua a devolver HTTP 429 mesmo depois de {self.max_holds} pausas de "
            f"segurança crescentes. A interromper o script para não ficar às voltas indefinidamente."
        )

http_client = ResilientSession()
