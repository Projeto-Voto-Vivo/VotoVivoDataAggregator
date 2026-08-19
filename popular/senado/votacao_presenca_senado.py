import sys
import time
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger
from utils.orgao_cache import OrgaoCache

logger = get_logger("ETL_Votacao_Senado")

BASE_URL = 'https://legis.senado.leg.br/dadosabertos'

# ---------------------------------------------------------
# 2. FUNÇÕES AUXILIARES
# ---------------------------------------------------------
def fazer_requisicao_com_retry(url):
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    if resp.status_code == 200: return resp
    return None

def mapear_voto_senado(voto_string):
    """Mapeia os votos considerando textos completos e as siglas de painel do Senado (S, N, P)"""
    voto = str(voto_string).upper().strip() if voto_string else ""
    palavras = voto.split()
    
    if "SIM" in voto or "S" in palavras: return "SIM"
    if "NÃO" in voto or "NAO" in voto or "N" in palavras: return "NAO"
    if "ABSTENÇÃO" in voto or "ABSTENCAO" in voto or "P" in palavras: return "ABSTENCAO"
    if "OBSTRUÇÃO" in voto or "OBSTRUCAO" in voto: return "OBSTRUCAO"
    if "ART. 17" in voto or "PRESIDENTE" in voto: return "NAO REGISTRADO"
    return "NAO REGISTRADO"

