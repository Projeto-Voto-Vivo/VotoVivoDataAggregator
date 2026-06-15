import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv
import mysql.connector
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

TIPOS_PERMITIDOS = {
    "PDC", "PL", "PLP", "MPV", "PLV", "PDL", "PEC", "VET",
    "PLS", "PLC", "PDS", "PLN", "PDN",
}

TIPOS_CAMARA = {"PDC", "PL", "PLP", "MPV", "PLV", "PDL", "PEC", "VET"}

TIPOS_SENADO = {"PLS", "PLC", "PDS", "PEC", "VET", "PL", "PDL", "MPV"}
TIPOS_CONGRESSO = {"PDC", "PDL", "PLN", "PDN"}

session = requests.Session()
retries = Retry(
    total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504]
)
session.mount("https://", HTTPAdapter(max_retries=retries))

# =====================================================================
# LÓGICA DINÂMICA DE DATAS (Substitui os valores em hardcode)
# =====================================================================
def gerar_cronograma_dinamico():
    ano_inicio = int(os.getenv("ANO_INICIO_ETL", 2025))
    mes_inicio = int(os.getenv("MES_INICIO_ETL", 5))
    ano_atual = datetime.now().year
    mes_atual = datetime.now().month
    cronograma = []
    
    for ano in range(ano_inicio, ano_atual + 1):
        mes_inicial_do_ano = mes_inicio if ano == ano_inicio else 1
        mes_final_do_ano = mes_atual if ano == ano_atual else 12
        if mes_inicial_do_ano <= mes_final_do_ano:
            cronograma.append({
                "ano": ano,
                "meses": list(range(mes_inicial_do_ano, mes_final_do_ano + 1))
            })
    return cronograma

CRONOGRAMA_BUSCA = gerar_cronograma_dinamico()

NOMES_MESES = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

# Atualização de tag do script para não gerar conflito com execuções antigas
ano_inicio_str = os.getenv("ANO_INICIO_ETL", "2025")
mes_inicio_str = os.getenv("MES_INICIO_ETL", "5")
script_camara = "popular/proposicao.py#camara_dinamico"
script_senado = "popular/proposicao.py#senado_dinamico"

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo"),
    )
    cursor = db.cursor(buffered=True)
    print("[+] Conexão com o banco de dados estabelecida com sucesso.\n")
except mysql.connector.Error as err:
    print(f"[!] Erro ao conectar ao banco: {err}")
    sys.exit(1)


def obter_ultimo_checkpoint(nome_script, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value


def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = '''
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    '''
    cursor.execute(query, (nome_script, str(valor_parametro)))


tipos_cache = {}

cursor.execute("SELECT idTipoProposicao, sigla, casa FROM tipoProposicao")
for id_, sigla, casa in cursor.fetchall():
    tipos_cache[(sigla.upper(), casa)] = id_


def garantir_tipo(sigla, casa):
    chave = (sigla.upper(), casa)

    if chave in tipos_cache:
        return tipos_cache[chave]

    cursor.execute(
        '''
        SELECT idTipoProposicao FROM tipoProposicao 
        WHERE sigla = %s AND casa = %s
    ''',
        (sigla.upper(), casa),
    )

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
        ("PL", "Senado"): "Projeto de Lei",
        ("PDL", "Senado"): "Projeto de Decreto Legislativo",
        ("MPV", "Senado"): "Medida Provisória",
        ("PDC", "Congresso"): "Projeto de Decreto Legislativo de Autorização do Congresso Nacional",
        ("PDL", "Congresso"): "Projeto de Decreto Legislativo de Autorização do Congresso Nacional",
        ("PLN", "Congresso"): "Projeto de Lei (CN)",
        ("PDN", "Congresso"): "Projeto de Decreto Legislativo (CN)",
    }

    nome_completo = nomes_completos.get((sigla.upper(), casa), sigla)
    print(f" CRIANDO TIPO INEXISTENTE: {sigla} ({casa}) - {nome_completo}")

    cursor.execute(
        '''
        INSERT INTO tipoProposicao (sigla, nome, casa)
        VALUES (%s, %s, %s)
    ''',
        (sigla.upper(), nome_completo, casa),
    )

    db.commit()
    id_tipo = cursor.lastrowid
    tipos_cache[chave] = id_tipo
    return id_tipo


