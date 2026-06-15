import os
import sys
import time
import logging
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import mysql.connector

from utils.http_client import http_client

# ==========================================
# CONFIGURAÇÃO DE LOGS
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("VotacaoETL")

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
    logger.info("Conexão com o banco de dados estabelecida com sucesso.")
except mysql.connector.Error as e:
    logger.error(f"Erro ao conectar ao banco: {e}")
    sys.exit(1)

script_camara = "popular/votacao.py#camara_logs"
script_senado = "popular/votacao.py#senado_logs_enums_rigidos"

def obter_ultimo_checkpoint(nome_script, default_value="2025_5_1"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    if resultado:
        logger.debug(f"Checkpoint recuperado para {nome_script}: {resultado[0]}")
        return resultado[0]
    return default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = '''
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    '''
    cursor.execute(query, (nome_script, str(valor_parametro)))
    logger.debug(f"Checkpoint salvo para {nome_script}: {valor_parametro}")

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

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

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
    logger.info(f"Novo órgão criado: {sigla} (Casa: {casa}, ID API: {id_api_orgao})")
    return id_novo

def importar_votacoes_camara():
    logger.info("="*50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTAÇÕES DA CÂMARA")
    logger.info("="*50)
    
    cursor.execute("SELECT idApi, idProposicao FROM proposicao WHERE idApi IS NOT NULL")
    map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}
    logger.info(f"Carregadas {len(map_proposicoes)} proposições para mapeamento (Câmara).")
    
    cronograma_camara = gerar_cronograma_dinamico()

    ano_inicio_str = os.getenv("ANO_INICIO_ETL", "2025")
    mes_inicio_str = os.getenv("MES_INICIO_ETL", "5")
    checkpoint_atual = obter_ultimo_checkpoint(script_camara, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1")
    ano_chk, mes_chk, pagina_chk = map(int, checkpoint_atual.split('_'))

    url = "https://dadosabertos.camara.leg.br/api/v2/votacoes"
    start_time = time.time()
    total_inserido = 0

    for bloco in cronograma_camara:
        ano = bloco["ano"]
        for num_mes, inicio, fim in bloco["meses"]:
            if ano < ano_chk or (ano == ano_chk and num_mes < mes_chk): 
                logger.debug(f"Pulando mês {num_mes:02d}/{ano} (já processado).")
                continue

            logger.info(f"Processando Câmara: Período {num_mes:02d}/{ano} ({inicio} a {fim})")
            pagina = pagina_chk if (ano == ano_chk and num_mes == mes_chk) else 1

            while True:
                if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: 
                    logger.warning("Tempo limite atingido para a Câmara.")
                    return
                
                params = {"dataInicio": inicio, "dataFim": fim, "itens": 100, "pagina": pagina}
                logger.info(f"  -> Buscando página {pagina}...")

                try:
                    res = http_client.get_safe(url, params=params, timeout=60)
                    if res.status_code != 200: 
                        logger.error(f"Erro na API da Câmara (Status {res.status_code})")
                        break
                    
                    dados = res.json().get("dados", [])
                    if not dados: 
                        logger.info(f"  -> Fim das votações para {num_mes:02d}/{ano}.")
                        break

                    if db.in_transaction: db.commit()
                    db.start_transaction()

                    inseridos_pagina = 0
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
                            else: resultado = "Não Informado"
                            
                            resumo = v_detalhe.get("descricao") or ""

                            resumo_lower = resumo.lower()
                            is_nominal = False
                            if "absten" in resumo_lower or "sim:" in resumo_lower or "não:" in resumo_lower or "nao:" in resumo_lower:
                                is_nominal = True
                                
                            tipo = "NOMINAL" if is_nominal else "SIMBOLICA"

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
                            
                            inseridos_pagina += 1

                        except Exception as e:
                            logger.error(f"Erro ao processar detalhes da votação {id_api}: {e}")
                            continue

                    salvar_checkpoint_transacao(script_camara, f"{ano}_{num_mes}_{pagina}")
                    db.commit()
                    total_inserido += inseridos_pagina
                    logger.info(f"  -> Página {pagina} processada: {inseridos_pagina} votações salvas.")

                    if len(dados) < 100: break
                    pagina += 1
                    time.sleep(0.2)

                except Exception as e:
                    logger.error(f"Erro no loop de paginação: {e}")
                    if db.in_transaction: db.rollback()
                    break

            if db.in_transaction: db.commit()
            proximo_mes = num_mes + 1 if num_mes < 12 else 1
            proximo_ano = ano if num_mes < 12 else ano + 1
            db.start_transaction()
            salvar_checkpoint_transacao(script_camara, f"{proximo_ano}_{proximo_mes}_1")
            db.commit()

    logger.info(f"Total de votações processadas na Câmara: {total_inserido}")

def importar_votacoes_e_votos_senado():
    logger.info("="*50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTAÇÕES E VOTOS DO SENADO")
    logger.info("="*50)
    
    cursor.execute("SELECT idProposicao, idApi FROM proposicao WHERE idApi IS NOT NULL AND idTipoProposicao IN (SELECT idTipoProposicao FROM tipoProposicao WHERE casa = 'Senado' OR casa = 'Congresso')")
    map_proposicoes_senado = {str(row[1]): row[0] for row in cursor.fetchall()}
    logger.info(f"Carregadas {len(map_proposicoes_senado)} proposições para mapeamento (Senado).")
    
    cronograma = gerar_cronograma_dinamico()
    
    ano_inicio_str = os.getenv("ANO_INICIO_ETL", "2025")
    mes_inicio_str = os.getenv("MES_INICIO_ETL", "5")
    checkpoint_atual = obter_ultimo_checkpoint(script_senado, default_value=f"{ano_inicio_str}_{mes_inicio_str}")
    
    partes_chk = checkpoint_atual.split('_')
    ano_chk = int(partes_chk[0])
    mes_chk = int(partes_chk[1]) if len(partes_chk) > 1 else 1

    headers = {"Accept": "application/json"}
    start_time = time.time()
    url = "https://legis.senado.leg.br/dadosabertos/votacao"
    
    total_votacoes = 0
    total_votos = 0

    for bloco in cronograma:
        ano = bloco["ano"]
        for num_mes, inicio, fim in bloco["meses"]:
            if ano < ano_chk or (ano == ano_chk and num_mes < mes_chk): continue
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: 
                logger.warning("Tempo limite atingido para o Senado.")
                return
            
            logger.info(f"Processando Senado: Período {num_mes:02d}/{ano} ({inicio} a {fim})")
            params = {"dataInicio": inicio, "dataFim": fim}

            try:
                res = http_client.get_safe(url, params=params, headers=headers, timeout=60)
                if res.status_code != 200:
                    logger.warning(f"  -> Falha na API do Senado (Status {res.status_code})")
                    continue
                
                dados = res.json()
                
                if isinstance(dados, list):
                    votacoes_json = dados
                else:
                    votacoes_json = dados.get("ListaVotacoes", {}).get("Votacoes", {}).get("Votacao", [])
                    if isinstance(votacoes_json, dict):
                        votacoes_json = [votacoes_json]
                        
                if not votacoes_json:
                    logger.info("  -> Nenhuma votação encontrada no período.")
                    if db.in_transaction: db.commit()
                    db.start_transaction()
                    salvar_checkpoint_transacao(script_senado, f"{ano}_{num_mes}")
                    db.commit()
                    continue

                logger.info(f"  -> {len(votacoes_json)} votações encontradas no payload.")

                if db.in_transaction: db.commit()
                db.start_transaction()

                inseridos_votacao_mes = 0
                inseridos_votos_mes = 0

                for v in votacoes_json:
                    id_api_materia = str(v.get("codigoMateria", ""))
                    id_proposicao = map_proposicoes_senado.get(id_api_materia)
                    
                    id_sessao = v.get("codigoSessao", "")
                    id_votacao_sessao = v.get("codigoSessaoVotacao", "")
                    id_api_votacao = f"SEN_{id_api_materia}_{id_sessao}_{id_votacao_sessao}"
                    
                    resumo = v.get("descricaoVotacao", "")
                    
                    resultado_raw = str(v.get("resultadoVotacao", ""))
                    if resultado_raw == "A": resultado = "Aprovado"
                    elif resultado_raw == "R": resultado = "Rejeitado"
                    else: resultado = resultado_raw
                    
                    data_sessao = v.get("dataSessao", "")
                    data_hora_str = f"{data_sessao} 00:00:00" if data_sessao else None
                    
                    id_orgao = None
                    informe = v.get("informeLegislativo", {})
                    if informe:
                        cod_col = informe.get("codigoColegiado")
                        sigla_col = informe.get("siglaColegiado")
                        if cod_col:
                            id_orgao = garantizar_orgao(cod_col, sigla_col, "Senado")
                    
                    secreta_flag = str(v.get("votacaoSecreta", "N")).upper()
                    votos_api = v.get("votos", [])
                    
                    if secreta_flag == "S":
                        tipo = "SECRETA"
                    else:
                        tipo = "NOMINAL" if votos_api else "SIMBOLICA"

                    cursor.execute('''
                        INSERT INTO votacao (idApi, casa, idProposicao, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
                        VALUES (%s, 'Senado', %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                            idProposicao = VALUES(idProposicao),
                            idOrgao = VALUES(idOrgao),
                            resumoMateria = VALUES(resumoMateria), 
                            resultadoFinal = VALUES(resultadoFinal), 
                            tipoVotacao = VALUES(tipoVotacao)
                    ''', (id_api_votacao, id_proposicao, id_orgao, data_hora_str, resumo, resultado, tipo))
                    
                    inseridos_votacao_mes += 1

                    cursor.execute("SELECT idVotacao FROM votacao WHERE idApi = %s", (id_api_votacao,))
                    row_votacao = cursor.fetchone()
                    if not row_votacao: continue
                    id_votacao_interno = row_votacao[0]

                    if isinstance(votos_api, dict): votos_api = [votos_api]

                    batch_votos = []
                    for voto_senador in votos_api:
                        id_sen_api = str(voto_senador.get("codigoParlamentar", ""))
                        if id_sen_api in map_parlamentares:
                            voto_sigla = str(voto_senador.get("siglaVotoParlamentar", "")).strip().upper()
                            
                            if voto_sigla in ["SIM", "S"]: 
                                voto_enum = "SIM"
                            elif voto_sigla in ["NÃO", "NAO", "N"]: 
                                voto_enum = "NAO"
                            elif voto_sigla in ["ABSTENÇÃO", "ABSTENCAO", "ABS"]: 
                                voto_enum = "ABSTENCAO"
                            else:
                                voto_enum = "AUSENTE"
                                
                            id_api_voto = f"{id_api_votacao}_{id_sen_api}"
                            batch_votos.append((map_parlamentares[id_sen_api], id_votacao_interno, id_api_voto, voto_enum))

                    if batch_votos:
                        cursor.executemany('''
                            INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                            VALUES (%s, %s, %s, %s)
                        ''', batch_votos)
                        inseridos_votos_mes += len(batch_votos)
                    
                salvar_checkpoint_transacao(script_senado, f"{ano}_{num_mes}")
                db.commit()
                
                total_votacoes += inseridos_votacao_mes
                total_votos += inseridos_votos_mes
                
                logger.info(f"  -> Concluído: {inseridos_votacao_mes} votações e {inseridos_votos_mes} votos salvos.")
                time.sleep(0.3)
                
            except Exception as e:
                logger.error(f"Erro no Senado ({num_mes}/{ano}): {e}")
                if db.in_transaction: db.rollback()
                continue

    logger.info(f"Total Senado: {total_votacoes} votações e {total_votos} votos.")

if __name__ == "__main__":
    try:
        logger.info("Iniciando Pipeline de Votações...")
        importar_votacoes_camara()
        importar_votacoes_e_votos_senado()
        logger.info("Pipeline de Votações concluído com sucesso!")
    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário (Ctrl+C).")
        if db.in_transaction: db.rollback()
    except Exception as e:
        logger.critical(f"Erro crítico não tratado: {e}")
        if db.in_transaction: db.rollback()
    finally:
        cursor.close()
        db.close()
        logger.info("Conexão com o banco encerrada.")
