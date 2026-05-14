import requests
import mysql.connector
import time
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()



TIPOS_PERMITIDOS = {
    
    "PDC", "PL", "PLP", "MPV", "PLV", "PDL", "PEC", "VET",
    
    "PLS", "PLC", "PDS",
    
    "PLN", "PDN"
}


TIPOS_CAMARA = {"PDC", "PL", "PLP", "MPV", "PLV", "PDL", "PEC", "VET"}
TIPOS_SENADO = {"PLS", "PLC", "PDS", "PEC", "VET"}
TIPOS_CONGRESSO = {"PDC", "PDL", "PLN", "PDN"}


session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504]
)
session.mount('https://', HTTPAdapter(max_retries=retries))


ANO = 2025
MESES = list(range(7, 10))  


NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

print(f" Período: {ANO} - Todos os meses (Janeiro a Dezembro)")
print(f" Tipos permitidos: {', '.join(sorted(TIPOS_PERMITIDOS))}")


db = mysql.connector.connect(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "votoVivo")
)
cursor = db.cursor(buffered=True)


tipos_cache = {}

cursor.execute("SELECT idTipoProposicao, sigla, casa FROM tipoProposicao")
for id_, sigla, casa in cursor.fetchall():
    tipos_cache[(sigla.upper(), casa)] = id_

def garantir_tipo(sigla, casa):
    """Garante que o tipo existe no banco, criando se necessário"""
    chave = (sigla.upper(), casa)
    
    
    if chave in tipos_cache:
        return tipos_cache[chave]
    
    
    cursor.execute("""
        SELECT idTipoProposicao FROM tipoProposicao 
        WHERE sigla = %s AND casa = %s
    """, (sigla.upper(), casa))
    
    resultado = cursor.fetchone()
    if resultado:
        id_tipo = resultado[0]
        tipos_cache[chave] = id_tipo
        return id_tipo
    
   
    nomes_completos = {
        
        ("PDC", "Camara"): "Projeto de Decreto Legislativo",
        ("PL", "Camara"): "Projeto de Lei",
        ("PLP", "Camara"): "Projeto de Lei Complementar",
        ("MPV", "Camara"): "Medida Provisória",
        ("PLV", "Camara"): "Projeto de Lei de Conversão",
        ("PDL", "Camara"): "Projeto de Decreto Legislativo",
        ("PEC", "Camara"): "Proposta de Emenda à Constituição",
        ("VET", "Camara"): "Veto Presidencial",
        
        ("PLS", "Senado"): "Projeto de Lei do Senado Federal",
        ("PLC", "Senado"): "Projeto de Lei da Câmara dos Deputados (SF)",
        ("PDS", "Senado"): "Projeto de Decreto Legislativo (SF)",
        ("PEC", "Senado"): "Proposta de Emenda à Constituição",
        ("VET", "Senado"): "Veto Presidencial",
        
        ("PDC", "Congresso"): "Projeto de Decreto Legislativo de Autorização do Congresso Nacional",
        ("PDL", "Congresso"): "Projeto de Decreto Legislativo de Autorização do Congresso Nacional",
        ("PLN", "Congresso"): "Projeto de Lei (CN)",
        ("PDN", "Congresso"): "Projeto de Decreto Legislativo (CN)",
    }
    
    nome_completo = nomes_completos.get((sigla.upper(), casa), sigla)
    
    print(f" CRIANDO TIPO: {sigla} ({casa}) - {nome_completo}")
    
    cursor.execute("""
        INSERT INTO tipoProposicao (sigla, nome, casa)
        VALUES (%s, %s, %s)
    """, (sigla.upper(), nome_completo, casa))
    
    db.commit()
    id_tipo = cursor.lastrowid
    tipos_cache[chave] = id_tipo
    
    return id_tipo

def tipo_permitido(sigla, casa):
    """Verifica se o tipo é permitido para a casa"""
    sigla_up = sigla.upper()
    
    if casa == "Camara":
        return sigla_up in TIPOS_CAMARA
    elif casa == "Senado":
        return sigla_up in TIPOS_SENADO
    elif casa == "Congresso":
        return sigla_up in TIPOS_CONGRESSO
    return False

def obter_ultimo_dia_mes(ano, mes):
    """Retorna o último dia do mês"""
    if mes == 12:
        return 31
    
    from datetime import date, timedelta
    primeiro_dia_proximo_mes = date(ano, mes + 1, 1)
    ultimo_dia = primeiro_dia_proximo_mes - timedelta(days=1)
    return ultimo_dia.day


