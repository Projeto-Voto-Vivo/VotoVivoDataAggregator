import os
import sys
import time
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import mysql.connector
from tqdm import tqdm

from utils.http_client import http_client

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo"),
    )
    cursor = db.cursor()
except mysql.connector.Error:
    sys.exit(1)

script_camara = "popular/votacao.py#camara_dinamico_v2"
script_senado = "popular/votacao.py#senado_dinamico_v2"

def obter_ultimo_checkpoint(nome_script, default_value="2025_5_1"):
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

def obter_ultimo_dia_mes(ano, mes):
    if mes == 12: return 31
    return (date(ano, mes + 1, 1) - timedelta(days=1)).day

def gerar_cronograma_dinamico():
    ano_inicio = int(os.getenv("ANO_INICIO_ETL", 2025))
    mes_inicio = int(os.getenv("MES_INICIO_ETL", 5))
    ano_atual = datetime.now().year
    mes_atual = datetime.now().month
    cronograma = []
    for ano in range(ano_inicio, ano_atual + 1):
        m_inicio = mes_inicio if ano == ano_inicio else 1
        m_fim = mes_atual if ano == ano_atual else 12
        if m_inicio <= m_fim:
            meses_list = []
            for mes in range(m_inicio, m_fim + 1):
                ud = obter_ultimo_dia_mes(ano, mes)
                meses_list.append((mes, f"{ano}-{mes:02d}-01", f"{ano}-{mes:02d}-{ud:02d}"))
            cronograma.append({"ano": ano, "meses": meses_list})
    return cronograma

orgaos_cache = {}
cursor.execute("SELECT idOrgao, idApi FROM orgao")
for id_, idApi in cursor.fetchall():
    orgaos_cache[str(idApi)] = id_

def garantizar_orgao(id_api_orgao, sigla=None, casa="Camara"):
    if not id_api_orgao: return None
    id_api_str = str(id_api_orgao)
    if id_api_str in orgaos_cache: return orgaos_cache[id_api_str]
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = %s", (id_api_str, casa))
    res = cursor.fetchone()
    if res:
        orgaos_cache[id_api_str] = res[0]
        return res[0]
    cursor.execute("INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
                   (id_api_str, sigla or "N/A", f"Órgão não mapeado ({sigla})", casa))
    db.commit()
    id_novo = cursor.lastrowid
    orgaos_cache[id_api_str] = id_novo
    return id_novo

