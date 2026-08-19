import os
import time
from tqdm import tqdm

from utils.http_client import http_client
from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager
from utils.orgao_cache import OrgaoCache

BASE_URL_SENADO = "https://legis.senado.leg.br/dadosabertos"
is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection()
chk_manager = CheckpointManager(db)
orgaos = OrgaoCache(db, cursor, "Senado")

script_senado = "popular/tramitacao.py#senado"

def importar_tramitacao_senado():
    checkpoint_atual = int(chk_manager.obter(script_senado, default_value="0"))
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        WHERE p.casa = 'Senado' AND p.idApi IS NOT NULL AND p.idProposicao > %s
        ORDER BY p.idProposicao ASC
    """, (checkpoint_atual,))
    fila_proposicoes = cursor.fetchall()

    if is_test_mode:
        fila_proposicoes = fila_proposicoes[:5]

    start_time = time.time()
    headers = {"Accept": "application/json"}

    for id_interno, id_api in tqdm(fila_proposicoes, desc="Tramitações Senado"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"{BASE_URL_SENADO}/materia/movimentacoes/{id_api}"
            res = http_client.get_safe(url, headers=headers, timeout=30)

            if res.status_code != 200:
                chk_manager.salvar(script_senado, id_interno)
                db.commit()
                continue

            dados_materia = res.json().get("MovimentacaoMateria", {}).get("Materia", {})
            historico = dados_materia.get("HistoricoMovimentacoes", {}).get("Movimentacao", [])

            if isinstance(historico, dict):
                historico = [historico]

            historico.sort(key=lambda x: int(x.get("CodigoTramitacao") or 0))

            for seq, m in enumerate(historico, start=1):
                cod_tramitacao = m.get("CodigoTramitacao")
                if not cod_tramitacao:
                    continue

                data_hora = m.get("DataTramitacao")
                if data_hora and len(data_hora) == 10:
                    data_hora = f"{data_hora} 00:00:00"

                descr_tramitacao = m.get("DescricaoComissao") or m.get("IdentificacaoOrgao") or "Senado"
                id_orgao = orgaos.garantir(m.get("CodigoComissao") or m.get("CodigoOrgao"))

                id_api_tramitacao = f"SEN_{id_api}_{cod_tramitacao}"

                cursor.execute("""
                    INSERT INTO tramitacao (idApi, idProposicao, idTipoTramitacao, idOrgao, dataHora, sequencia, descricaoTramitacao, descricaoSituacao, despacho)
                    VALUES (%s, %s, NULL, %s, %s, %s, %s, NULL, %s)
                    ON DUPLICATE KEY UPDATE
                        idOrgao = VALUES(idOrgao),
                        dataHora = VALUES(dataHora),
                        descricaoTramitacao = VALUES(descricaoTramitacao),
                        despacho = VALUES(despacho)
                """, (id_api_tramitacao, id_interno, id_orgao, data_hora, seq, descr_tramitacao, m.get("TextoParecer") or m.get("DescricaoUltimaSituacao")))

            chk_manager.salvar(script_senado, id_interno)
            db.commit()
            time.sleep(0.1)

        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        importar_tramitacao_senado()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()
