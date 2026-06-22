import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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

    def get_safe(self, url, wait_429=60, max_429_retries=3, **kwargs):
        """
        Executa um GET com tratamento específico para limites de taxa (Rate Limits).
        Se a API devolver 429, o script pausa automaticamente e tenta novamente.
        Se o limite persistir mesmo depois destas tentativas normais, entra numa
        pausa de segurança cada vez mais longa (5, 10, 20 minutos); se mesmo assim
        continuar bloqueado, levanta RateLimitAbort para parar o script em segurança.
        """
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
