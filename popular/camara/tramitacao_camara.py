import os
import time
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.orgao_cache import OrgaoCache

BASE_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Camara")

script_camara = "popular/tramitacao.py#camara"

cursor.execute("SELECT idTipoTramitacao, idApi FROM tipoTramitacao")
map_tipo = {str(row[1]): row[0] for row in cursor.fetchall()}

def extrair_id(uri):
    try:
        return str(int(uri.split("/")[-1]))
    except:
        return None

def importar_tramitacao_camara():
    checkpoint_atual = int(chk_manager.obter(script_camara, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Camara' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila_proposicoes = cursor.fetchall()

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Tramitações Câmara"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL_CAMARA}/proposicoes/{id_api}/tramitacoes"
            res = http_client.get_safe(url, timeout=30)

            if res.status_code != 200:
                chk_manager.salvar(script_camara, id_interno)
                db.commit()
                continue

            dados = res.json().get("dados", [])
            dados.sort(key=lambda x: x.get("sequencia") or 0)

            for t in dados:
                sequencia = t.get("sequencia")
                if not sequencia:
                    continue

                id_tipo = map_tipo.get(str(t.get("codTipoTramitacao")))
                if id_tipo is None:
                    continue

                id_orgao_api = extrair_id(t.get("uriOrgao"))
                id_orgao = orgaos.garantir(id_orgao_api)
                id_api_tramitacao = f"{id_api}_{sequencia}"

                cursor.execute("""
                    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        idTipoTramitacao = VALUES(idTipoTramitacao),
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        descricaoSituacao = VALUES(descricaoSituacao),
                        despacho = VALUES(despacho)
                """, (id_api_tramitacao, id_interno, id_tipo, id_orgao, t.get("dataHora"), sequencia, t.get("descricaoTramitacao"), t.get("descricaoSituacao"), t.get("despacho")))

            chk_manager.salvar(script_camara, id_interno)
            db.commit()
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_tramitacao_camara()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()
