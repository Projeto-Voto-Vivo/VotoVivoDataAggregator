import os
import sys
import time
from datetime import datetime
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger

logger = get_logger("ETL_Proposicao_Senado")

BASE_URL_SENADO = 'https://legis.senado.leg.br/dadosabertos'

# ---------------------------------------------------------
# 2. FUNÇÕES DE PRÉ-SINCRONIZAÇÃO (REFERÊNCIAS)
# ---------------------------------------------------------
def sincronizar_tipos_proposicao(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL_SENADO}/processo/siglas"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_tipos = {}
    
    if resp.status_code == 200:
        dados = resp.json()
        tipos = dados if isinstance(dados, list) else dados.get('Siglas', [])
        
        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Senado')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('descricao') or sigla
            if not sigla: continue
            
            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Senado'", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]
            
        conexao.commit()
        logger.info(f"{len(mapa_tipos)} Tipos de Proposição do Senado sincronizados.")
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL_SENADO}/processo/assuntos"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_temas = {}
    
    if resp.status_code == 200:
        dados = resp.json()
        temas = dados if isinstance(dados, list) else dados.get('Assuntos', [])
        
        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao) VALUES (%s, 'Senado', %s)
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = t.get('id')
            nome = t.get('assuntoEspecifico') or t.get('assuntoGeral')
            if not cod_externo or not nome: continue
            
            cursor.execute(sql, (int(cod_externo), nome))
            cursor.execute("SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Senado'", (int(cod_externo),))
            mapa_temas[int(cod_externo)] = cursor.fetchone()[0]
            
        conexao.commit()
        logger.info(f"{len(mapa_temas)} Temas (Assuntos) do Senado sincronizados.")
    cursor.close()
    return mapa_temas

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def obter_senadores_ativos(cursor):
    cursor.execute("""
        SELECT idParlamentar, idApi, nomeUrna FROM parlamentar
        WHERE cargo = 'Senador(a)' ORDER BY idParlamentar ASC
    """)
    return cursor.fetchall()

def buscar_detalhes_processo_senado(id_processo_api):
    url = f"{BASE_URL_SENADO}/processo/{id_processo_api}?v=1"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    return resp.json() if resp.status_code == 200 else None

def extrair_autores_senado(detalhes):
    codigos = []
    for chave in ("autoriaIniciativa", "autoria"):
        for autor in detalhes.get(chave, []) or []:
            codigo = autor.get("codigoParlamentar")
            if codigo:
                codigos.append(str(codigo))

    for autor in (detalhes.get("documento") or {}).get("autoria", []) or []:
        codigo = autor.get("codigoParlamentar")
        if codigo:
            codigos.append(str(codigo))

    return codigos

def processar_proposicoes_senado():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_senado_v2"

    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)

    senadores = obter_senadores_ativos(cursor)
    total_senadores = len(senadores)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    mapa_parlamentares_senado = {str(id_api): id_parl for id_parl, id_api, _ in senadores}

    sql_proposicao = """
        INSERT INTO proposicao (idApi, casa, idTipoProposicao, numero, ano, ementa, statusAtual, dataApresentacao)
        VALUES (%s, 'Senado', %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        idTipoProposicao=VALUES(idTipoProposicao), numero=VALUES(numero),
        ano=VALUES(ano), ementa=VALUES(ementa), statusAtual=VALUES(statusAtual),
        dataApresentacao=VALUES(dataApresentacao)
    """
    sql_get_prop_id = "SELECT idProposicao FROM proposicao WHERE idApi = %s AND casa = 'Senado'"
    sql_autoria = "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)"
    sql_vincular_tema = "INSERT IGNORE INTO temaProposicao (idProposicao, idTema) VALUES (%s, %s)"

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year
    anos_mandato = list(range(ano_inicio, ano_atual + 1))

    proposicoes_processadas = set()
    sucesso_total = True

    for i, (id_parlamentar, id_api_senador, nome_urna) in enumerate(senadores, 1):
        if id_parlamentar <= ultimo_processado:
            continue

        logger.info(f"[{i}/{total_senadores}] Buscando proposições de: {nome_urna}")
        sucesso_senador = True
        
        for ano in anos_mandato:
            url_lista = f"{BASE_URL_SENADO}/processo?ano={ano}&codigoParlamentarAutor={id_api_senador}&v=1"

            resp_lista = http_client.get_safe(url_lista, headers={'accept': 'application/json'})
            if resp_lista.status_code != 200:
                logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                sucesso_senador = False
                continue

            dados_lista = resp_lista.json()
            lista_props = dados_lista if isinstance(dados_lista, list) else dados_lista.get('Processos', [])
            
            if not lista_props: 
                continue
                
            for prop_basico in lista_props:
                id_processo_lista = str(prop_basico.get('id'))
                status_atual_lista = prop_basico.get('situacaoAtual')

                if id_processo_lista in proposicoes_processadas:
                    continue

                detalhes = buscar_detalhes_processo_senado(id_processo_lista)
                if not detalhes:
                    time.sleep(0.5)
                    continue
                
                codigo_materia = str(detalhes.get('codigoMateria') or id_processo_lista)
                sigla_tipo = detalhes.get('sigla')
                id_tipo_interno = map_tipos.get(sigla_tipo)
                
                conteudo = detalhes.get('conteudo', {})
                documento = detalhes.get('documento', {})
                
                ementa_final = conteudo.get('ementa') or prop_basico.get('ementa')
                data_apresentacao_raw = documento.get('dataApresentacao') or prop_basico.get('dataApresentacao')
                data_apresentacao = data_apresentacao_raw.replace('T', ' ') if data_apresentacao_raw else None
                
                try:
                    cursor.execute(sql_proposicao, (
                        codigo_materia, id_tipo_interno, detalhes.get('numero'),
                        detalhes.get('ano'), ementa_final, status_atual_lista,
                        data_apresentacao
                    ))
                    
                    cursor.execute(sql_get_prop_id, (codigo_materia,))
                    id_proposicao_interno = cursor.fetchone()[0]

                    for codigo_autor in extrair_autores_senado(detalhes):
                        id_autor_parlamentar = mapa_parlamentares_senado.get(codigo_autor)
                        if id_autor_parlamentar:
                            cursor.execute(sql_autoria, (id_autor_parlamentar, id_proposicao_interno))

                    assuntos = detalhes.get('assuntos', []) or prop_basico.get('assuntos', [])
                    for a in assuntos:
                        cod_tema_api = a.get('id') or a.get('codigo')
                        if cod_tema_api:
                            id_tema_interno = map_temas.get(int(cod_tema_api))
                            if id_tema_interno:
                                cursor.execute(sql_vincular_tema, (id_proposicao_interno, id_tema_interno))

                    conexao.commit()
                    proposicoes_processadas.add(id_processo_lista)
                except Exception as e:
                    conexao.rollback()
                    logger.error(f"Erro ao salvar proposição Senado {codigo_materia}: {e}")
                    sucesso_senador = False

                time.sleep(0.3)
                
        if not sucesso_senador:
            sucesso_total = False

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
            time.sleep(1)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        logger.info("Proposições do Senado sincronizadas com SUCESSO.")
    else:
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    if not processar_proposicoes_senado():
        sys.exit(1)
