import io
import os
import sys
import unicodedata
import zipfile
import requests
import pandas as pd

from utils.db import get_connection

try:
    from utils.log import get_logger
    logger = get_logger("candidatura_tse")
except ModuleNotFoundError:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logger = logging.getLogger("candidatura_tse")

URL_TSE_CANDIDATOS_2026 = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2026.zip"


def normalizar_texto(texto: str) -> str:
    """Remove acentos, espacos extras e converte para maiusculas."""
    if not texto or pd.isna(texto):
        return ""
    texto = str(texto).strip().upper()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def carregar_mapa_parlamentares(cursor) -> dict:
    """
    Carrega parlamentares da base em memoria para matching por (NOME, UF).
    """
    cursor.execute("SELECT idParlamentar, nomeCivil, nomeUrna, uf FROM parlamentar")
    rows = cursor.fetchall()

    mapa = {}
    for r in rows:
        id_parlamentar = r[0] if isinstance(r, (tuple, list)) else r["idParlamentar"]
        nome_civil = r[1] if isinstance(r, (tuple, list)) else r["nomeCivil"]
        nome_urna = r[2] if isinstance(r, (tuple, list)) else r["nomeUrna"]
        uf = r[3] if isinstance(r, (tuple, list)) else r["uf"]

        uf_norm = normalizar_texto(uf)
        if nome_civil:
            mapa[(normalizar_texto(nome_civil), uf_norm)] = id_parlamentar
        if nome_urna:
            mapa[(normalizar_texto(nome_urna), uf_norm)] = id_parlamentar

    return mapa


def processar_e_inserir_dataframe(df: pd.DataFrame, mapa_parlamentares: dict, cursor, conn, ano_eleicao: int):
    """Realiza o tratamento dos dados e a insercao em lote na tabela candidaturaTse."""
    sql = """
        INSERT INTO candidaturaTse (
            idParlamentar,
            sqCandidato,
            anoEleicao,
            descricaoEleicao,
            uf,
            cargo,
            numeroCandidato,
            nomeUrna,
            nomeCivil,
            siglaPartido,
            situacaoCandidatura,
            resultadoEleicao
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            idParlamentar = VALUES(idParlamentar),
            descricaoEleicao = VALUES(descricaoEleicao),
            cargo = VALUES(cargo),
            numeroCandidato = VALUES(numeroCandidato),
            nomeUrna = VALUES(nomeUrna),
            nomeCivil = VALUES(nomeCivil),
            siglaPartido = VALUES(siglaPartido),
            situacaoCandidatura = VALUES(situacaoCandidatura),
            resultadoEleicao = VALUES(resultadoEleicao);
    """

    df = df.fillna("")
    registros = []
    vinculos_encontrados = 0

    for _, row in df.iterrows():
        sq_candidato = str(row.get("SQ_CANDIDATO", "")).strip()
        if not sq_candidato:
            continue

        ds_eleicao = str(row.get("DS_ELEICAO", "")).strip()
        uf = str(row.get("SG_UF", "")).strip().upper()
        cargo = str(row.get("DS_CARGO", "")).strip().upper()
        nr_candidato = str(row.get("NR_CANDIDATO", "")).strip()
        nm_urna = str(row.get("NM_URNA_CANDIDATO", "")).strip()
        nm_civil = str(row.get("NM_CANDIDATO", "")).strip()
        sigla_partido = str(row.get("SG_PARTIDO", "")).strip().upper()
        situacao = str(row.get("DS_SITUACAO_CANDIDATURA", "")).strip()
        resultado = str(row.get("DS_SIT_TOT_TURNO", "")).strip()

        nm_civil_norm = normalizar_texto(nm_civil)
        nm_urna_norm = normalizar_texto(nm_urna)
        uf_norm = normalizar_texto(uf)

        id_parlamentar = mapa_parlamentares.get((nm_civil_norm, uf_norm))
        if not id_parlamentar:
            id_parlamentar = mapa_parlamentares.get((nm_urna_norm, uf_norm))

        if id_parlamentar:
            vinculos_encontrados += 1

        registros.append((
            id_parlamentar,
            sq_candidato,
            ano_eleicao,
            ds_eleicao,
            uf,
            cargo,
            nr_candidato,
            nm_urna,
            nm_civil,
            sigla_partido,
            situacao,
            resultado
        ))

    if registros:
        cursor.executemany(sql, registros)
        conn.commit()

    return len(registros), vinculos_encontrados


def popular_candidaturas_tse(url_download: str = URL_TSE_CANDIDATOS_2026, ano_eleicao: int = 2026):
    """Baixa o zip do TSE e popula a tabela candidaturaTse."""
    logger.info(f"Conectando ao repositorio do TSE: {url_download}")

    # Desempacota conn e cursor tratando retorno como tupla ou objeto individual
    db_res = get_connection()
    if isinstance(db_res, tuple):
        conn, cursor = db_res[0], db_res[1]
    else:
        conn = db_res
        cursor = conn.cursor()

    try:
        logger.info("Carregando lista de parlamentares para matching...")
        mapa_parlamentares = carregar_mapa_parlamentares(cursor)

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url_download, headers=headers, stream=True, timeout=120)
        response.raise_for_status()

        logger.info("Download concluido. Processando arquivos CSV em memoria...")

        colunas_necessarias = [
            "SQ_CANDIDATO",
            "DS_ELEICAO",
            "SG_UF",
            "DS_CARGO",
            "NR_CANDIDATO",
            "NM_URNA_CANDIDATO",
            "NM_CANDIDATO",
            "SG_PARTIDO",
            "DS_SITUACAO_CANDIDATURA",
            "DS_SIT_TOT_TURNO"
        ]

        total_inseridos = 0
        total_vinculados = 0

        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            arquivos_csv = [f for f in z.namelist() if f.endswith(".csv")]
            arquivos_brasil = [f for f in arquivos_csv if "BRASIL" in f.upper()]
            arquivos_alvo = arquivos_brasil if arquivos_brasil else arquivos_csv

            for filename in arquivos_alvo:
                logger.info(f"Lendo arquivo: {filename}")
                with z.open(filename) as f:
                    df = pd.read_csv(
                        f,
                        sep=";",
                        encoding="latin1",
                        usecols=lambda c: c in colunas_necessarias,
                        dtype=str
                    )

                    inseridos, vinculados = processar_e_inserir_dataframe(
                        df, mapa_parlamentares, cursor, conn, ano_eleicao
                    )
                    total_inseridos += inseridos
                    total_vinculados += vinculados
                    logger.info(f"Parcial ({filename}): {inseridos} registros processados | {vinculados} vinculados.")

        logger.info(f"Carga finalizada! Total inserido: {total_inseridos} | Vinculados a parlamentares: {total_vinculados}")

    except requests.exceptions.RequestException as req_err:
        logger.error(f"Erro ao baixar dados do TSE: {req_err}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Erro inesperado no pipeline do TSE: {e}")
        raise e
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    popular_candidaturas_tse()