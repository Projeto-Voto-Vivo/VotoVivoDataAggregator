import sys
import time
from urllib.parse import urlparse
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger

logger = get_logger("ETL_Camara")

BASE_URL = 'https://dadosabertos.camara.leg.br/api/v2'

# ---------------------------------------------------------
# 3. FUNÇÕES AUXILIARES
# ---------------------------------------------------------
def identificar_plataforma(url):
    dominio = urlparse(url).netloc.lower()
    if 'twitter' in dominio or 'x.com' in dominio: return 'Twitter'
    if 'facebook' in dominio: return 'Facebook'
    if 'instagram' in dominio: return 'Instagram'
    if 'youtube' in dominio: return 'YouTube'
    if 'tiktok' in dominio: return 'TikTok'
    if 'linkedin' in dominio: return 'LinkedIn'
    return 'Website'

def condicao_mandato_camara(ultimo_status):
    situacao = ultimo_status.get('situacao', '')
    cond_eleitoral = ultimo_status.get('condicaoEleitoral', '')

    if situacao in ('Afastado', 'Fim de Mandato'):
        return "Afastado"
    if 'Suplente' in cond_eleitoral:
        return "Suplente"
    return "Titular"

def processar_gabinete(gabinete_dados):
    if not gabinete_dados:
        return None, None
        
    endereco = []
    if gabinete_dados.get('predio'): endereco.append(f"Prédio {gabinete_dados['predio']}")
    if gabinete_dados.get('andar'): endereco.append(f"Andar {gabinete_dados['andar']}")
    if gabinete_dados.get('sala'): endereco.append(f"Sala {gabinete_dados['sala']}")
    
    str_endereco = ", ".join(endereco) if endereco else None
    telefone = gabinete_dados.get('telefone')
    
    return str_endereco, telefone

# ---------------------------------------------------------
# 4. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def buscar_deputados():
    deputados = []
    pagina = 1
    
    logger.info("Iniciando busca paginada da lista de deputados...")
    while True:
        url = f"{BASE_URL}/deputados?pagina={pagina}&itens=100&ordem=ASC&ordenarPor=nome"
        
        # Utilizando o http_client mantido
        response = http_client.get_safe(url, headers={'accept': 'application/json'})
        
        if response.status_code != 200:
            logger.error(f"Erro na API da Câmara ao buscar página {pagina}: HTTP {response.status_code}")
            break
            
        dados = response.json().get('dados', [])
        if not dados:
            break
            
        deputados.extend(dados)
        logger.debug(f"Página {pagina} extraída ({len(dados)} deputados).")
        pagina += 1
        
    logger.info(f"Busca finalizada. Total de {len(deputados)} deputados mapeados no plenário.")
    return deputados

def buscar_detalhes_deputado(id_api):
    url = f"{BASE_URL}/deputados/{id_api}"
    response = http_client.get_safe(url, headers={'accept': 'application/json'})
    
    if response.status_code == 200:
        return response.json().get('dados', {})
    else:
        logger.warning(f"Falha ao buscar detalhes do deputado {id_api} (HTTP {response.status_code})")
        return None

def processar_camara():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "parlamentar_camara_v2"

    # A lista da API vem ordenada por nome, então o índice é um cursor estável
    # o suficiente para retomar uma carga interrompida no mesmo dia.
    progresso = chk_manager.obter(nome_script, "INDEX_0", reiniciar_se_concluido=True)
    indice_inicial = int(progresso.rsplit("_", 1)[-1]) if progresso.startswith("INDEX_") else 0

    deputados_basico = buscar_deputados()
    total = len(deputados_basico)
    
    sql_parlamentar = """
        INSERT INTO parlamentar
        (idApi, cargo, nomeCivil, nomeUrna, partidoAtual, uf, fotoUrl, dataNascimento, email, telefone, enderecoGabinete, condicao_mandato)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        cargo=VALUES(cargo), nomeCivil=VALUES(nomeCivil), nomeUrna=VALUES(nomeUrna),
        partidoAtual=VALUES(partidoAtual), uf=VALUES(uf), fotoUrl=VALUES(fotoUrl),
        dataNascimento=VALUES(dataNascimento), email=VALUES(email),
        telefone=VALUES(telefone), enderecoGabinete=VALUES(enderecoGabinete),
        condicao_mandato=VALUES(condicao_mandato)
    """

    sql_get_id = "SELECT idParlamentar FROM parlamentar WHERE idApi = %s AND cargo = 'Deputado(a)'"
    sql_delete_redes = "DELETE FROM redeSocial WHERE idParlamentar = %s"
    sql_insert_rede = "INSERT INTO redeSocial (idParlamentar, plataforma, url) VALUES (%s, %s, %s)"

    sucesso_total = True

    for i, dep in enumerate(deputados_basico, 1):
        if i <= indice_inicial:
            continue

        id_api = str(dep['id'])
        logger.info(f"[{i}/{total}] Processando detalhes: {dep['nome']} ({id_api})")
        
        detalhes = buscar_detalhes_deputado(id_api)
        if not detalhes:
            time.sleep(1)
            sucesso_total = False
            continue
            
        ultimo_status = detalhes.get('ultimoStatus', {})
        gabinete = ultimo_status.get('gabinete', {})
        endereco_gab, telefone_gab = processar_gabinete(gabinete)
        condicao_mandato = condicao_mandato_camara(ultimo_status)

        email = gabinete.get('email') or dep.get('email')
        data_nasc = detalhes.get('dataNascimento')

        try:
            # 1. Inserir Parlamentar
            valores = (
                id_api, 'Deputado(a)', detalhes.get('nomeCivil'), dep.get('nome'),
                dep.get('siglaPartido'), dep.get('siglaUf'), dep.get('urlFoto'),
                data_nasc if data_nasc else None, email, telefone_gab, endereco_gab,
                condicao_mandato
            )
            cursor.execute(sql_parlamentar, valores)
            
            # 2. Resgatar ID
            cursor.execute(sql_get_id, (id_api,))
            id_parlamentar = cursor.fetchone()[0]
            
            # 3. Atualizar Redes Sociais
            redes = detalhes.get('redeSocial', [])
            if redes:
                cursor.execute(sql_delete_redes, (id_parlamentar,))
                for url in redes:
                    plataforma = identificar_plataforma(url)
                    cursor.execute(sql_insert_rede, (id_parlamentar, plataforma, url))
            
            conexao.commit()
            
            # Atualiza checkpoint a cada 50 deputados, mas só enquanto nenhum
            # falhou — a retomada recomeça no primeiro que deu erro.
            if i % 50 == 0 and sucesso_total:
                chk_manager.salvar(nome_script, f"INDEX_{i}")
                
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao salvar dados do deputado {id_api} no banco: {e}")
            sucesso_total = False

        time.sleep(0.1) # Respeitar rate limit

    if sucesso_total:
        chk_manager.concluir(nome_script)
        logger.info("Sincronização da Câmara finalizada com SUCESSO!")
    else:
        logger.warning("Sincronização finalizada com erros; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    logger.info("=== INICIANDO SCRIPT DE CARGA: CÂMARA DOS DEPUTADOS ===")
    if not processar_camara():
        sys.exit(1)
