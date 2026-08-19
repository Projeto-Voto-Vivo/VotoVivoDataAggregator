import sys
import time
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger

logger = get_logger("ETL_Orgao_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# ---------------------------------------------------------
# 2. FUNÇÕES DE BANCO E CACHE
# ---------------------------------------------------------
def obter_deputados_ativos(cursor):
    cursor.execute("""
        SELECT idParlamentar, idApi, nomeUrna FROM parlamentar
        WHERE cargo = 'Deputado(a)' ORDER BY idParlamentar ASC
    """)
    return cursor.fetchall()

def carregar_cache_orgaos(cursor):
    """Carrega os órgãos já existentes no banco para a memória, evitando chamadas repetidas à API"""
    cursor.execute("SELECT idApi, idOrgao FROM orgao WHERE casa = 'Camara'")
    return {str(row[0]): row[1] for row in cursor.fetchall()}

def buscar_detalhes_orgao(id_orgao_api):
    url = f"{BASE_URL}/orgaos/{id_orgao_api}"
    resp = http_client.get_safe(url, headers={'accept': 'application/json'})
    return resp.json().get('dados', {}) if resp.status_code == 200 else None

# ---------------------------------------------------------
# 3. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def processar_orgaos_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "orgao_camara_v2"

    deputados = obter_deputados_ativos(cursor)
    map_orgaos = carregar_cache_orgaos(cursor)

    total_deputados = len(deputados)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    sucesso_total = True
    
    sql_orgao = """
        INSERT INTO orgao (idApi, sigla, nome, tipoOrgao, casa)
        VALUES (%s, %s, %s, %s, 'Camara')
        ON DUPLICATE KEY UPDATE sigla=VALUES(sigla), nome=VALUES(nome), tipoOrgao=VALUES(tipoOrgao)
    """

    sql_get_orgao_id = "SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = 'Camara'"
    sql_membro = "INSERT IGNORE INTO membroOrgao (idParlamentar, idOrgao, cargo) VALUES (%s, %s, %s)"

    for i, (id_parlamentar, id_api_deputado, nome_urna) in enumerate(deputados, 1):
        if id_parlamentar <= ultimo_processado:
            continue

        logger.info(f"[{i}/{total_deputados}] Buscando órgãos/comissões de: {nome_urna}")
        sucesso_deputado = True
        
        pagina = 1
        while True:
            url_lista = f"{BASE_URL}/deputados/{id_api_deputado}/orgaos?ordem=ASC&ordenarPor=dataInicio&pagina={pagina}&itens=100"

            resp_lista = http_client.get_safe(url_lista, headers={'accept': 'application/json'})
            if resp_lista.status_code != 200:
                logger.error(f"Erro crítico HTTP {resp_lista.status_code} na URL: {url_lista}")
                sucesso_deputado = False
                break

            lista_orgaos = resp_lista.json().get('dados', [])
            if not lista_orgaos: 
                break
                
            for org_basico in lista_orgaos:
                id_orgao_api = str(org_basico.get('idOrgao'))
                cargo_parlamentar = org_basico.get('titulo')
                
                # Ignorar cargos nulos ou inválidos
                if not id_orgao_api or not cargo_parlamentar:
                    continue
                
                # Apenas órgãos ativos no mandato atual (sem data de fim ou data futura)
                data_fim = org_basico.get('dataFim')
                if data_fim and data_fim < "2023-01-01":
                    continue

                try:
                    # Se o órgão não está no banco, buscamos os detalhes na API e inserimos
                    if id_orgao_api not in map_orgaos:
                        detalhes = buscar_detalhes_orgao(id_orgao_api)
                        
                        sigla_final = detalhes.get('sigla') if detalhes else org_basico.get('siglaOrgao')
                        nome_final = detalhes.get('nome') if detalhes else org_basico.get('nomeOrgao')
                        tipo_orgao = detalhes.get('tipoOrgao') if detalhes else 'Comissão'
                        
                        cursor.execute(sql_orgao, (id_orgao_api, sigla_final, nome_final, tipo_orgao))
                        
                        cursor.execute(sql_get_orgao_id, (id_orgao_api,))
                        id_orgao_interno = cursor.fetchone()[0]
                        map_orgaos[id_orgao_api] = id_orgao_interno # Atualiza o cache em memória
                        
                        time.sleep(0.2) # Respiro para a API de detalhes
                        
                    # 2. Inserir a relação de membresia (Parlamentar <-> Órgão)
                    id_orgao_interno = map_orgaos[id_orgao_api]
                    cursor.execute(sql_membro, (id_parlamentar, id_orgao_interno, cargo_parlamentar))
                    
                    conexao.commit()
                except Exception as e:
                    conexao.rollback()
                    logger.error(f"Erro ao salvar órgão {id_orgao_api} para o deputado {id_api_deputado}: {e}")
                    sucesso_deputado = False
                
            pagina += 1
            time.sleep(0.2)
            
        if not sucesso_deputado:
            sucesso_total = False

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
            time.sleep(0.5)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        logger.info("=== Sincronização de Órgãos e Membros da Câmara FINALIZADA ===")
    else:
        logger.warning("Sincronização terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    if not processar_orgaos_camara():
        sys.exit(1)
