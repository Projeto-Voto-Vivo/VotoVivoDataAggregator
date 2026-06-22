import os
import time
from datetime import datetime
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger

logger = get_logger("ETL_Proposicao_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# ---------------------------------------------------------
# 2. FUNÇÕES DE PRÉ-SINCRONIZAÇÃO (REFERÊNCIAS)
# ---------------------------------------------------------
def sincronizar_tipos_proposicao(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/siglaTipo"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_tipos = {}
    
    if resp.status_code == 200:
        tipos = resp.json().get('dados', [])
        sql = """
            INSERT INTO tipoProposicao (sigla, nome, casa) VALUES (%s, %s, 'Camara')
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """
        for t in tipos:
            sigla = t.get('sigla')
            nome = t.get('nome') or sigla
            if not sigla: continue
            
            cursor.execute(sql, (sigla, nome))
            cursor.execute("SELECT idTipoProposicao FROM tipoProposicao WHERE sigla = %s AND casa = 'Camara'", (sigla,))
            mapa_tipos[sigla] = cursor.fetchone()[0]
            
        conexao.commit()
    cursor.close()
    return mapa_tipos

def sincronizar_temas(conexao):
    cursor = conexao.cursor()
    url = f"{BASE_URL}/referencias/proposicoes/codTema"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    mapa_temas = {}
    
    if resp.status_code == 200:
        temas = resp.json().get('dados', [])
        sql = """
            INSERT INTO tema (codigoExterno, casa, descricao) VALUES (%s, 'Camara', %s)
            ON DUPLICATE KEY UPDATE descricao = VALUES(descricao)
        """
        for t in temas:
            cod_externo = int(t.get('cod'))
            nome = t.get('nome')
            
            cursor.execute(sql, (cod_externo, nome))
            cursor.execute("SELECT idTema FROM tema WHERE codigoExterno = %s AND casa = 'Camara'", (cod_externo,))
            mapa_temas[cod_externo] = cursor.fetchone()[0]
            
        conexao.commit()
    cursor.close()
    return mapa_temas

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def obter_deputados_ativos(cursor):
    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Deputado(a)'")
    return cursor.fetchall()

def buscar_detalhes_proposicao(id_prop_api):
    url = f"{BASE_URL}/proposicoes/{id_prop_api}"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    return resp.json().get('dados', {}) if resp.status_code == 200 else None

def buscar_temas_da_proposicao(id_prop_api):
    url = f"{BASE_URL}/proposicoes/{id_prop_api}/temas"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    return resp.json().get('dados', []) if resp.status_code == 200 else []

def buscar_autores_camara(id_prop_api):
    url = f"{BASE_URL}/proposicoes/{id_prop_api}/autores"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    if resp.status_code != 200:
        return []

    autores_ids = []
    for autor in resp.json().get('dados', []):
        uri = autor.get('uri', '')
        if 'deputados/' in uri:
            autores_ids.append(uri.split('/')[-1])
    return autores_ids

def processar_proposicoes_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "proposicao_camara_v2"
    
    map_tipos = sincronizar_tipos_proposicao(conexao)
    map_temas = sincronizar_temas(conexao)
    
    deputados = obter_deputados_ativos(cursor)
    total_deputados = len(deputados)
    ultimo_deputado_processado = chk_manager.obter(nome_script, "0")
    mapa_parlamentares_camara = {str(id_api): id_parl for id_parl, id_api, _ in deputados}
    
    sql_proposicao = """
        INSERT INTO proposicao (idApi, idTipoProposicao, numero, ano, ementa, statusAtual, dataApresentacao)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        idTipoProposicao=VALUES(idTipoProposicao), numero=VALUES(numero), 
        ano=VALUES(ano), ementa=VALUES(ementa), statusAtual=VALUES(statusAtual),
        dataApresentacao=VALUES(dataApresentacao)
    """
    sql_get_prop_id = "SELECT idProposicao FROM proposicao WHERE idApi = %s"
    sql_autoria = "INSERT IGNORE INTO autoriaProposicao (idParlamentar, idProposicao) VALUES (%s, %s)"
    sql_vincular_tema = "INSERT IGNORE INTO temaProposicao (idProposicao, idTema) VALUES (%s, %s)"

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year
    anos_mandato = list(range(ano_inicio, ano_atual + 1))

    proposicoes_processadas = set()

    for i, (_, id_api_deputado, nome_urna) in enumerate(deputados, 1):
        if str(id_api_deputado) <= ultimo_deputado_processado and ultimo_deputado_processado != "0":
            continue
            
        logger.info(f"[{i}/{total_deputados}] Buscando proposições de: {nome_urna}")
        sucesso_deputado = True
        
        for ano in anos_mandato:
            pagina = 1
            
            while True:
                url_lista = f"{BASE_URL}/proposicoes?ano={ano}&idDeputadoAutor={id_api_deputado}&pagina={pagina}&itens=100&ordem=ASC"

                resp_lista = http_client.get_safe(url_lista, headers={'accept': 'application/json'})
                if resp_lista.status_code != 200:
                    logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                    sucesso_deputado = False
                    break

                lista_props = resp_lista.json().get('dados', [])
                if not lista_props: 
                    break
                    
                for prop_basico in lista_props:
                    id_prop_api = str(prop_basico['id'])

                    if id_prop_api in proposicoes_processadas:
                        continue

                    detalhes = buscar_detalhes_proposicao(id_prop_api)
                    if not detalhes:
                        time.sleep(0.5)
                        continue
                    
                    sigla_tipo = detalhes.get('siglaTipo')
                    id_tipo_interno = map_tipos.get(sigla_tipo)
                    status_node = detalhes.get('statusProposicao') or {}
                    status_atual = status_node.get('descricaoSituacao')
                    
                    data_apresentacao_raw = detalhes.get('dataApresentacao')
                    data_apresentacao = data_apresentacao_raw.replace('T', ' ') if data_apresentacao_raw else None
                    
                    try:
                        cursor.execute(sql_proposicao, (
                            id_prop_api, id_tipo_interno, detalhes.get('numero'),
                            detalhes.get('ano'), detalhes.get('ementa'), status_atual,
                            data_apresentacao
                        ))
                        
                        cursor.execute(sql_get_prop_id, (id_prop_api,))
                        id_proposicao_interno = cursor.fetchone()[0]

                        for autor_id_api in buscar_autores_camara(id_prop_api):
                            id_autor_parlamentar = mapa_parlamentares_camara.get(autor_id_api)
                            if id_autor_parlamentar:
                                cursor.execute(sql_autoria, (id_autor_parlamentar, id_proposicao_interno))

                        temas_da_prop = buscar_temas_da_proposicao(id_prop_api)
                        for t in temas_da_prop:
                            cod_tema_api = t.get('codTema')
                            id_tema_interno = map_temas.get(cod_tema_api)
                            
                            if id_tema_interno:
                                cursor.execute(sql_vincular_tema, (id_proposicao_interno, id_tema_interno))

                        conexao.commit()
                        proposicoes_processadas.add(id_prop_api)
                    except Exception as e:
                        conexao.rollback()
                        logger.error(f"Erro ao salvar proposição {id_prop_api}: {e}")
                        sucesso_deputado = False

                    time.sleep(0.3)
                    
                pagina += 1
                
        if sucesso_deputado:
            chk_manager.salvar(nome_script, str(id_api_deputado))
            time.sleep(1)

    chk_manager.salvar(nome_script, "CONCLUIDO_" + datetime.now().strftime('%Y-%m-%d'))
    cursor.close()
    conexao.close()

if __name__ == "__main__":
    processar_proposicoes_camara()
