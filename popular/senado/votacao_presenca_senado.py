import sys
import time
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.execucao import ExecucaoEtl
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

# Siglas oficiais do painel do Senado. Antes, "P" (presente) era classificado
# como ABSTENCAO e as licenças/missões viravam voto — distorcendo as métricas.
MAPA_VOTO_SENADO = {
    "SIM": "SIM", "S": "SIM",
    "NÃO": "NAO", "NAO": "NAO", "N": "NAO",
    "ABSTENÇÃO": "ABSTENCAO", "ABSTENCAO": "ABSTENCAO",
    "OBSTRUÇÃO": "OBSTRUCAO", "OBSTRUCAO": "OBSTRUCAO",
    "P-NRV": "NAO REGISTRADO",   # presente, não registrou voto
    "P": "NAO REGISTRADO",       # presente
    "VOTOU": "NAO REGISTRADO",   # votação secreta
    "NA": "NAO REGISTRADO",      # não apurado
    "AP": "AUSENCIA JUSTIFICADA",   # atividade parlamentar
    "LS": "AUSENCIA JUSTIFICADA",   # licença saúde
    "LAP": "AUSENCIA JUSTIFICADA",  # licença atividade parlamentar
    "LP": "AUSENCIA JUSTIFICADA",   # licença particular
    "MIS": "AUSENCIA JUSTIFICADA",  # missão
    "REP": "AUSENCIA JUSTIFICADA",  # representação
    "NCOM": "AUSENTE",              # não compareceu
}

def extrair_voto_bruto(v):
    """Extrai o texto do voto preferindo os campos oficiais; só na ausência
    deles recorre à concatenação defensiva dos campos string."""
    for chave in ("siglaVoto", "SiglaVoto", "siglaDescricaoVoto", "descricaoVoto", "DescricaoVoto", "voto", "Voto"):
        val = v.get(chave)
        if isinstance(val, str) and val.strip():
            return val

    partes = []
    for key, val in v.items():
        if isinstance(val, str):
            chave_limpa = key.lower()
            if 'nome' not in chave_limpa and 'partido' not in chave_limpa and 'uf' not in chave_limpa:
                partes.append(val)
    return " ".join(partes)

def mapear_voto_senado(voto_string):
    voto = str(voto_string).upper().strip() if voto_string else ""

    # Sigla exata primeiro (ex.: "P-NRV (Presente - não registrou voto)" -> "P-NRV")
    sigla = voto.split("(")[0].strip()
    if sigla in MAPA_VOTO_SENADO:
        return MAPA_VOTO_SENADO[sigla]

    if "NRV" in voto or "PRESIDENTE" in voto or "ART" in voto or "VOTOU" in voto:
        return "NAO REGISTRADO"
    if "OBSTRU" in voto: return "OBSTRUCAO"
    if "ABSTEN" in voto: return "ABSTENCAO"
    if any(t in voto for t in ("LICEN", "MISS", "ATIVIDADE", "REPRESENTA", "SAUDE", "SAÚDE", "JUSTIFICAD")):
        return "AUSENCIA JUSTIFICADA"
    if "COMPARECEU" in voto or "AUSEN" in voto:
        return "AUSENTE"
    if voto.startswith("SIM"): return "SIM"
    if voto.startswith("NÃO") or voto.startswith("NAO"): return "NAO"
    return "NAO REGISTRADO"

def presenca_do_voto(voto_enum):
    if voto_enum == "AUSENTE":
        return "AUSENTE"
    if voto_enum == "AUSENCIA JUSTIFICADA":
        return "JUSTIFICADA"
    return "PRESENTE"

