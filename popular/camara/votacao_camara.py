import os
import time
from datetime import date, datetime, timedelta

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.orgao_cache import OrgaoCache
from utils.paralelo import buscar_lote

logger = get_logger("VotacaoETL")

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
logger.info("Conexão com o banco de dados estabelecida com sucesso.")

chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Camara", logger=logger)

script_camara = "popular/votacao.py#camara_logs"
fila_erros = EtlErro(db, script_camara)
execucao = ExecucaoEtl(db, script_camara)

def obter_ultimo_dia_mes(ano, mes):
    if mes == 12: return 31
    return (date(ano, mes + 1, 1) - timedelta(days=1)).day

def gerar_cronograma_dinamico():
    ano_inicio = int(os.getenv("ANO_INICIO_ETL", 2023))
    mes_inicio = int(os.getenv("MES_INICIO_ETL", 1))
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

def importar_votacoes_camara():
    logger.info("="*50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTAÇÕES DA CÂMARA")
    logger.info("="*50)

    cursor.execute("""
        SELECT p.idApi, p.idProposicao FROM proposicao p
        WHERE p.casa = 'Camara' AND p.idApi IS NOT NULL
    """)
    map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}
    logger.info(f"Carregadas {len(map_proposicoes)} proposições para mapeamento (Câmara).")

    cronograma_camara = gerar_cronograma_dinamico()

    ano_inicio_str = os.getenv("ANO_INICIO_ETL", "2023")
    mes_inicio_str = os.getenv("MES_INICIO_ETL", "1")
    checkpoint_atual = chk_manager.obter(script_camara, default_value=f"{ano_inicio_str}_{mes_inicio_str}_1")
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
                    execucao.finalizar("INTERROMPIDO", "tempo limite atingido")
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

                    # A conexão pode ter caído durante a espera das chamadas HTTP
                    garantir_conexao(db)
                    if db.in_transaction: db.commit()
                    db.start_transaction()

                    inseridos_pagina = 0
                    # Detalhes de toda a página em paralelo; gravação sequencial.
                    ids_pagina = [v.get("id") for v in dados if v.get("id")]
                    detalhes = buscar_lote(ids_pagina, lambda id_v: http_client.get_safe(f"{url}/{id_v}", timeout=60))
                    for id_api, res_detalhe in zip(ids_pagina, detalhes):
                        try:
                            if isinstance(res_detalhe, Exception): raise res_detalhe
                            if res_detalhe.status_code != 200: continue
                            v_detalhe = res_detalhe.json().get("dados", {})

                            id_proposicao = None
                            # "or []" porque a API pode devolver a chave com valor null
                            elementos_afetados = (v_detalhe.get("proposicoesAfetadas") or []) + (v_detalhe.get("objetosPossiveis") or [])
                            for p in elementos_afetados:
                                if p.get("id"):
                                    id_api_verificar = str(p.get("id"))
                                    if id_api_verificar in map_proposicoes:
                                        id_proposicao = map_proposicoes[id_api_verificar]
                                        break

                            uri_orgao = v_detalhe.get("uriOrgao", "")
                            id_orgao_api = uri_orgao.split("/")[-1] if uri_orgao else None
                            id_orgao = orgaos.garantir(id_orgao_api, v_detalhe.get("siglaOrgao"))

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
                            fila_erros.registrar(str(id_api), e)
                            execucao.incrementar(erros=1)
                            continue

                    chk_manager.salvar(script_camara, f"{ano}_{num_mes}_{pagina}")
                    db.commit()
                    total_inserido += inseridos_pagina
                    execucao.incrementar(processados=inseridos_pagina, registros=inseridos_pagina)
                    logger.info(f"  -> Página {pagina} processada: {inseridos_pagina} votações salvas.")

                    if len(dados) < 100: break
                    pagina += 1

                except Exception as e:
                    logger.error(f"Erro no loop de paginação: {e}")
                    if db.in_transaction: db.rollback()
                    break

            if db.in_transaction: db.commit()
            proximo_mes = num_mes + 1 if num_mes < 12 else 1
            proximo_ano = ano if num_mes < 12 else ano + 1
            db.start_transaction()
            chk_manager.salvar(script_camara, f"{proximo_ano}_{proximo_mes}_1")
            db.commit()

    logger.info(f"Total de votações processadas na Câmara: {total_inserido}")

if __name__ == "__main__":
    try:
        logger.info("Iniciando Pipeline de Votações da Câmara...")
        importar_votacoes_camara()
        execucao.finalizar("SUCESSO")
        logger.info("Pipeline de Votações da Câmara concluído com sucesso!")
    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário (Ctrl+C).")
        if db.in_transaction: db.rollback()
        execucao.finalizar("INTERROMPIDO")
    except Exception as e:
        logger.critical(f"Erro crítico não tratado: {e}")
        if db.in_transaction: db.rollback()
        execucao.finalizar("FALHA", str(e))
    finally:
        orgaos.fechar()
        cursor.close()
        db.close()
        logger.info("Conexão com o banco encerrada.")