def importar_camara_mes(ano, mes):
    """Importa proposições da Câmara para um mês específico"""
    mes_nome = NOMES_MESES[mes]
    ultimo_dia = obter_ultimo_dia_mes(ano, mes)
    
    data_inicio = f"{ano}-{mes:02d}-01"
    data_fim = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
    
    print(f"\n    {mes_nome}/{ano}: {data_inicio} a {data_fim}")
    
    url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"

    params = {
        "dataApresentacaoInicio": data_inicio,
        "dataApresentacaoFim": data_fim,
        "itens": 100,
        "ordem": "ASC",
        "ordenarPor": "id"
    }

    pagina = 1
    total_inseridos_mes = 0
    paginas_sem_novos = 0
    MAX_PAGINAS_SEM_NOVOS = 5

    while True:
        params["pagina"] = pagina

        try:
            res = session.get(url, params=params, timeout=30)

            if res.status_code != 200:
                print(f"       Erro: Status {res.status_code}")
                break

            dados = res.json().get("dados", [])
            
            if not dados:
                break

            inseridos_pagina = 0
            
            for p in dados:
                sigla = (p.get("siglaTipo") or "").upper()
                id_api = p.get("id")
                ano_proposicao = p.get("ano")

                
                if not tipo_permitido(sigla, "Camara"):
                    continue
                
              
                id_tipo = garantir_tipo(sigla, "Camara")

                cursor.execute("""
                    INSERT IGNORE INTO proposicao
                    (idApi, idTipoProposicao, numero, ano, ementa, statusAtual)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    id_api,
                    id_tipo,
                    p.get("numero"),
                    ano_proposicao,
                    p.get("ementa", ""),
                    p.get("statusProposicao", {}).get("descricao", "Em tramitação")
                ))

                inseridos_pagina += cursor.rowcount
                total_inseridos_mes += cursor.rowcount

            db.commit()
            
            if inseridos_pagina == 0:
                paginas_sem_novos += 1
                if paginas_sem_novos >= MAX_PAGINAS_SEM_NOVOS:
                    break
            else:
                paginas_sem_novos = 0

            pagina += 1
            time.sleep(0.2)

        except Exception as e:
            print(f"       Erro: {e}")
            time.sleep(2)
            break
    
    return total_inseridos_mes


def importar_senado_mes(ano, mes):
    """Importa proposições do Senado para um mês específico"""
    mes_nome = NOMES_MESES[mes]
    ultimo_dia = obter_ultimo_dia_mes(ano, mes)
    
    data_inicio = f"{ano}-{mes:02d}-01"
    data_fim = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
    
    print(f"\n    {mes_nome}/{ano}: {data_inicio} a {data_fim}")
    
    url = "https://legis.senado.leg.br/dadosabertos/processo"
    
    params = {
        "dataInicioApresentacao": data_inicio,
        "dataFimApresentacao": data_fim,
        "v": 1,
        "quantidade": 100
    }
    
    headers = {
        "Accept": "application/json"
    }
    
    pagina = 1
    total_inseridos_mes = 0
    paginas_sem_insercao = 0
    MAX_PAGINAS_SEM_INSERCAO = 10
    
    while True:
        params["pagina"] = pagina
        
        try:
            res = requests.get(url, params=params, headers=headers, timeout=30)
            
            if res.status_code == 503:
                print("       API sobrecarregada (503). Aguardando 10s...")
                time.sleep(10)
                continue
                
            if res.status_code != 200:
                print(f"       Erro: Status {res.status_code}")
                break
            
            processos = res.json()
            
            if not isinstance(processos, list) or len(processos) == 0:
                break
            
            inseridos_pagina = 0
            
            for proc in processos:
                identificacao = proc.get("identificacao", "")
                sigla = ""
                numero = ""
                ano_proposicao = ""
                
                
                if identificacao:
                    partes = identificacao.split()
                    if len(partes) >= 2:
                        sigla = partes[0].upper()
                        num_ano = partes[1]
                        if "/" in num_ano:
                            numero, ano_proposicao = num_ano.split("/")
                
                id_api = proc.get("id")
                ementa = proc.get("ementa", "")
                data_apresentacao = proc.get("dataApresentacao", "")
                tipo_documento = proc.get("tipoDocumento", "")
                
                
                if not sigla:
                    tipo_doc = tipo_documento.upper()
                    if "DECRETO" in tipo_doc:
                        sigla = "PDL"
                    elif "LEI" in tipo_doc:
                        sigla = "PL"
                    elif "MEDIDA PROVISÓRIA" in tipo_doc:
                        sigla = "MPV"
                    elif "VETO" in tipo_doc:
                        sigla = "VET"
                    elif "REQUERIMENTO" in tipo_doc:
                        sigla = "REQ"
                    elif "PROJETO DE LEI DO SENADO" in tipo_doc:
                        sigla = "PLS"
                    elif "PROJETO DE LEI DA CÂMARA" in tipo_doc:
                        sigla = "PLC"
                    elif "PROPOSTA DE EMENDA" in tipo_doc:
                        sigla = "PEC"
                
                
                if not tipo_permitido(sigla, "Senado"):
                    continue
                
                
                id_tipo = garantir_tipo(sigla, "Senado")
                
                
                cursor.execute(
                    "SELECT idProposicao FROM proposicao WHERE idApi = %s",
                    (id_api,)
                )
                if cursor.fetchone():
                    continue
                
                
                if ano_proposicao:
                    ano_proposicao = int(ano_proposicao)
                else:
                    ano_proposicao = None
                
               
                cursor.execute("""
                    INSERT INTO proposicao
                    (idApi, idTipoProposicao, numero, ano, ementa, statusAtual)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    id_api,
                    id_tipo,
                    numero,
                    ano_proposicao,
                    ementa[:65535] if ementa else None,
                    proc.get("tramitando", "Em tramitação")
                ))
                
                inseridos_pagina += cursor.rowcount
                total_inseridos_mes += cursor.rowcount
            
            db.commit()
            
            if inseridos_pagina == 0:
                paginas_sem_insercao += 1
                if paginas_sem_insercao >= MAX_PAGINAS_SEM_INSERCAO:
                    break
            else:
                paginas_sem_insercao = 0
            
            if len(processos) < params["quantidade"]:
                break
            
            pagina += 1
            time.sleep(0.5)
            
        except Exception as e:
            print(f"       Erro: {e}")
            break
    
    return total_inseridos_mes

