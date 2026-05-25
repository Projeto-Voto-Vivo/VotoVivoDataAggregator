import os
import requests
import mysql.connector
import time
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

BASE_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "testpass"),
    database=os.getenv("DB_NAME", "votovivo")
)
cursor = db.cursor()

def extrair_id_orgao(uri):
    """Extrai o ID da Câmara do final da URI"""
    try:
        return str(int(uri.split("/")[-1]))
    except:
        return None

def buscar_nome_orgao(id_api):
    """Busca o nome descritivo do órgão na API da Câmara"""
    try:
        url = f"{BASE_URL_CAMARA}/orgaos/{id_api}"
        r = requests.get(url, timeout=10)

        if r.status_code == 200:
            nome = r.json().get("dados", {}).get("nome")
            return nome[:500] if nome else None
    except:
        pass
    return None

def importar_orgaos_camara():
    """Importa órgãos (Comissões e Plenário) lendo as tramitações da Câmara"""
    cursor.execute("""
        SELECT p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Camara' OR t.casa = 'Congresso'
    """)
    proposicoes = cursor.fetchall()

    if is_test_mode:
        limite_itens = 20
        print(f"\n[MODO TESTE] Limitando a verificação a apenas {limite_itens} proposições na Câmara.")
        proposicoes = proposicoes[:limite_itens]

    total = len(proposicoes)
    print(f"\nTotal de proposições a verificar (Câmara): {total}")
    
    start_time = time.time()

    for (id_api,) in tqdm(proposicoes, desc="Importando órgãos da Câmara", unit="proposição"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Execução interrompida na Câmara após {tempo_limite_segundos} segundos.")
            break
            
        try:
            url = f"{BASE_URL_CAMARA}/proposicoes/{id_api}/tramitacoes"
            res = requests.get(url, timeout=10)

            if res.status_code != 200:
                continue

            dados = res.json().get("dados", [])
            orgaos_unicos = {}

            for t in dados:
                uri = t.get("uriOrgao")
                sigla = t.get("siglaOrgao")

                id_orgao_api = extrair_id_orgao(uri)

                if id_orgao_api and id_orgao_api not in orgaos_unicos:
                    orgaos_unicos[id_orgao_api] = (sigla or "N/A")[:50]

            for id_orgao_api, sigla in orgaos_unicos.items():
                cursor.execute(
                    "SELECT idOrgao, nome FROM orgao WHERE idApi = %s AND casa = 'Camara'",
                    (id_orgao_api,)
                )
                existente = cursor.fetchone()

                if existente:
                    if not existente[1]:
                        nome = buscar_nome_orgao(id_orgao_api)
                        if nome:
                            cursor.execute("""
                                UPDATE orgao SET nome = %s WHERE idApi = %s AND casa = 'Camara'
                            """, (nome, id_orgao_api))
                else:
                    nome = buscar_nome_orgao(id_orgao_api)
                    cursor.execute("""
                        INSERT INTO orgao (idApi, sigla, nome, casa)
                        VALUES (%s, %s, %s, 'Camara')
                        ON DUPLICATE KEY UPDATE
                            sigla = VALUES(sigla),
                            nome = VALUES(nome)
                    """, (id_orgao_api, sigla, nome))

            db.commit()
            time.sleep(0.1)

        except Exception as e:
            print(f"\nErro na proposição {id_api}: {e}")

def importar_orgaos_senado():
    """Importa todos os colegiados ativos do Senado Federal usando o endpoint atualizado"""
    print("\nImportando colegiados/comissões do Senado...")
    
    url = f"{BASE_URL_SENADO}/comissao/lista/colegiados"
    headers = {"Accept": "application/json"}
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            dados = res.json()
            
            comissoes = dados.get("ListaColegiados", {}).get("Colegiados", {}).get("Colegiado", [])
            
            if isinstance(comissoes, dict):
                comissoes = [comissoes]
                
            if is_test_mode:
                limite_itens = 5
                print(f"[MODO TESTE] Limitando a importação a {limite_itens} colegiados no Senado.")
                comissoes = comissoes[:limite_itens]

            for c in comissoes:
                id_api = str(c.get("Codigo"))
                sigla = c.get("Sigla", "N/A")[:50]
                
                nome = c.get("Nome", "Sem Nome")[:500] 
                
                sigla_casa = c.get("SiglaCasa", "")
                
                if sigla_casa == "CN":
                    casa = "Congresso"
                elif sigla_casa == "SF":
                    casa = "Senado"
                else:
                    casa = 'Congresso' if 'Mista' in nome or 'Misto' in nome else 'Senado'

                cursor.execute("""
                    INSERT INTO orgao (idApi, sigla, nome, casa)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        sigla = VALUES(sigla),
                        nome = VALUES(nome),
                        casa = VALUES(casa)
                """, (id_api, sigla, nome, casa))
            
            db.commit()
            print(f"Sucesso! {len(comissoes)} órgãos do Senado processados.")
        else:
            print(f"Erro ao buscar comissões do Senado: Status {res.status_code}")
            
    except Exception as e:
        print(f"Erro na importação do Senado: {e}")

if __name__ == "__main__":
    importar_orgaos_camara()
    importar_orgaos_senado()
    
    cursor.close()
    db.close()
    print("\nImportação de órgãos concluída.")
