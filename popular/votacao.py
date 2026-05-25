import requests
import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "test"),
    password=os.getenv("DB_PASSWORD", "testpass"),
    database=os.getenv("DB_NAME", "votovivo")
)
cursor = db.cursor()
print("Conectado ao banco para Votações.")

orgaos_cache = {}
cursor.execute("SELECT idOrgao, idApi FROM orgao")
for id_, idApi in cursor.fetchall():
    orgaos_cache[str(idApi)] = id_

def garantir_orgao(id_api_orgao, sigla=None, casa='Camara'):
    if not id_api_orgao:
        return None
    id_api_str = str(id_api_orgao)
    if id_api_str in orgaos_cache:
        return orgaos_cache[id_api_str]

    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = %s", (id_api_str, casa))
    res = cursor.fetchone()
    if res:
        orgaos_cache[id_api_str] = res[0]
        return res[0]

    cursor.execute(
        "INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
        (id_api_str, sigla or "N/A", f"Órgão não mapeado ({sigla})", casa)
    )
    db.commit()
    id_novo = cursor.lastrowid
    orgaos_cache[id_api_str] = id_novo
    return id_novo

def importar_votacoes_camara():
    print("\n--- IMPORTANDO VOTAÇÕES DA CÂMARA ---")
    
    cursor.execute("SELECT idApi, idProposicao FROM proposicao WHERE idApi IS NOT NULL")
    map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}

    meses = [
        ("2025-07-01", "2025-07-31"),
        ("2025-08-01", "2025-08-31"),
        ("2025-09-01", "2025-09-30"),
    ]

    if is_test_mode:
        meses = [("2025-08-01", "2025-08-31")]  
        print("[MODO TESTE] Buscando no mês 08/2025 na Câmara.")

    url = "https://dadosabertos.camara.leg.br/api/v2/votacoes"
    lista_ids = []

    for inicio, fim in meses:
        pagina = 1
        while True:
            params = {"dataInicio": inicio, "dataFim": fim, "itens": 100, "pagina": pagina}
            res = requests.get(url, params=params, timeout=60)
            if res.status_code != 200: break
            dados = res.json().get("dados", [])
            if not dados: break
            
            for v in dados:
                if v.get("id"): lista_ids.append(v["id"])
            
            if len(dados) < 100 or is_test_mode: break
            pagina += 1
            time.sleep(0.2)

    lista_ids = list(set(lista_ids))
    if is_test_mode:
        lista_ids = lista_ids[:5]
    print(f"Total de votações a verificar na Câmara: {len(lista_ids)}")

    start_time = time.time()
    
    for id_api in tqdm(lista_ids, desc="Votações Câmara"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Câmara interrompida após {tempo_limite_segundos}s.")
            break

        try:
            res = requests.get(f"{url}/{id_api}", timeout=60)
            if res.status_code != 200: continue
            v = res.json().get("dados", {})

            id_prop_api = None
            for p in v.get("proposicoesAfetadas", []) + v.get("objetosPossiveis", []):
                if p.get("id"):
                    id_prop_api = str(p.get("id"))
                    break
            
            id_proposicao = map_proposicoes.get(id_prop_api)

            uri_orgao = v.get("uriOrgao", "")
            id_orgao_api = uri_orgao.split("/")[-1] if uri_orgao else None
            id_orgao = garantir_orgao(id_orgao_api, v.get("siglaOrgao"), 'Camara')

            dataHora = v.get("dataHoraRegistro") or (v.get("data") + " 00:00:00" if v.get("data") else None)
            
            aprovacao = v.get("aprovacao")
            if aprovacao == 1: resultado = "Aprovado"
            elif aprovacao == 0: resultado = "Rejeitado"
            else: resultado = v.get("resultado")

            resumo = v.get("descricao")
            
            efeitos = v.get("efeitosRegistrados", [])
            descricao_lower = (resumo or "").lower()
            if "nominal" in descricao_lower or any("voto" in e.get("descEfeito", "").lower() for e in efeitos):
                tipo = "NOMINAL"
            else:
                tipo = "SIMBOLICA"

            cursor.execute("""
                INSERT INTO votacao
                (idApi, casa, idProposicao, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
                VALUES (%s, 'Camara', %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    idProposicao = VALUES(idProposicao),
                    idOrgao = VALUES(idOrgao),
                    dataHora = VALUES(dataHora),
                    resumoMateria = VALUES(resumoMateria),
                    resultadoFinal = VALUES(resultadoFinal),
                    tipoVotacao = VALUES(tipoVotacao)
            """, (str(id_api), id_proposicao, id_orgao, dataHora, resumo, resultado, tipo))
            db.commit()

        except Exception as e:
            print(f"Erro na votação {id_api}: {e}")


def importar_votacoes_senado():
    print("\n--- IMPORTANDO VOTAÇÕES DO SENADO ---")
    
    cursor.execute("""
        SELECT p.idProposicao, p.idApi 
        FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Senado' OR t.casa = 'Congresso'
        ORDER BY p.idProposicao DESC
    """)
    proposicoes = cursor.fetchall()

    if not proposicoes:
        print("AVISO: Nenhuma proposição do Senado encontrada no banco!")
        print("Por favor, rode o script 'proposicao.py' antes de buscar as votações.")
        return
    
    if is_test_mode:
        print("[MODO TESTE] Limitando a 100 proposições recentes do Senado.")
        proposicoes = proposicoes[:100]

    headers = {"Accept": "application/json"}
    start_time = time.time()

    for id_proposicao, id_api_materia in tqdm(proposicoes, desc="Matérias Senado"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            print(f"\n[LIMITE DE TEMPO] Senado interrompido após {tempo_limite_segundos}s.")
            break

        try:
            url = f"https://legis.senado.leg.br/dadosabertos/materia/{id_api_materia}/votacoes"
            res = requests.get(url, headers=headers, timeout=15)
            
            if res.status_code != 200: continue
            
            dados = res.json()
            votacoes_json = dados.get("VotacaoMateria", {}).get("Materia", {}).get("Votacoes", {}).get("Votacao", [])
            
            if isinstance(votacoes_json, dict):
                votacoes_json = [votacoes_json]
                
            for v in votacoes_json:
                id_sessao = v.get("CodigoSessao", "")
                id_votacao_sessao = v.get("CodigoSessaoVotacao", "")
                id_api_votacao = f"SEN_{id_api_materia}_{id_sessao}_{id_votacao_sessao}"

                resumo = v.get("DescricaoVotacao", "")
                resultado = v.get("Resultado", "")
                
                data_hora_str = v.get("DataSessao", "") + " 00:00:00" if v.get("DataSessao") else None
                
                votos = v.get("Votos", {}).get("VotoParlamentar", [])
                tipo = "NOMINAL" if votos else "SIMBOLICA"

                cursor.execute("""
                    INSERT INTO votacao
                    (idApi, casa, idProposicao, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
                    VALUES (%s, 'Senado', %s, NULL, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        resumoMateria = VALUES(resumoMateria),
                        resultadoFinal = VALUES(resultadoFinal),
                        tipoVotacao = VALUES(tipoVotacao)
                """, (id_api_votacao, id_proposicao, data_hora_str, resumo, resultado, tipo))
            
            db.commit()
            time.sleep(0.3)
            
        except Exception as e:
            continue

if __name__ == "__main__":
    importar_votacoes_camara()
    importar_votacoes_senado()
    
    cursor.close()
    db.close()
    print("\nImportação de Votações finalizada.")
