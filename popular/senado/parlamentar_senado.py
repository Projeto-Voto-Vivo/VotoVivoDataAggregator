import sys
import time
import xml.etree.ElementTree as ET
from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.logging_config import get_logger

logger = get_logger("ETL_Senado")

BASE_URL_SENADO = 'https://legis.senado.leg.br/dadosabertos'

# ---------------------------------------------------------
# 3. FUNÇÕES AUXILIARES DE BANCO E XML
# ---------------------------------------------------------
def get_xml_text(element, tag_name, default=None):
    """Busca recursivamente uma tag no XML e retorna o seu texto, se existir."""
    if element is None: return default
    node = element.find(f".//{tag_name}")
    return node.text if node is not None and node.text else default

# ---------------------------------------------------------
# 4. LÓGICA DE EXTRAÇÃO E INSERÇÃO
# ---------------------------------------------------------
def buscar_senadores_lista():
    url = f"{BASE_URL_SENADO}/senador/lista/atual?v=4"
    logger.info("Buscando lista de senadores em exercício...")
    
    response = http_client.get_safe(url, headers={'accept': 'application/xml'})
    if response.status_code != 200:
        logger.error(f"Erro ao buscar lista do Senado: HTTP {response.status_code}")
        return []

    try:
        root = ET.fromstring(response.content)
        # O XML agrupa em <Parlamentares><Parlamentar>
        senadores = root.findall('.//Parlamentar')
        logger.info(f"Lista obtida com sucesso. {len(senadores)} senadores encontrados.")
        return senadores
    except ET.ParseError as e:
        logger.error(f"Erro ao fazer o parse do XML da lista: {e}")
        return []

def buscar_afastados_senado():
    url = f"{BASE_URL_SENADO}/senador/lista/afastados"
    response = http_client.get_safe(url, headers={'accept': 'application/xml'})
    if response.status_code != 200:
        logger.warning(f"Falha ao buscar lista de afastados do Senado (HTTP {response.status_code})")
        return set()

    try:
        root = ET.fromstring(response.content)
        return {
            get_xml_text(p, 'CodigoParlamentar')
            for p in root.findall('.//Parlamentar')
            if get_xml_text(p, 'CodigoParlamentar')
        }
    except ET.ParseError as e:
        logger.error(f"Erro ao fazer o parse do XML de afastados: {e}")
        return set()

def condicao_mandato_senado(id_api, parlamentar_xml, afastados_senado):
    if id_api in afastados_senado:
        return "Afastado"
    if 'Suplente' in (get_xml_text(parlamentar_xml, 'DescricaoParticipacao') or ''):
        return "Suplente"
    return "Titular"

def buscar_detalhes_senador(id_api):
    url = f"{BASE_URL_SENADO}/senador/{id_api}?v=6"
    response = http_client.get_safe(url, headers={'accept': 'application/xml'})
    
    if response.status_code == 200:
        try:
            root = ET.fromstring(response.content)
            return root
        except ET.ParseError:
            logger.warning(f"Erro ao fazer parse dos detalhes do senador {id_api}")
            return None
    else:
        logger.warning(f"Falha ao buscar detalhes do senador {id_api} (HTTP {response.status_code})")
        return None

def processar_senado():
    conexao, cursor = get_connection()
    chk_manager = CheckpointManager(conexao)
    nome_script = "parlamentar_senado_v2"

    # A lista da API tem ordem estável, então o índice serve de cursor para
    # retomar uma carga interrompida no mesmo dia.
    progresso = chk_manager.obter(nome_script, "INDEX_0", reiniciar_se_concluido=True)
    indice_inicial = int(progresso.rsplit("_", 1)[-1]) if progresso.startswith("INDEX_") else 0

    senadores_elementos = buscar_senadores_lista()
    total = len(senadores_elementos)
    afastados_senado = buscar_afastados_senado()

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

    sucesso_total = True

    for i, parlamentar_xml in enumerate(senadores_elementos, 1):
        if i <= indice_inicial:
            continue

        # 1. Dados Básicos vindos da Lista Principal
        id_api = get_xml_text(parlamentar_xml, 'CodigoParlamentar')
        nome_urna = get_xml_text(parlamentar_xml, 'NomeParlamentar')
        
        if not id_api: continue
        
        logger.info(f"[{i}/{total}] Processando: {nome_urna} ({id_api})")
        
        # 2. Buscar Detalhes para pegar DataNascimento e Gabinete
        detalhes_xml = buscar_detalhes_senador(id_api)
        if detalhes_xml is None:
            time.sleep(1)
            sucesso_total = False
            continue
            
        # Extrair dados agregados
        nome_civil = get_xml_text(detalhes_xml, 'NomeCompletoParlamentar') or get_xml_text(parlamentar_xml, 'NomeCompletoParlamentar')
        partido = get_xml_text(detalhes_xml, 'SiglaPartidoParlamentar') or get_xml_text(parlamentar_xml, 'SiglaPartidoParlamentar')
        uf = get_xml_text(detalhes_xml, 'UfParlamentar') or get_xml_text(parlamentar_xml, 'UfParlamentar')
        foto_url = get_xml_text(detalhes_xml, 'UrlFotoParlamentar') or get_xml_text(parlamentar_xml, 'UrlFotoParlamentar')
        email = get_xml_text(detalhes_xml, 'EmailParlamentar') or get_xml_text(parlamentar_xml, 'EmailParlamentar')
        
        telefone = get_xml_text(detalhes_xml, 'NumeroTelefone')
        data_nasc = get_xml_text(detalhes_xml, 'DataNascimento')
        endereco_gab = get_xml_text(detalhes_xml, 'EnderecoParlamentar')
        condicao_mandato = condicao_mandato_senado(id_api, parlamentar_xml, afastados_senado)

        try:
            valores = (
                id_api, 'Senador(a)', nome_civil, nome_urna,
                partido, uf, foto_url,
                data_nasc if data_nasc else None,
                email, telefone, endereco_gab, condicao_mandato
            )
            cursor.execute(sql_parlamentar, valores)
            conexao.commit()
            
            # Checkpoint a cada 20 senadores, mas só enquanto nenhum falhou —
            # a retomada recomeça no primeiro que deu erro.
            if i % 20 == 0 and sucesso_total:
                chk_manager.salvar(nome_script, f"INDEX_{i}")
                
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao salvar dados do senador {id_api} no banco: {e}")
            sucesso_total = False

        time.sleep(0.1) # Respeitar limites da API do Senado

    if sucesso_total:
        chk_manager.concluir(nome_script)
        logger.info("Sincronização do Senado finalizada com SUCESSO!")
    else:
        logger.warning("Sincronização finalizada com erros; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total

if __name__ == "__main__":
    logger.info("=== INICIANDO SCRIPT DE CARGA: SENADO FEDERAL ===")
    if not processar_senado():
        sys.exit(1)