if __name__ == "__main__":
    try:
        print("=" * 60)
        print(" IMPORTANDO PROPOSIÇÕES DO CONGRESSO")
        print(f" PERÍODO: {ANO} - TODOS OS MESES")
        print(f" TIPOS PERMITIDOS: {', '.join(sorted(TIPOS_PERMITIDOS))}")
        print("=" * 60)
        
       
        print("\n Verificando/Criando tipos no banco...")
        
       
        for sigla in TIPOS_CAMARA:
            garantir_tipo(sigla, "Camara")
        
       
        for sigla in TIPOS_SENADO:
            garantir_tipo(sigla, "Senado")
        
        
        for sigla in TIPOS_CONGRESSO:
            garantir_tipo(sigla, "Congresso")
        
        print("    Tipos verificados/criados")
    
        
        total_camara_geral = 0
        total_senado_geral = 0
        
        
        camara_por_mes = {}
        senado_por_mes = {}
        
       
        for mes in MESES:
            print(f"\n{'='*50}")
            print(f" PROCESSANDO {NOMES_MESES[mes].upper()}/{ANO}")
            print(f"{'='*50}")
            
            print("\n CÂMARA:")
            total_camara_mes = importar_camara_mes(ANO, mes)
            total_camara_geral += total_camara_mes
            camara_por_mes[mes] = total_camara_mes
            
            print("\n SENADO:")
            total_senado_mes = importar_senado_mes(ANO, mes)
            total_senado_geral += total_senado_mes
            senado_por_mes[mes] = total_senado_mes
            
            print(f"\n RESULTADO {NOMES_MESES[mes]}/{ANO}:")
            print(f"   Câmara: {total_camara_mes} proposições")
            print(f"   Senado: {total_senado_mes} proposições")
            print(f"   Total: {total_camara_mes + total_senado_mes} proposições")
            
            time.sleep(1)  
        
        
        print("\n" + "=" * 60)
        print(" RESUMO FINAL - ANO COMPLETO")
        print("=" * 60)
        print(f" Câmara (total): {total_camara_geral} proposições")
        print(f" Senado (total): {total_senado_geral} proposições")
        print(f" Total Congresso: {total_camara_geral + total_senado_geral} proposições")
        
        print("\n DETALHAMENTO POR MÊS:")
        print("-" * 40)
        print("   Mês         | Câmara | Senado | Total")
        print("-" * 40)
        for mes in MESES:
            cam = camara_por_mes.get(mes, 0)
            sen = senado_por_mes.get(mes, 0)
            print(f"   {NOMES_MESES[mes]:9} | {cam:6} | {sen:6} | {cam+sen:5}")
        print("-" * 40)
        
       
        cursor.execute("""
            SELECT t.sigla, t.casa, COUNT(*) as total 
            FROM proposicao p
            INNER JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
            WHERE p.ano = %s OR p.ano IS NULL
            GROUP BY t.sigla, t.casa
            ORDER BY t.casa, t.sigla
        """, (ANO,))
        
        print("\n ESTATÍSTICAS FINAIS POR TIPO:")
        for sigla, casa, total_tipo in cursor.fetchall():
            print(f"   {sigla} ({casa}): {total_tipo} proposições")
        
    except Exception as e:
        print(f" Erro geral: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cursor.close()
        db.close()
        print("\n CONEXÃO COM O BANCO ENCERRADA")