def tipo_permitido(sigla, casa):
    sigla_up = sigla.upper()
    if casa == "Camara":
        return sigla_up in TIPOS_CAMARA
    elif casa == "Senado":
        return sigla_up in TIPOS_SENADO
    elif casa == "Congresso":
        return sigla_up in TIPOS_CONGRESSO
    return False


def obter_ultimo_dia_mes(ano, mes):
    if mes == 12:
        return 31
    from datetime import date, timedelta

    primeiro_dia_proximo_mes = date(ano, mes + 1, 1)
    ultimo_dia = primeiro_dia_proximo_mes - timedelta(days=1)
    return ultimo_dia.day


def importar_camara_mes(ano, mes):
    mes_nome = NOMES_MESES[mes]
    ultimo_dia = obter_ultimo_dia_mes(ano, mes)

    data_inicio = f"{ano}-{mes:02d}-01"
    data_fim = f"{ano}-{mes:02d}-{ultimo_dia:02d}"

    print(f"\n    [CÂMARA] {mes_nome}/{ano}: {data_inicio} a {data_fim}")

    checkpoint_atual = obter_ultimo_checkpoint(
        script_camara, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1"
    )
    ano_chk, mes_chk, pagina_chk = map(int, checkpoint_atual.split("_"))

    if ano < ano_chk or (ano == ano_chk and mes < mes_chk):
        print(
            f"      [i] Período {mes}/{ano} já concluído em execuções anteriores. FORÇANDO RETOMADA."
        )

    url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
    params = {
        "dataApresentacaoInicio": data_inicio,
        "dataApresentacaoFim": data_fim,
        "itens": 100,
        "ordem": "ASC",
        "ordenarPor": "id",
    }

    pagina = pagina_chk if (mes == mes_chk and ano == ano_chk) else 1
    total_inseridos_mes = 0
    paginas_sem_novos = 0
    MAX_PAGINAS_SEM_NOVOS = 5
    start_time = time.time()

    while True:
        params["pagina"] = pagina
        print(f"     -> Lendo Página {pagina} da API da Câmara...")

        try:
            res = session.get(url, params=params, timeout=30)
            if res.status_code != 200:
                print(
                    f"       [!] Erro na API da Câmara: Status {res.status_code}"
                )
                break

            dados = res.json().get("dados", [])
            if not dados:
                print("       [+] Fim das páginas deste mês atingido.")
                break

            if db.in_transaction:
                db.commit()

            db.start_transaction()
            inseridos_pagina = 0

            for p in dados:
                sigla = (p.get("siglaTipo") or "").upper()
                id_api = p.get("id")
                ano_proposicao = p.get("ano")

                if not tipo_permitido(sigla, "Camara"):
                    continue

                id_tipo = garantir_tipo(sigla, "Camara")

                cursor.execute(
                    '''
                    INSERT IGNORE INTO proposicao
                    (idApi, idTipoProposicao, numero, ano, ementa, statusAtual)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''',
                    (
                        id_api,
                        id_tipo,
                        p.get("numero"),
                        ano_proposicao,
                        p.get("ementa", ""),
                        p.get("statusProposicao", {}).get(
                            "descricao", "Em tramitação"
                        ),
                    ),
                )

                inseridos_pagina += cursor.rowcount
                total_inseridos_mes += cursor.rowcount

            salvar_checkpoint_transacao(
                script_camara, f"{ano}_{mes}_{pagina}"
            )
            db.commit()

            if inseridos_pagina == 0:
                paginas_sem_novos += 1
                if paginas_sem_novos >= MAX_PAGINAS_SEM_NOVOS:
                    break
            else:
                paginas_sem_novos = 0

            pagina += 1
            time.sleep(0.2)

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                print(f"\n[LIMITE DE TEMPO] Câmara interrompida após {tempo_limite_segundos}s na página {pagina}.")
                break

        except Exception as e:
            print(
                f"       [!] Erro crítico na execução da página {pagina}: {e}"
            )
            if db.in_transaction:
                db.rollback()
            break

    proximo_mes = mes + 1 if mes < 12 else 1
    proximo_ano = ano if mes < 12 else ano + 1

    if db.in_transaction: db.commit()
    db.start_transaction()
    salvar_checkpoint_transacao(
        script_camara, f"{proximo_ano}_{proximo_mes}_1"
    )
    db.commit()

    return total_inseridos_mes


