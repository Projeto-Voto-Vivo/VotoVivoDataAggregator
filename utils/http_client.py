import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class ResilientSession(requests.Session):
    """
    Sessão HTTP customizada que já inclui políticas de retry (retentativas)
    para erros de servidor e tratamento automático do erro 429 (Too Many Requests).
    """
    def __init__(self, retries=5, backoff_factor=1, status_forcelist=(500, 502, 503, 504)):
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

    def get_safe(self, url, wait_429=60, max_429_retries=3, **kwargs):
        """
        Executa um GET com tratamento específico para limites de taxa (Rate Limits).
        Se a API devolver 429, o script pausa automaticamente e tenta novamente.
        """
        tentativas = 0
        while tentativas <= max_429_retries:
            kwargs.setdefault('timeout', 30)
            
            try:
                response = self.get(url, **kwargs)
                
                if response.status_code == 429:
                    print(f"\n[!] Limite da API atingido (HTTP 429).")
                    print(f"    A aguardar {wait_429} segundos antes de tentar novamente... (Tentativa {tentativas + 1}/{max_429_retries})")
                    time.sleep(wait_429)
                    tentativas += 1
                    continue
                    
                return response
                
            except requests.exceptions.RequestException as e:
                print(f"\n[!] Falha de conexão ao aceder a {url}: {e}")
                raise e
                
        return response

http_client = ResilientSession()
