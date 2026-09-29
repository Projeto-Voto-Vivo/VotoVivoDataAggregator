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
# A situacao da candidatura so vem preenchida no arquivo complementar: no consulta_cand,
# DS_SITUACAO_CANDIDATURA e "#NE" para todos. Usamos DS_SITUACAO_JULGAMENTO (julgamento
# do registro: DEFERIDO, INDEFERIDO, RENUNCIA...), unica coluna preenchida para todos.
URL_TSE_COMPLEMENTAR_2026 = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand_complementar/consulta_cand_complementar_2026.zip"
HEADERS_TSE = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# Foto do candidato no DivulgaCandContas: /img/{ID_ELEICAO_DIVULGA}/{SQ_CANDIDATO}/{SG_UF} (SG_UF = "BR" para presidente)
URL_TSE_FOTO = "https://divulgacandcontas.tse.jus.br/divulga/rest/arquivo/img/{id_eleicao}/{sq_candidato}/{uf}"
# O id da eleicao no DivulgaCandContas NAO e o CD_ELEICAO do CSV (ex.: 6259 aponta para outra eleicao).
# E um id proprio do sistema, unico para a eleicao geral (federal + estadual) do ano.
ID_ELEICAO_DIVULGA = {
    2022: "2040602022",
    2026: "20322002026",
}


def montar_foto_url(ano_eleicao: int, sq_candidato: str, uf: str):
    id_eleicao = ID_ELEICAO_DIVULGA.get(ano_eleicao)
    if not (id_eleicao and sq_candidato and uf):
        return None
    return URL_TSE_FOTO.format(id_eleicao=id_eleicao, sq_candidato=sq_candidato, uf=uf)


# Marcadores do TSE para campo sem valor: #NULO (nulo) e #NE (nao existe/nao informado)
MARCADORES_TSE_VAZIO = {"#NULO", "#NULO#", "#NE", "#NE#"}


def valor_tse(texto):
    """Valor do CSV do TSE, ou None quando vazio ou marcador (#NE, #NULO)."""
    texto = str(texto or "").strip()
    return None if not texto or texto.upper() in MARCADORES_TSE_VAZIO else texto


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


def baixar_zip(url: str) -> io.BytesIO:
    response = requests.get(url, headers=HEADERS_TSE, stream=True, timeout=120)
    response.raise_for_status()
    return io.BytesIO(response.content)


def arquivos_alvo_zip(z: zipfile.ZipFile) -> list:
    """CSV consolidado (BRASIL) quando existir; senao, todos os CSVs por UF."""
    arquivos_csv = [f for f in z.namelist() if f.endswith(".csv")]
    arquivos_brasil = [f for f in arquivos_csv if "BRASIL" in f.upper()]
    return arquivos_brasil if arquivos_brasil else arquivos_csv


def carregar_situacoes(origem_zip) -> dict:
    """SQ_CANDIDATO -> situacao do julgamento da candidatura (arquivo complementar)."""
    situacoes = {}
    with zipfile.ZipFile(origem_zip) as z:
        for filename in arquivos_alvo_zip(z):
            with z.open(filename) as f:
                df = pd.read_csv(f, sep=";", encoding="latin1", dtype=str,
                                 usecols=["SQ_CANDIDATO", "DS_SITUACAO_JULGAMENTO"])
            for sq, situacao in zip(df["SQ_CANDIDATO"], df["DS_SITUACAO_JULGAMENTO"]):
                situacao = valor_tse(situacao)
                if sq and situacao:
                    situacoes[str(sq).strip()] = situacao
    return situacoes


def processar_e_inserir_dataframe(df: pd.DataFrame, mapa_parlamentares: dict, situacoes: dict, cursor, conn, ano_eleicao: int):
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
            resultadoEleicao,
            fotoUrl
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            idParlamentar = VALUES(idParlamentar),
            descricaoEleicao = VALUES(descricaoEleicao),
            cargo = VALUES(cargo),
            numeroCandidato = VALUES(numeroCandidato),
            nomeUrna = VALUES(nomeUrna),
            nomeCivil = VALUES(nomeCivil),
            siglaPartido = VALUES(siglaPartido),
            situacaoCandidatura = VALUES(situacaoCandidatura),
            resultadoEleicao = VALUES(resultadoEleicao),
            fotoUrl = VALUES(fotoUrl);
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
        situacao = situacoes.get(sq_candidato) or valor_tse(row.get("DS_SITUACAO_CANDIDATURA"))
        resultado = valor_tse(row.get("DS_SIT_TOT_TURNO"))
        foto_url = montar_foto_url(ano_eleicao, sq_candidato, uf)

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
            resultado,
            foto_url
        ))

    if registros:
        cursor.executemany(sql, registros)
        conn.commit()

    return len(registros), vinculos_encontrados


def popular_candidaturas_tse(url_download: str = URL_TSE_CANDIDATOS_2026, ano_eleicao: int = 2026, arquivo_zip: str = None,
                             url_complementar: str = URL_TSE_COMPLEMENTAR_2026, arquivo_complementar: str = None):
    """Baixa os zips do TSE (ou le os arquivos locais) e popula a tabela candidaturaTse."""
    if arquivo_zip:
        logger.info(f"Usando arquivo local: {arquivo_zip}")
    else:
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

        if arquivo_zip:
            origem_zip = arquivo_zip
        else:
            origem_zip = baixar_zip(url_download)
            logger.info("Download concluido.")

        # Sem o complementar a carga segue, so que sem a situacao da candidatura
        try:
            if arquivo_complementar:
                logger.info(f"Usando arquivo complementar local: {arquivo_complementar}")
                situacoes = carregar_situacoes(arquivo_complementar)
            else:
                logger.info(f"Baixando arquivo complementar: {url_complementar}")
                situacoes = carregar_situacoes(baixar_zip(url_complementar))
            logger.info(f"Situacao carregada para {len(situacoes)} candidaturas.")
        except (requests.exceptions.RequestException, OSError, zipfile.BadZipFile, ValueError) as e:
            logger.warning(f"Arquivo complementar indisponivel ({e}); situacaoCandidatura ficara sem valor.")
            situacoes = {}

        logger.info("Processando arquivos CSV...")

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

        with zipfile.ZipFile(origem_zip) as z:
            for filename in arquivos_alvo_zip(z):
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
                        df, mapa_parlamentares, situacoes, cursor, conn, ano_eleicao
                    )
                    total_inseridos += inseridos
                    total_vinculados += vinculados
                    logger.info(f"Parcial ({filename}): {inseridos} registros processados | {vinculados} vinculados.")

        logger.info(f"Carga finalizada! Total inserido: {total_inseridos} | Vinculados a parlamentares: {total_vinculados}")

    except requests.exceptions.RequestException as req_err:
        logger.error(f"Erro ao baixar dados do TSE: {req_err}")
        logger.error("Se o CDN do TSE bloquear o servidor, baixe o zip pelo navegador e rode: "
                     "python popular/candidaturaTse.py <consulta_cand_2026.zip> [consulta_cand_complementar_2026.zip]")
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
    # Uso: python popular/candidaturaTse.py [consulta_cand_2026.zip] [consulta_cand_complementar_2026.zip]
    popular_candidaturas_tse(
        arquivo_zip=sys.argv[1] if len(sys.argv) > 1 else None,
        arquivo_complementar=sys.argv[2] if len(sys.argv) > 2 else None,
    )