def importing_senado_mes(ano, mes):
    mes_nome = NOMES_MESES[mes]
    ultimo_dia = obter_ultimo_dia_mes(ano, mes)

    data_inicio = f"{ano}-{mes:02d}-01"
    data_fim = f"{ano}-{mes:02d}-{ultimo_dia:02d}"

    print(f"\n    [SENADO] {mes_nome}/{ano}: {data_inicio} a {data_fim}")

    checkpoint_atual = obter_ultimo_checkpoint(
        script_senado, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1"
    )
    ano_chk, mes_chk, pagina_chk = map(int, checkpoint_atual.split("_"))

    if ano < ano_chk or (ano == ano_chk and mes < mes_chk):
        print(
            f"      [i] Período {mes}/{ano} já concluído em execuções anteriores. FORÇANDO RETOMADA."
        )

    url = "https://legis.senado.leg.br/dadosabertos/processo"
    params = {
        "dataInicioApresentacao": data_inicio,
        "dataFimApresentacao": data_fim,
        "v": 1,
        "quantidade": 100,
    }
    headers = {"Accept": "application/json"}

    pagina = pagina_chk if (mes == mes_chk and ano == ano_chk) else 1
    total_inseridos_mes = 0
    paginas_sem_insercao = 0
    MAX_PAGINAS_SEM_INSERCAO = 10
    start_time = time.time()

    while True:
        params["pagina"] = pagina
        print(f"     -> Lendo Página {pagina} da API do Senado...")

        try:
            res = session.get(url, params=params, headers=headers, timeout=30)

            if res.status_code == 503:
                print("       [!] API sobrecarregada (503). Aguardando 10s...")
                time.sleep(10)
                continue

            if res.status_code != 200:
                print(
                    f"       [!] Erro na API do Senado: Status {res.status_code}"
                )
                break

            processos = res.json()

            if not isinstance(processos, list) or len(processos) == 0:
                print("       [+] Fim das páginas deste mês atingido.")
                break

            if db.in_transaction:
                db.commit()

            db.start_transaction()
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
                
                cursor.execute("SELECT 1 FROM proposicao WHERE idApi = %s", (id_api,))
                if cursor.fetchone():
                    continue

                ano_final = int(ano_proposicao) if ano_proposicao else None

                cursor.execute(
                    '''
                    INSERT INTO proposicao
                    (idApi, idTipoProposicao, numero, ano, ementa, statusAtual)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''',
                    (
                        id_api,
                        id_tipo,
                        numero,
                        ano_final,
                        ementa[:65535] if ementa else None,
                        proc.get("tramitando", "Em tramitação"),
                    ),
                )

                inseridos_pagina += cursor.rowcount
                total_inseridos_mes += cursor.rowcount

            salvar_checkpoint_transacao(
                script_senado, f"{ano}_{mes}_{pagina}"
            )
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

            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
                print(f"\n[LIMITE DE TEMPO] Senado interrompido após {tempo_limite_segundos}s na página {pagina}.")
                break

        except Exception as e:
            print(
                f"       [!] Erro crítico na execução da página {pagina}: {e}"
            )
            if db.in_transaction:
                db.rollback()
            break

    proximo_mes = mes + 1 if mes < 12 else 1
    proximo_ano = ano if mes < 12 else ano + 1

    if db.in_transaction: db.commit()
    db.start_transaction()
    salvar_checkpoint_transacao(
        script_senado, f"{proximo_ano}_{proximo_mes}_1"
    )
    db.commit()

    return total_inseridos_mes