def buscar_exercicios_senador(id_api):
    """Períodos (DataInicio, DataFim) em que o senador efetivamente exerceu o
    mandato. DataFim ausente significa exercício em curso. Devolve None se a
    API falhar (o chamador decide como tratar)."""
    resp = fazer_requisicao_com_retry(f"{BASE_URL}/senador/{id_api}/mandatos")
    if not resp:
        return None
    try:
        dados = resp.json()
        mandatos = (((dados.get('MandatoParlamentar') or {}).get('Parlamentar') or {}).get('Mandatos') or {}).get('Mandato') or []
        if isinstance(mandatos, dict):
            mandatos = [mandatos]

        periodos = []
        for m in mandatos:
            exercicios = (m.get('Exercicios') or {}).get('Exercicio') or []
            if isinstance(exercicios, dict):
                exercicios = [exercicios]
            for e in exercicios:
                inicio = e.get('DataInicio')
                if inicio:
                    periodos.append((inicio, e.get('DataFim')))
        return periodos
    except Exception:
        return None

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
    fila_erros = EtlErro(conexao, nome_script)
    execucao = ExecucaoEtl(conexao, nome_script)

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

    # Períodos de exercício de cada senador: um senador só pode ser marcado
    # como AUSENTE em sessões dentro do seu exercício — sem isso, quem assumiu
    # em 2025 apareceria "ausente" em todas as sessões de 2023.
    exercicios_senadores = {}
    if proposicoes:
        # Preferência: períodos persistidos por senado/mandato_senado.py; a API
        # só é consultada como fallback quando a tabela ainda está vazia.
        cursor.execute("""
            SELECT p.idApi, me.dataInicio, me.dataFim
            FROM mandatoExercicio me
            JOIN parlamentar p ON p.idParlamentar = me.idParlamentar
            WHERE p.cargo = 'Senador(a)'
        """)
        for row in cursor.fetchall():
            chave = str(row['idApi'])
            fim = str(row['dataFim']) if row['dataFim'] else None
            exercicios_senadores.setdefault(chave, []).append((str(row['dataInicio']), fim))

        if exercicios_senadores:
            logger.info(f"Períodos de exercício carregados do banco para {len(exercicios_senadores)} senadores.")
        else:
            logger.warning("Tabela mandatoExercicio vazia; buscando exercícios na API (rode senado/mandato_senado.py antes para evitar isso).")
            for id_api_sen in map_senadores:
                periodos = buscar_exercicios_senador(id_api_sen)
                if periodos is None:
                    logger.warning(f"Sem dados de exercício para o senador {id_api_sen}; ele não será marcado como ausente.")
                exercicios_senadores[id_api_sen] = periodos
                time.sleep(0.1)

    def em_exercicio(id_api_sen, data_sessao):
        """Sem dados de exercício ou de data, devolve False — nunca marcar
        ausência que não se pode comprovar."""
        periodos = exercicios_senadores.get(id_api_sen)
        if not data_sessao or not periodos:
            return False
        return any(inicio <= data_sessao and (not fim or data_sessao <= fim) for inicio, fim in periodos)

    logger.info(f"=== INICIANDO ETL DE VOTAÇÕES DO SENADO V4 ({total_props} Proposições) ===")

    for i, prop in enumerate(proposicoes, 1):
        id_proposicao_interno = prop['idProposicao']
        id_materia_api = str(prop['idApi'])
        sucesso_prop = True

        logger.info(f"[{i}/{total_props}] A procurar votações nominais para a Matéria ID {id_materia_api}...")
        resp = fazer_requisicao_com_retry(f"{BASE_URL}/votacao?codigoMateria={id_materia_api}&v=1")
        # A conexão pode ter caído durante a espera das chamadas HTTP
        garantir_conexao(conexao)

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
                senadores_no_painel = set()
                contagem_votos = {'SIM': 0, 'NAO': 0, 'ABSTENCAO': 0, 'OBSTRUCAO': 0,
                                  'AUSENCIA JUSTIFICADA': 0, 'AUSENTE': 0, 'NAO REGISTRADO': 0}

                sql_voto = """
                    INSERT INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE votoRegistrado=VALUES(votoRegistrado)
                """
                sql_presenca = "INSERT IGNORE INTO presenca (idParlamentar, idEvento, statusPresenca) VALUES (%s, %s, %s)"

                for v in votos_api:
                    if not isinstance(v, dict): continue

                    id_senador_api = str(v.get('codigoParlamentar', ''))
                    if not id_senador_api: continue

                    voto_enum = mapear_voto_senado(extrair_voto_bruto(v))
                    contagem_votos[voto_enum] += 1

                    if id_senador_api in map_senadores:
                        id_parlamentar_interno = map_senadores[id_senador_api]
                        id_voto_api = f"{id_sessao}_{id_senador_api}"

                        cursor.execute(sql_voto, (id_parlamentar_interno, id_votacao_interno, id_voto_api, voto_enum))
                        # A presença deriva do que o painel registrou: licença/missão
                        # vira JUSTIFICADA, "não compareceu" vira AUSENTE.
                        cursor.execute(sql_presenca, (id_parlamentar_interno, id_evento_interno, presenca_do_voto(voto_enum)))
                        senadores_no_painel.add(id_senador_api)

                # 4. Faltas (Auditoria) — apenas senadores em exercício na data da
                # sessão e que não constam do painel de votação.
                ausentes = 0
                for id_api, id_interno in map_senadores.items():
                    if id_api not in senadores_no_painel and em_exercicio(id_api, data_votacao):
                        cursor.execute(sql_presenca, (id_interno, id_evento_interno, 'AUSENTE'))
                        ausentes += 1

                conexao.commit()
                logger.info(f"      └─ Sucesso: {len(senadores_no_painel)} no painel | {ausentes} Ausentes.")
                logger.info(f"      └─ Detalhe Votos Extraídos: {contagem_votos['SIM']} SIM | {contagem_votos['NAO']} NÃO | {contagem_votos['ABSTENCAO']} ABS.")

            except Exception as e:
                conexao.rollback()
                logger.error(f"Erro ao processar votação {id_sessao}: {e}")
                fila_erros.registrar(f"materia_{id_materia_api}_sessao_{id_sessao}", e)
                sucesso_prop = False

        if not sucesso_prop:
            sucesso_total = False
        execucao.incrementar(processados=1, erros=0 if sucesso_prop else 1)

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_proposicao_interno))
        time.sleep(0.3)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("=== ETL DE VOTAÇÕES E PRESENÇAS DO SENADO FINALIZADO COM SUCESSO ===")
    else:
        execucao.finalizar("FALHA")
        logger.warning("ETL terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    if not processar_votacoes_presencas_senado():
        sys.exit(1)