def mapear_resultado_senado(resultado_raw):
    resultado_raw = str(resultado_raw or "")
    if resultado_raw == "A": return "Aprovado"
    if resultado_raw == "R": return "Rejeitado"
    return resultado_raw or None

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def processar_votacoes_presencas_senado():
    conexao, cursor = get_connection(dictionary=True)
    chk_manager = CheckpointManager(conexao)
    orgaos = OrgaoCache(conexao, cursor, "Senado", logger=logger)

    nome_script = "votacao_presenca_senado_v2"

    logger.info("A carregar Senadores em atividade para a memória...")
    cursor.execute("SELECT idParlamentar, idApi FROM parlamentar WHERE cargo = 'Senador(a)'")
    map_senadores = {str(row['idApi']): row['idParlamentar'] for row in cursor.fetchall()}
    total_senadores_ativos = len(map_senadores)
    logger.info(f"Total de senadores ativos carregados: {total_senadores_ativos}")

    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    sucesso_total = True

    cursor.execute("""
        SELECT p.idProposicao, p.idApi
        FROM proposicao p
        WHERE p.casa = 'Senado' AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (ultimo_processado,))
    proposicoes = cursor.fetchall()
    total_props = len(proposicoes)

    cursor.execute("INSERT IGNORE INTO orgao (idApi, sigla, nome, tipoOrgao, casa) VALUES ('1001', 'PLEN-SF', 'Plenário do Senado Federal', 'Plenário', 'Senado')")
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '1001' AND casa = 'Senado'")
    id_plenario_senado = cursor.fetchone()['idOrgao']
    conexao.commit()

    logger.info(f"=== INICIANDO ETL DE VOTAÇÕES DO SENADO V4 ({total_props} Proposições) ===")

    for i, prop in enumerate(proposicoes, 1):
        id_proposicao_interno = prop['idProposicao']
        id_materia_api = str(prop['idApi'])
        sucesso_prop = True

        logger.info(f"[{i}/{total_props}] A procurar votações nominais para a Matéria ID {id_materia_api}...")
        resp = fazer_requisicao_com_retry(f"{BASE_URL}/votacao?codigoMateria={id_materia_api}&v=1")

        if not resp:
            logger.info("   └─ Nenhuma votação nominal encontrada para esta matéria.")
            if sucesso_total:
                chk_manager.salvar(nome_script, str(id_proposicao_interno))
            continue

        lista_votacoes = resp.json()
        if not isinstance(lista_votacoes, list): lista_votacoes = [lista_votacoes]
            
        for votacao in lista_votacoes:
            id_sessao = str(votacao.get('codigoSessaoVotacao', ''))
            if not id_sessao: continue
            
            data_votacao = votacao.get('dataSessao')
            data_hora_formatada = data_votacao + " 00:00:00" if data_votacao else None
            resumo = votacao.get('descricaoVotacao')

            votos_api = votacao.get('votos', [])
            if isinstance(votos_api, dict):
                votos_api = votos_api.get('votoParlamentar') or votos_api.get('voto') or []
            if not isinstance(votos_api, list):
                votos_api = [votos_api]

            resultado = mapear_resultado_senado(votacao.get('resultadoVotacao'))

            secreta_flag = str(votacao.get('votacaoSecreta', 'N')).upper()
            tipo = "SECRETA" if secreta_flag == "S" else ("NOMINAL" if votos_api else "SIMBOLICA")

            id_orgao_votacao = id_plenario_senado
            informe = votacao.get('informeLegislativo') or {}
            cod_colegiado = informe.get('codigoColegiado')
            if cod_colegiado:
                id_orgao_votacao = orgaos.garantir(cod_colegiado, informe.get('siglaColegiado')) or id_plenario_senado

            logger.info(f"   └─ Encontrada Sessão {id_sessao} ({data_votacao}). A processar dados de plenário e votos...")

            try:
                # 1. Evento
                id_evento_api = f"SESSAO_{id_sessao}"
                cursor.execute("""
                    INSERT IGNORE INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo)
                    VALUES (%s, 'Senado', %s, %s, 'Sessão Deliberativa')
                """, (id_evento_api, id_orgao_votacao, data_hora_formatada))

                cursor.execute("SELECT idEvento FROM evento WHERE idApi = %s", (id_evento_api,))
                id_evento_interno = cursor.fetchone()['idEvento']

                # 2. Votação
                cursor.execute("""
                    INSERT INTO votacao (idApi, casa, idProposicao, idEvento, idOrgao, dataHora, resumoMateria, resultadoFinal, tipoVotacao)
                    VALUES (%s, 'Senado', %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        resumoMateria=VALUES(resumoMateria),
                        resultadoFinal=VALUES(resultadoFinal),
                        tipoVotacao=VALUES(tipoVotacao)
                """, (id_sessao, id_proposicao_interno, id_evento_interno, id_orgao_votacao, data_hora_formatada, resumo, resultado, tipo))

                cursor.execute("SELECT idVotacao FROM votacao WHERE idApi = %s AND casa = 'Senado'", (id_sessao,))
                id_votacao_interno = cursor.fetchone()['idVotacao']

                # 3. Votos
                senadores_votaram = set()
                contagem_votos = {'SIM': 0, 'NAO': 0, 'ABSTENCAO': 0, 'OBSTRUCAO': 0, 'NAO REGISTRADO': 0}

                sql_voto = """
                    INSERT INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE votoRegistrado=VALUES(votoRegistrado)
                """
                sql_presenca = "INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca) VALUES (%s, %s, 'PRESENTE')"

                for v in votos_api:
                    if not isinstance(v, dict): continue
                    
                    id_senador_api = str(v.get('codigoParlamentar', ''))
                    if not id_senador_api: continue
                    
                    voto_bruto = ""
                    for key, val in v.items():
                        if isinstance(val, str):
                            chave_limpa = key.lower()
                            if 'nome' not in chave_limpa and 'partido' not in chave_limpa and 'uf' not in chave_limpa:
                                voto_bruto += f" {val} "
                                
                    voto_enum = mapear_voto_senado(voto_bruto)
                    contagem_votos[voto_enum] += 1
                    
                    if id_senador_api in map_senadores:
                        id_parlamentar_interno = map_senadores[id_senador_api]
                        id_voto_api = f"{id_sessao}_{id_senador_api}"
                        
                        cursor.execute(sql_voto, (id_parlamentar_interno, id_votacao_interno, id_voto_api, voto_enum))
                        cursor.execute(sql_presenca, (id_parlamentar_interno, id_evento_interno))
                        senadores_votaram.add(id_senador_api)

                # 4. Faltas (Auditoria)
                sql_falta = "INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca) VALUES (%s, %s, 'AUSENTE')"
                ausentes = 0
                for id_api, id_interno in map_senadores.items():
                    if id_api not in senadores_votaram:
                        cursor.execute(sql_falta, (id_interno, id_evento_interno))
                        ausentes += 1

                conexao.commit()
                logger.info(f"      └─ Sucesso: {len(senadores_votaram)} Votantes | {ausentes} Ausentes.")
                logger.info(f"      └─ Detalhe Votos Extraídos: {contagem_votos['SIM']} SIM | {contagem_votos['NAO']} NÃO | {contagem_votos['ABSTENCAO']} ABS.")
                
            except Exception as e:
                conexao.rollback()
                logger.error(f"Erro ao processar votação {id_sessao}: {e}")
                sucesso_prop = False

        if not sucesso_prop:
            sucesso_total = False

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_proposicao_interno))
        time.sleep(0.3)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        logger.info("=== ETL DE VOTAÇÕES E PRESENÇAS DO SENADO FINALIZADO COM SUCESSO ===")
    else:
        logger.warning("ETL terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    if not processar_votacoes_presencas_senado():
        sys.exit(1)