if __name__ == "__main__":
    try:
        print("=" * 60)
        print(" IMPORTANDO PROPOSIÇÕES DO CONGRESSO")
        print(" PERÍODO FOCO: BASEADO EM .ENV")
        print("=" * 60)

        print("\n Verificando tabelas de tipos de suporte no banco...")
        for sigla in TIPOS_CAMARA:
            garantir_tipo(sigla, "Camara")
        for sigla in TIPOS_SENADO:
            garantir_tipo(sigla, "Senado")
        for sigla in TIPOS_CONGRESSO:
            garantir_tipo(sigla, "Congresso")
        print("      [+] Catálogo de tipos carregado com sucesso.")

        total_camara_geral = 0
        total_senado_geral = 0
        historico_consolidado = []

        for bloco in CRONOGRAMA_BUSCA:
            ano_alvo = bloco["ano"]
            for mes_alvo in bloco["meses"]:
                print(f"\n{'='*50}")
                print(
                    f" PROCESSANDO TIMELINE DE {NOMES_MESES[mes_alvo].upper()}/{ano_alvo}"
                )
                print(f"{'='*50}")

                total_camara_mes = importar_camara_mes(ano_alvo, mes_alvo)
                total_camara_geral += total_camara_mes

                total_senado_mes = importing_senado_mes(ano_alvo, mes_alvo)
                total_senado_geral += total_senado_mes

                historico_consolidado.append(
                    {
                        "ano": ano_alvo,
                        "mes": mes_alvo,
                        "camara": total_camara_mes,
                        "senado": total_senado_mes,
                    }
                )

                print(
                    f"\n RESULTADO PARCIAL {NOMES_MESES[mes_alvo]}/{ano_alvo}:"
                )
                print(
                    f"   Câmara: {total_camara_mes} novas proposições adicionadas"
                )
                print(
                    f"   Senado: {total_senado_mes} novas proposições adicionadas"
                )

                time.sleep(1)

        print("\n" + "=" * 60)
        print(" RESUMO FINAL DE CONSOLIDAÇÃO DA BASE DE DADOS")
        print("=" * 60)
        print(f" Novas Proposições da Câmara: {total_camara_geral}")
        print(f" Novas Proposições do Senado: {total_senado_geral}")
        print(
            f" Total de Registros Inseridos: {total_camara_geral + total_senado_geral}"
        )

        print("\n DETALHAMENTO HISTÓRICO:")
        print("-" * 45)
        print("   Mês/Ano       | Câmara | Senado | Total")
        print("-" * 45)
        for h in historico_consolidado:
            label = f"{NOMES_MESES[h['mes']][:3]}/{h['ano']}"
            tot = h["camara"] + h["senado"]
            print(
                f"   {label:13} | {h['camara']:6} | {h['senado']:6} | {tot:5}"
            )
        print("-" * 45)

    except KeyboardInterrupt:
        print(
            "\n\n[!] Execução cancelada de forma forçada via terminal. Aplicando Rollback nos lotes..."
        )
        if db.in_transaction:
            db.rollback()
    except Exception as e:
        print(f"\n [!] Ocorreu um erro geral na aplicação: {e}")
    finally:
        cursor.close()
        db.close()
        print("\n[+] CONEXÃO COM O BANCO ENCERRADA DE FORMA SEGURA. [FIM]")

