import os
import sys
import time
from datetime import datetime
from bs4 import BeautifulSoup
from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.checkpoint_manager import CheckpointManager
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger
from utils.paralelo import buscar_lote

logger = get_logger("ETL_Evento_Presenca_Camara")

SITE = "https://www.camara.leg.br/deputados"


def mapear_status(status_texto):
    texto = status_texto.lower().strip()
    if texto == 'presença': return 'PRESENTE'
    if texto == 'ausência' or texto == 'ausência não justificada': return 'AUSENTE'
    if not texto or texto == '-': return 'NAO REGISTRADO'
    return 'JUSTIFICADA'


def formatar_data(data_str):
    try:
        return datetime.strptime(data_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d 00:00:00")
    except Exception:
        return None


def limpar_string_para_id(texto):
    return "".join(c if c.isalnum() else "_" for c in texto).strip("_").upper()


def unificar_plenario_sintetico(cursor, conexao, id_plenario_real):
    """Antes o script inventava o Plenário da Câmara como idApi '114', enquanto
    tramitação e votação usam o id real 180 — duas linhas para o mesmo órgão.
    Migra o que foi gravado com 114 para o 180 e remove o 114. Idempotente."""
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '114' AND casa = 'Camara'")
    antigo = cursor.fetchone()
    if not antigo:
        return
    id_antigo = antigo['idOrgao']
    cursor.execute("UPDATE evento SET idOrgao = %s WHERE idOrgao = %s", (id_plenario_real, id_antigo))
    migrados = cursor.rowcount
    cursor.execute("UPDATE votacao SET idOrgao = %s WHERE idOrgao = %s", (id_plenario_real, id_antigo))
    cursor.execute("UPDATE tramitacao SET idOrgao = %s WHERE idOrgao = %s", (id_plenario_real, id_antigo))
    cursor.execute("DELETE FROM orgao WHERE idOrgao = %s", (id_antigo,))
    conexao.commit()
    logger.info(f"Plenário sintético '114' unificado no órgão real 180 ({migrados} eventos migrados).")


def buscar_pagina(tarefa):
    id_api, ano, tipo = tarefa
    return http_client.get_safe(f"{SITE}/{id_api}/presenca-{tipo}/{ano}")


# ---------------------------------------------------------
# 2. LÓGICA DE EXTRAÇÃO UNIFICADA (SCRAPING)
# ---------------------------------------------------------
def processar_eventos_presencas_camara():
    conexao, cursor = get_connection(dictionary=True)
    chk_manager = CheckpointManager(conexao)

    nome_script = "evento_presenca_camara_v2"
    execucao = ExecucaoEtl(conexao, nome_script)

    # Plenário da Câmara = órgão real 180, carregado pelo catálogo de
    # camara/orgao_camara.py (que roda antes no pipeline). Sem tipoOrgao o
    # backend exclui o órgão das taxas de presença — por isso a exigência.
    cursor.execute("SELECT idOrgao, tipoOrgao FROM orgao WHERE idApi = '180' AND casa = 'Camara'")
    linha = cursor.fetchone()
    if not linha or not linha['tipoOrgao']:
        raise RuntimeError(
            "Órgão 180 (Plenário da Câmara) ausente ou sem tipoOrgao — rode camara/orgao_camara.py antes "
            "(ele carrega o catálogo completo de órgãos)."
        )
    id_plenario_camara = linha['idOrgao']
    unificar_plenario_sintetico(cursor, conexao, id_plenario_camara)

    # 111 é o id REAL do Congresso Nacional na API da Câmara (sigla CN). É
    # mantido com casa='Congresso' deliberadamente: sessões conjuntas não são
    # eventos da Câmara nem do Senado.
    cursor.execute("INSERT IGNORE INTO orgao (idApi, sigla, nome, tipoOrgao, casa) VALUES ('111', 'CN', 'Congresso Nacional', 'Plenário', 'Congresso')")
    cursor.execute("SELECT idOrgao FROM orgao WHERE idApi = '111' AND casa = 'Congresso'")
    id_plenario_congresso = cursor.fetchone()['idOrgao']

    # Com o catálogo completo de órgãos, o casamento de comissões por nome fica
    # muito mais rico e menos eventos de comissão ficam sem idOrgao.
    cursor.execute("SELECT idOrgao, nome FROM orgao WHERE casa = 'Camara'")
    map_orgaos = {row['nome'].lower().strip(): row['idOrgao'] for row in cursor.fetchall() if row['nome']}
    conexao.commit()

    cursor.execute("SELECT idParlamentar, idApi, nomeUrna FROM parlamentar WHERE cargo = 'Deputado(a)' ORDER BY idParlamentar ASC")
    deputados = cursor.fetchall()
    total_deps = len(deputados)
    ultimo_processado = int(chk_manager.obter(nome_script, "0", reiniciar_se_concluido=True))
    sucesso_total = True

    ano_inicio = int(os.getenv("ANO_INICIO_ETL", "2023"))
    ano_atual = datetime.now().year
    anos_mandato = list(range(ano_inicio, ano_atual + 1))

    sql_evento = """
        INSERT INTO evento (idApi, casa, idOrgao, dataHoraInicio, descricaoTipo)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE descricaoTipo=VALUES(descricaoTipo), casa=VALUES(casa), idOrgao=VALUES(idOrgao)
    """
    sql_presenca = """
        INSERT INTO presenca (idParlamentar, idEvento, statusPresenca)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE statusPresenca=VALUES(statusPresenca)
    """

    logger.info("=== INICIANDO ETL UNIFICADO DE EVENTOS E PRESENÇAS (V6) ===")

    for i, dep in enumerate(deputados, 1):
        id_parlamentar = dep['idParlamentar']
        id_api = str(dep['idApi'])
        nome_urna = dep['nomeUrna']

        if id_parlamentar <= ultimo_processado:
            continue

        sucesso_deputado = True
        logger.info(f"[{i}/{total_deps}] A processar: {nome_urna}...")

        # Todas as páginas do deputado (plenário + comissões × anos) em paralelo;
        # parsing e gravação sequenciais.
        tarefas = [(id_api, ano, tipo) for ano in anos_mandato for tipo in ('plenario', 'comissoes')]
        respostas = dict(zip(tarefas, buscar_lote(tarefas, buscar_pagina)))

        eventos = {}      # idApi do evento -> (casa, idOrgao, dataHoraInicio, descricaoTipo)
        presencas = []    # (idApi do evento, statusPresenca)

        for ano in anos_mandato:
            # ── Plenário / Congresso ──
            resp_plen = respostas[(id_api, ano, 'plenario')]
            if isinstance(resp_plen, Exception):
                logger.error(f"Erro ao baixar presença em plenário {ano}: {resp_plen}")
                sucesso_deputado = False
            elif resp_plen.status_code == 200:
                soup = BeautifulSoup(resp_plen.text, 'html.parser')
                for linha in soup.find_all('tr', class_='info-data__child'):
                    celulas = linha.find_all('td')
                    if len(celulas) < 2:
                        continue
                    try:
                        sessao_texto = celulas[0].text.strip()
                        status_enum = mapear_status(celulas[1].text.strip())
                        if status_enum == 'NAO REGISTRADO':
                            continue

                        if "CONJUNTA" in sessao_texto.upper():
                            orgao_evento, casa_evento, desc_tipo = id_plenario_congresso, "Congresso", "Sessão Conjunta"
                        else:
                            orgao_evento, casa_evento, desc_tipo = id_plenario_camara, "Camara", "Sessão Deliberativa"

                        data_str = sessao_texto.split('-')[-1].strip() if '-' in sessao_texto else f"01/01/{ano}"
                        id_evento_api = f"PLEN_{limpar_string_para_id(sessao_texto)}"
                        eventos[id_evento_api] = (casa_evento, orgao_evento, formatar_data(data_str), desc_tipo)
                        presencas.append((id_evento_api, status_enum))
                    except Exception as e:
                        logger.error(f"Erro de parsing (Plenário {ano}): {e}")
                        sucesso_deputado = False

            # ── Comissões ──
            resp_com = respostas[(id_api, ano, 'comissoes')]
            if isinstance(resp_com, Exception):
                logger.error(f"Erro ao baixar presença em comissões {ano}: {resp_com}")
                sucesso_deputado = False
            elif resp_com.status_code == 200:
                soup = BeautifulSoup(resp_com.text, 'html.parser')
                for linha in soup.find_all('tr'):
                    celulas = linha.find_all('td')
                    if len(celulas) < 4:
                        continue
                    links_eventos = celulas[2].find_all('a', href=True)
                    if not links_eventos or 'evento-legislativo' not in links_eventos[0].get('href', ''):
                        continue

                    data_str = celulas[0].text.strip()
                    data_formatada = formatar_data(data_str)
                    orgao_textos = [t.strip() for t in celulas[1].strings if t.strip()]
                    status_textos = [t.strip() for t in celulas[3].strings if t.strip()]

                    for idx, tag_a in enumerate(links_eventos):
                        try:
                            id_evento_api = tag_a.get('href', '').split('/')[-1].split('?')[0]
                            if not id_evento_api.isdigit():
                                continue
                            status_texto = status_textos[idx] if idx < len(status_textos) else status_textos[-1]
                            status_enum = mapear_status(status_texto)
                            if status_enum == 'NAO REGISTRADO':
                                continue

                            id_orgao_interno = None
                            if idx < len(orgao_textos):
                                nome_limpo = orgao_textos[idx].split('-', 1)[-1].strip().lower()
                                id_orgao_interno = map_orgaos.get(nome_limpo)

                            eventos[id_evento_api] = ('Camara', id_orgao_interno, data_formatada, tag_a.text.strip())
                            presencas.append((id_evento_api, status_enum))
                        except Exception as e:
                            logger.error(f"Erro de parsing (Comissão {data_str}): {e}")
                            sucesso_deputado = False

        # ── Gravação em lote: eventos, depois presenças (precisam do idEvento) ──
        try:
            garantir_conexao(conexao)
            if eventos:
                cursor.executemany(sql_evento, [(k, *v) for k, v in eventos.items()])

                ids_evento = {}
                chaves = list(eventos.keys())
                for j in range(0, len(chaves), 500):
                    fatia = chaves[j:j + 500]
                    marcadores = ",".join(["%s"] * len(fatia))
                    cursor.execute(f"SELECT idApi, idEvento FROM evento WHERE idApi IN ({marcadores})", tuple(fatia))
                    ids_evento.update({row['idApi']: row['idEvento'] for row in cursor.fetchall()})

                linhas_presenca = [
                    (id_parlamentar, ids_evento[id_evento_api], status)
                    for id_evento_api, status in presencas if id_evento_api in ids_evento
                ]
                if linhas_presenca:
                    cursor.executemany(sql_presenca, linhas_presenca)
            conexao.commit()
            logger.info(f"   └─ {len(eventos)} eventos / {len(presencas)} presenças gravados.")
        except Exception as e:
            conexao.rollback()
            logger.error(f"Erro ao gravar eventos/presenças do deputado {id_api}: {e}")
            sucesso_deputado = False

        if not sucesso_deputado:
            sucesso_total = False
        execucao.incrementar(processados=1, registros=len(presencas), erros=0 if sucesso_deputado else 1)

        if sucesso_total:
            chk_manager.salvar(nome_script, str(id_parlamentar))
        time.sleep(0.2)

    if sucesso_total:
        chk_manager.concluir(nome_script)
        execucao.finalizar("SUCESSO")
        logger.info("=== ETL de Eventos e Presenças da Câmara FINALIZADO com sucesso ===")
    else:
        execucao.finalizar("FALHA")
        logger.warning("ETL terminou com falhas; checkpoint preservado para retomada. Execute novamente.")

    cursor.close()
    conexao.close()
    return sucesso_total


if __name__ == "__main__":
    if not processar_eventos_presencas_camara():
        sys.exit(1)