def importar_votacoes_camara():
    cursor.execute("SELECT idApi, idProposicao FROM proposicao WHERE idApi IS NOT NULL")
    map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}
    
    cronograma_camara = gerar_cronograma_dinamico()

    ano_inicio_str = os.getenv("ANO_INICIO_ETL", "2025")
    mes_inicio_str = os.getenv("MES_INICIO_ETL", "5")
    checkpoint_atual = obter_ultimo_checkpoint(script_camara, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1")
    ano_chk, mes_chk, pagina_chk = map(int, checkpoint_atual.split('_'))

    url = "https://dadosabertos.camara.leg.br/api/v2/votacoes"
    start_time = time.time()

    for bloco in cronograma_camara:
        ano = bloco["ano"]
        for num_mes, inicio, fim in bloco["meses"]:
            if ano < ano_chk or (ano == ano_chk and num_mes < mes_chk): continue

            pagina = pagina_chk if (ano == ano_chk and num_mes == mes_chk) else 1

            while True:
                if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: return
                params = {"dataInicio": inicio, "dataFim": fim, "itens": 100, "pagina": pagina}

                try:
                    res = http_client.get_safe(url, params=params, timeout=60)
                    if res.status_code != 200: break
                    dados = res.json().get("dados", [])
                    if not dados: break

                    if db.in_transaction: db.commit()
                    db.start_transaction()

                    for v in dados:
                        id_api = v.get("id")
                        if not id_api: continue

                        try:
                            res_detalhe = http_client.get_safe(f"{url}/{id_api}", timeout=60)
                            if res_detalhe.status_code != 200: continue
                            v_detalhe = res_detalhe.json().get("dados", {})

                            id_proposicao = None
                            elementos_afetados = v_detalhe.get("proposicoesAfetadas", []) + v_detalhe.get("objetosPossiveis", [])
                            for p in elementos_afetados:
                                if p.get("id"):
                                    id_api_verificar = str(p.get("id"))
                                    if id_api_verificar in map_proposicoes:
                                        id_proposicao = map_proposicoes[id_api_verificar]
                                        break

                            uri_orgao = v_detalhe.get("uriOrgao", "")
                            id_orgao_api = uri_orgao.split("/")[-1] if uri_orgao else None
                            id_orgao = garantizar_orgao(id_orgao_api, v_detalhe.get("siglaOrgao"), 'Camara')

                            dataHora = v_detalhe.get("dataHoraRegistro") or (v_detalhe.get("data") + " 00:00:00" if v_detalhe.get("data") else None)
                            aprovacao = v_detalhe.get("aprovacao")
                            if aprovacao == 1: resultado = "Aprovado"
                            elif aprovacao == 0: resultado = "Rejeitado"
                            else: resultado = v_detalhe.get("resultado")
                            resumo = v_detalhe.get("descricao")

                            tipo_api = (v_detalhe.get("tipoVotacao") or "").upper()
                            tipo = "NOMINAL" if tipo_api == "NOMINAL" else "SIMBOLICA"

                            cursor.execute('''
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
                            ''', (str(id_api), id_proposicao, id_orgao, dataHora, resumo, resultado, tipo))

                        except Exception:
                            continue

                    salvar_checkpoint_transacao(script_camara, f"{ano}_{num_mes}_{pagina}")
                    db.commit()

                    if len(dados) < 100: break
                    pagina += 1
                    time.sleep(0.2)

                except Exception:
                    if db.in_transaction: db.rollback()
                    break

            if db.in_transaction: db.commit()
            proximo_mes = num_mes + 1 if num_mes < 12 else 1
            proximo_ano = ano if num_mes < 12 else ano + 1
            db.start_transaction()
            salvar_checkpoint_transacao(script_camara, f"{proximo_ano}_{proximo_mes}_1")
            db.commit()

def importar_votacoes_senado():
    ano_inicio = int(os.getenv("ANO_INICIO_ETL", 2025))
    ano_atual = datetime.now().year
    anos_tupla = tuple(range(ano_inicio, ano_atual + 1))
    
    if anos_tupla:
        placeholders = ','.join(['%s'] * len(anos_tupla))
        query = f'''
            SELECT p.idProposicao, p.idApi 
            FROM proposicao p
            JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
            WHERE (t.casa = 'Senado' OR t.casa = 'Congresso') AND p.ano IN ({placeholders})
            ORDER BY p.idProposicao ASC
        '''
        cursor.execute(query, anos_tupla)
    else:
        # Caso fallback se não houver anos para procurar
        return

    proposicoes = cursor.fetchall()

    if not proposicoes: return
    checkpoint_senado_atual = int(obter_ultimo_checkpoint(script_senado, default_value="0"))

    headers = {"Accept": "application/json"}
    start_time = time.time()

    try:
        for id_proposicao, id_api_materia in tqdm(proposicoes, desc="Matérias Senado"):
            if id_proposicao <= checkpoint_senado_atual: continue
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: break

            try:
                url = f"https://legis.senado.leg.br/dadosabertos/materia/{id_api_materia}/votacoes"
                res = http_client.get_safe(url, headers=headers, timeout=15)
                
                if res.status_code != 200:
                    if db.in_transaction: db.commit()
                    db.start_transaction()
                    salvar_checkpoint_transacao(script_senado, id_proposicao)
                    db.commit()
                    continue
                
                dados = res.json()
                votacoes_json = dados.get("VotacaoMateria", {}).get("Materia", {}).get("Votacoes", {}).get("Votacao", [])
                if isinstance(votacoes_json, dict): votacoes_json = [votacoes_json]
                
                if db.in_transaction: db.commit()
                db.start_transaction()
                    
                for v in votacoes_json:
                    id_sessao = v.get("CodigoSessao", "")
                    id_votacao_sessao = v.get("CodigoSessaoVotacao", "")
                    id_api_votacao = f"SEN_{id_api_materia}_{id_sessao}_{id_votacao_sessao}"

                    resumo = v.get("DescricaoVotacao", "")
                    resultado = v.get("Resultado", "")
                    data_hora_str = v.get("DataSessao", "") + " 00:00:00" if v.get("DataSessao") else None
                    
                    votos = v.get("Votos", {}).get("VotoParlamentar", [])
                    tipo = "NOMINAL" if votos else "SIMBOLICA"

                    cursor.execute('''
                        INSERT INTO votacao
                        (idApi, casa, idProposicao, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
                        VALUES (%s, 'Senado', %s, NULL, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            resumoMateria = VALUES(resumoMateria),
                            resultadoFinal = VALUES(resultadoFinal),
                            tipoVotacao = VALUES(tipoVotacao)
                    ''', (id_api_votacao, id_proposicao, data_hora_str, resumo, resultado, tipo))
                
                salvar_checkpoint_transacao(script_senado, id_proposicao)
                db.commit()
                time.sleep(0.3)
            except Exception:
                if db.in_transaction: db.rollback()
                continue

    except KeyboardInterrupt:
        if db.in_transaction: db.rollback()
        cursor.close()
        db.close()
        sys.exit(0)

if __name__ == "__main__":
    try:
        importar_votacoes_camara()
        importar_votacoes_senado()
    except KeyboardInterrupt:
        if db.in_transaction: db.rollback()
    finally:
        cursor.close()
        db.close()

