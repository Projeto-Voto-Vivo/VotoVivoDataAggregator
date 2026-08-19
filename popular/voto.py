import time
import sys
import os

from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.etl_erro import EtlErro
from utils.logging_config import get_logger

logger = get_logger("VotoETL")

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
logger.info("Conexão estabelecida para Votos da Câmara.")

chk_manager = CheckpointManager(db)

script_camara = "popular/voto.py#camara_logs_ausencia_justificada"

cursor.execute("SELECT idApi, idParlamentar FROM parlamentar WHERE cargo = 'Deputado(a)'")
map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

def importar_votos_camara():
    logger.info("="*50)
    logger.info("INICIANDO IMPORTAÇÃO DE VOTOS DA CÂMARA")
    logger.info("="*50)
    
    cursor.execute('''
        SELECT idApi, idVotacao 
        FROM votacao 
        WHERE casa = 'Camara'
        ORDER BY idVotacao ASC
    ''')
    votacoes = cursor.fetchall()
    checkpoint_atual = int(chk_manager.obter(script_camara, default_value="0"))
    total_votos = 0
    start_time = time.time()

    # Reprocesso: votações que falharam em execuções anteriores voltam à fila,
    # mesmo estando atrás do checkpoint.
    fila_erros = EtlErro(db, script_camara)
    pendentes = set(fila_erros.listar_pendentes())
    if pendentes:
        logger.info(f"{len(pendentes)} votações com erro pendente serão reprocessadas.")

    try:
        for index, (id_api_votacao, id_votacao) in enumerate(votacoes):
            if id_votacao <= checkpoint_atual and str(id_api_votacao) not in pendentes:
                continue
            if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos: 
                logger.warning("Tempo limite atingido para Votos da Câmara.")
                break
            
            if index % 50 == 0:
                logger.info(f"Processando votação {index}/{len(votacoes)} (ID: {id_votacao})...")

            try:
                res = http_client.get_safe(f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{id_api_votacao}/votos", timeout=30)
                if res.status_code != 200: continue
                
                dados = res.json().get("dados", [])
                
                if db.in_transaction: db.commit()
                db.start_transaction()

                if not dados:
                    if id_votacao > checkpoint_atual:
                        chk_manager.salvar(script_camara, id_votacao)
                    db.commit()
                    if str(id_api_votacao) in pendentes:
                        fila_erros.resolver(str(id_api_votacao))
                    continue

                batch = []
                for v in dados:
                    id_dep_api = str(v.get("deputado_", {}).get("id"))
                    if id_dep_api in map_parlamentares:
                        voto_txt = v.get("tipoVoto", "").strip().lower()
                        
                        if voto_txt == "sim":
                            voto_enum = "SIM"
                        elif voto_txt in ["não", "nao"]:
                            voto_enum = "NAO"
                        elif "absten" in voto_txt:
                            voto_enum = "ABSTENCAO"
                        elif "obstru" in voto_txt:
                            voto_enum = "OBSTRUCAO"
                        elif any(palavra in voto_txt for palavra in ["justificad", "licença", "missão", "afastament"]):
                            # Cobre casos raros mas possíveis de "Ausência Justificada", "Licença Médica", etc. na Camara
                            voto_enum = "AUSENCIA JUSTIFICADA"
                        elif voto_txt == "ausente" or "ausência" in voto_txt:
                            voto_enum = "AUSENTE"
                        else:
                            # Cobre coisas como "Artigo 17", "Branco", Votações Secretas
                            voto_enum = "NAO REGISTRADO"

                        id_api_voto = f"{id_api_votacao}_{id_dep_api}"
                        batch.append((map_parlamentares[id_dep_api], id_votacao, id_api_voto, voto_enum))

                if batch:
                    cursor.executemany('''
                        INSERT IGNORE INTO voto (idParlamentar, idVotacao, idApi, votoRegistrado)
                        VALUES (%s, %s, %s, %s)
                    ''', batch)
                    total_votos += len(batch)

                # Um item reprocessado da fila de erros não pode regredir o cursor
                if id_votacao > checkpoint_atual:
                    chk_manager.salvar(script_camara, id_votacao)
                db.commit()
                if str(id_api_votacao) in pendentes:
                    fila_erros.resolver(str(id_api_votacao))
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Erro ao buscar votos da votação {id_votacao}: {e}")
                if db.in_transaction: db.rollback()
                fila_erros.registrar(str(id_api_votacao), e)
                continue
    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário.")
        if db.in_transaction: db.rollback()
        sys.exit(0)
    
    logger.info(f"Concluído: {total_votos} votos da Câmara inseridos no total.")

if __name__ == "__main__":
    importar_votos_camara()
    cursor.close()
    db.close()
