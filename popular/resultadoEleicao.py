"""Atualiza os resultados da eleição geral a partir da totalização do TSE
(resultados.tse.jus.br — os mesmos arquivos que alimentam o site de resultados).

Grava, por turno/cargo/abrangência:
- eleicaoResultado: comparecimento, abstenção, válidos/brancos/nulos, seções
  totalizadas, vagas, quociente eleitoral e se a disputa foi a 2º turno;
- eleicaoResultadoCandidato: votos, percentual dos válidos, colocação e
  situação (Eleito, 2º turno, Suplente...) de cada candidato;
- eleicaoResultadoPartido: cadeiras conquistadas e votos de cada partido.

E repassa a situação final para candidaturaTse.resultadoEleicao.

Presidente é gravado com uf = 'BR' (total nacional) e também por UF e 'ZZ'
(exterior). Os demais cargos existem só por UF: a composição nacional da Câmara
ou do Senado é a soma das cadeiras das 27 UFs.

É um refresh completo e idempotente: cada execução substitui os números de cada
arquivo pelos mais recentes (recontagens e decisões sobre votos sub judice
mudam o resultado depois do dia da eleição). O 2º turno entra sozinho quando o
TSE publicar os arquivos. Rode candidaturaTse.py antes para que os candidatos
fiquem vinculados a candidaturaTse.

    python popular/resultadoEleicao.py [ano] [--sem-banco]

--sem-banco só baixa e mostra o resumo, sem gravar nada.
"""

import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests

from utils.http_client import http_client
from utils.db import get_connection, garantir_conexao
from utils.execucao import ExecucaoEtl
from utils.logging_config import get_logger

logger = get_logger("ETL_Resultado_Eleicao")

ANO_PADRAO = 2026
BASE_URL = "https://resultados.tse.jus.br/oficial"
URL_CONFIG = f"{BASE_URL}/comum/config/ele-c.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

UFS = ["ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma", "mg", "ms", "mt", "pa",
       "pb", "pe", "pi", "pr", "rj", "rn", "ro", "rr", "rs", "sc", "se", "sp", "to"]

# Tipos de eleição no ele-c.json: ordinária estadual e ordinária federal
# (o mesmo ciclo traz suplementares e consultas populares, que não interessam).
TIPOS_ELEICAO_GERAL = {"1", "8"}
CARGO_PRESIDENTE, CARGO_GOVERNADOR = 1, 3
CARGO_DEP_ESTADUAL, CARGO_DEP_DISTRITAL = 7, 8
CARGOS_COM_SEGUNDO_TURNO = (CARGO_PRESIDENTE, CARGO_GOVERNADOR)
SITUACAO_SEGUNDO_TURNO = "2º TURNO"


def inteiro(valor):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def decimal(valor):
    """Percentuais do TSE vêm com vírgula decimal ('47,027772356')."""
    try:
        return round(Decimal(str(valor).strip().replace(",", ".")), 4)
    except (TypeError, ValueError, InvalidOperation):
        return None


def data_hora(data, hora):
    try:
        return datetime.strptime(f"{data} {hora}", "%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return None


def baixar_json(url):
    """JSON da URL, ou None quando o arquivo não existe (404).

    Sem get_safe de propósito: o cache em disco do staging serviria um
    resultado antigo, e este script existe justamente para buscar o atual.
    """
    resposta = http_client.get(url, headers=HEADERS, timeout=30)
    if resposta.status_code == 404:
        return None
    resposta.raise_for_status()
    return resposta.json()


def descobrir_eleicoes(ano):
    """Eleições gerais do ano no ele-c.json: lista de (codigo, turno, [cargos])."""
    config = baixar_json(URL_CONFIG)
    if not config:
        raise RuntimeError(f"Configuração de eleições do TSE indisponível: {URL_CONFIG}")

    eleicoes, segundos_turnos = {}, {}
    for pleito in config.get("pl", []):
        if pleito.get("c") != f"ele{ano}":
            continue
        for eleicao in pleito.get("e", []):
            if eleicao.get("tp") not in TIPOS_ELEICAO_GERAL:
                continue
            cargos = sorted({int(cp["cd"]) for abr in eleicao.get("abr", []) for cp in abr.get("cp", [])})
            eleicoes[eleicao["cd"]] = (int(eleicao["t"]), cargos)
            if eleicao.get("cdt2"):
                segundos_turnos[eleicao["cdt2"]] = [c for c in cargos if c in CARGOS_COM_SEGUNDO_TURNO]

    # Antes do 2º turno o ele-c.json só o cita como `cdt2` do 1º; tenta assim
    # mesmo — os arquivos passam a existir quando a totalização começar.
    for codigo, cargos in segundos_turnos.items():
        if codigo not in eleicoes and cargos:
            eleicoes[codigo] = (2, cargos)

    return sorted(((codigo, turno, cargos) for codigo, (turno, cargos) in eleicoes.items()),
                  key=lambda e: (e[1], e[0]))


def abrangencias_do_cargo(cargo):
    if cargo == CARGO_PRESIDENTE:
        return ["br"] + UFS + ["zz"]
    if cargo == CARGO_DEP_ESTADUAL:
        return [uf for uf in UFS if uf != "df"]
    if cargo == CARGO_DEP_DISTRITAL:
        return ["df"]
    return UFS


def interpretar(dados, ano, turno):
    """Converte um arquivo de resultado do TSE nas linhas das três tabelas."""
    cargo = dados["carg"][0]
    eleitorado, votos, secoes = dados.get("e", {}), dados.get("v", {}), dados.get("s", {})
    votos_validos = inteiro(votos.get("vv"))
    votos_totais = inteiro(votos.get("tv"))
    federacoes = {f["n"]: f.get("sg") for f in cargo.get("fed", [])}

    candidatos, partidos = {}, {}
    for agremiacao in cargo.get("agr", []):
        coligacao = (agremiacao.get("com") or "")[:500] or None
        for partido in agremiacao.get("par", []):
            sigla = (partido.get("sg") or "").strip().upper()[:50]
            if not sigla:
                continue
            linha_partido = partidos.setdefault(sigla, {
                "siglaPartido": sigla,
                "numeroPartido": partido.get("n"),
                "nomePartido": (partido.get("nm") or "")[:255] or None,
                "federacao": federacoes.get(partido.get("nfed")),
                "coligacao": coligacao,
                "cadeiras": 0,
                "votosNominais": 0,
                "votosLegenda": 0,
            })
            linha_partido["votosNominais"] += inteiro(partido.get("tvan")) or 0
            linha_partido["votosLegenda"] += inteiro(partido.get("tval")) or 0

            for cand in partido.get("cand", []):
                situacao = (cand.get("st") or "").strip()
                # `e` = 's' também para quem só passou ao 2º turno; quem
                # conquistou a vaga é quem tem a situação "Eleito...".
                eleito = situacao.upper().startswith("ELEITO")
                if eleito:
                    linha_partido["cadeiras"] += 1
                vice = next((v for v in cand.get("vs", []) if v.get("tp") == "v"), None)
                candidatos[cand["sqcand"]] = {
                    "sqCandidato": cand["sqcand"],
                    "numeroCandidato": cand.get("n"),
                    "nomeUrna": (cand.get("nmu") or "")[:255] or None,
                    "nomeCivil": (cand.get("nm") or "")[:255] or None,
                    "siglaPartido": sigla,
                    "coligacao": coligacao,
                    "nomeVice": (vice.get("nmu") or "")[:255] or None if vice else None,
                    "posicao": inteiro(cand.get("seq")),
                    "votos": inteiro(cand.get("vap")),
                    "percentualVotos": decimal(cand.get("pvapn")),
                    "situacao": situacao[:100] or None,
                    "eleito": 1 if eleito else 0,
                    "segundoTurno": 1 if situacao.upper() == SITUACAO_SEGUNDO_TURNO else 0,
                    "situacaoVotos": (cand.get("dvt") or "")[:100] or None,
                }

    for linha_partido in partidos.values():
        total = linha_partido["votosNominais"] + linha_partido["votosLegenda"]
        linha_partido["votos"] = total
        linha_partido["percentualVotos"] = (
            round(Decimal(total) * 100 / votos_validos, 4) if votos_validos else None
        )

    resultado = {
        "anoEleicao": ano,
        "turno": turno,
        "cargo": (cargo.get("nmn") or "").strip().upper(),
        "uf": dados["cdabr"].upper(),
        "vagas": inteiro(cargo.get("nv")),
        "quocienteEleitoral": inteiro(cargo.get("qe")),
        "eleitorado": inteiro(eleitorado.get("te")),
        "comparecimento": inteiro(eleitorado.get("c")),
        "abstencoes": inteiro(eleitorado.get("a")),
        "percentualComparecimento": decimal(eleitorado.get("pcn")),
        "percentualAbstencao": decimal(eleitorado.get("pan")),
        "votosTotais": votos_totais,
        "votosValidos": votos_validos,
        "votosNominais": inteiro(votos.get("vnom")),
        "votosLegenda": inteiro(votos.get("vl")),
        "votosBrancos": inteiro(votos.get("vb")),
        "votosNulos": inteiro(votos.get("tvn")),
        "votosAnuladosSubJudice": inteiro(votos.get("vansj")),
        "percentualValidos": (
            round(Decimal(votos_validos) * 100 / votos_totais, 4) if votos_validos and votos_totais else None
        ),
        "percentualBrancos": decimal(votos.get("pvbn")),
        "percentualNulos": decimal(votos.get("ptvnn")),
        "secoes": inteiro(secoes.get("ts")),
        "secoesTotalizadas": inteiro(secoes.get("st")),
        "percentualSecoesTotalizadas": decimal(secoes.get("pstn")),
        "totalizacaoFinal": 1 if dados.get("tf") == "s" else 0,
        "haSegundoTurno": 1 if any(c["segundoTurno"] for c in candidatos.values()) else 0,
        "dataTotalizacao": data_hora(dados.get("dt"), dados.get("ht")),
        "dataGeracao": data_hora(dados.get("dg"), dados.get("hg")),
    }
    return resultado, list(candidatos.values()), list(partidos.values())


def coletar(ano):
    """Baixa e interpreta todos os arquivos de resultado disponíveis do ano."""
    coletados, falhas = [], 0
    for codigo, turno, cargos in descobrir_eleicoes(ano):
        logger.info(f"Eleição {codigo} ({turno}º turno), cargos {cargos}")
        for cargo in cargos:
            for abrangencia in abrangencias_do_cargo(cargo):
                url = (f"{BASE_URL}/ele{ano}/{codigo}/dados/{abrangencia}/"
                       f"{abrangencia}-c{cargo:04d}-e{int(codigo):06d}-u.json")
                try:
                    dados = baixar_json(url)
                    # 404 é esperado: UF sem 2º turno, ou 2º turno ainda não realizado
                    if dados and dados.get("carg"):
                        coletados.append(interpretar(dados, ano, turno))
                except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                    falhas += 1
                    logger.error(f"Falha em {url}: {e}")
    return coletados, falhas


SQL_RESULTADO = """
    INSERT INTO eleicaoResultado ({colunas}) VALUES ({marcadores})
    ON DUPLICATE KEY UPDATE idEleicaoResultado = LAST_INSERT_ID(idEleicaoResultado), {atualizacoes}
"""
CHAVE_RESULTADO = ("anoEleicao", "turno", "cargo", "uf")


def gravar(conexao, cursor, coletados, execucao):
    cursor.execute("SELECT sqCandidato, idCandidaturaTse, anoEleicao FROM candidaturaTse")
    candidaturas = {(ano, sq): id_candidatura for sq, id_candidatura, ano in cursor.fetchall()}

    falhas = 0
    for resultado, candidatos, partidos in coletados:
        garantir_conexao(conexao)
        try:
            colunas = list(resultado)
            cursor.execute(SQL_RESULTADO.format(
                colunas=", ".join(colunas),
                marcadores=", ".join(["%s"] * len(colunas)),
                atualizacoes=", ".join(f"{c} = VALUES({c})" for c in colunas if c not in CHAVE_RESULTADO),
            ), [resultado[c] for c in colunas])
            id_resultado = cursor.lastrowid

            # Substitui em vez de fazer upsert: candidato que saiu do arquivo
            # (registro cassado, renúncia) não pode sobrar na tabela.
            cursor.execute("DELETE FROM eleicaoResultadoCandidato WHERE idEleicaoResultado = %s", (id_resultado,))
            cursor.execute("DELETE FROM eleicaoResultadoPartido WHERE idEleicaoResultado = %s", (id_resultado,))

            if candidatos:
                colunas = list(candidatos[0])
                cursor.executemany(
                    f"INSERT INTO eleicaoResultadoCandidato (idEleicaoResultado, idCandidaturaTse, {', '.join(colunas)}) "
                    f"VALUES (%s, %s, {', '.join(['%s'] * len(colunas))})",
                    [(id_resultado, candidaturas.get((resultado["anoEleicao"], c["sqCandidato"])),
                      *[c[col] for col in colunas]) for c in candidatos],
                )
            if partidos:
                colunas = list(partidos[0])
                cursor.executemany(
                    f"INSERT INTO eleicaoResultadoPartido (idEleicaoResultado, {', '.join(colunas)}) "
                    f"VALUES (%s, {', '.join(['%s'] * len(colunas))})",
                    [(id_resultado, *[p[col] for col in colunas]) for p in partidos],
                )
            conexao.commit()
            execucao.incrementar(processados=1, registros=1 + len(candidatos) + len(partidos))
        except Exception as e:
            conexao.rollback()
            falhas += 1
            execucao.incrementar(erros=1)
            logger.error(f"Erro ao gravar {resultado['cargo']} {resultado['uf']} ({resultado['turno']}º turno): {e}")

    # Situação final em candidaturaTse, um turno por vez em ordem: quem foi ao
    # 2º turno termina com o resultado do 2º. Presidente só pela linha nacional.
    for turno in sorted({r["turno"] for r, _, _ in coletados}):
        for ano in sorted({r["anoEleicao"] for r, _, _ in coletados}):
            cursor.execute("""
                UPDATE candidaturaTse c
                JOIN eleicaoResultadoCandidato rc ON rc.idCandidaturaTse = c.idCandidaturaTse
                JOIN eleicaoResultado r ON r.idEleicaoResultado = rc.idEleicaoResultado
                SET c.resultadoEleicao = UPPER(rc.situacao)
                WHERE r.anoEleicao = %s AND r.turno = %s AND rc.situacao IS NOT NULL
                  AND (r.cargo <> 'PRESIDENTE' OR r.uf = 'BR')
            """, (ano, turno))
    conexao.commit()
    return falhas


def cadeiras_por_partido(coletados, cargo):
    cadeiras = defaultdict(int)
    for resultado, _, partidos in coletados:
        if resultado["cargo"] == cargo:
            for partido in partidos:
                cadeiras[partido["siglaPartido"]] += partido["cadeiras"]
    return sorted(((s, n) for s, n in cadeiras.items() if n), key=lambda x: (-x[1], x[0]))


def descrever(candidato):
    return (f"{candidato['nomeUrna']} ({candidato['siglaPartido']}) "
            f"{candidato['percentualVotos']:.2f}% — {candidato['votos']:,} votos".replace(",", "."))


def mostrar_resumo(coletados):
    for turno in sorted({r["turno"] for r, _, _ in coletados}):
        do_turno = [c for c in coletados if c[0]["turno"] == turno]
        logger.info(f"════════ {turno}º TURNO ════════")

        for resultado, candidatos, _ in do_turno:
            if resultado["cargo"] != "PRESIDENTE" or resultado["uf"] != "BR":
                continue
            logger.info(
                f"PRESIDENTE — {resultado['percentualSecoesTotalizadas']:.2f}% das seções | "
                f"comparecimento {resultado['percentualComparecimento']:.2f}% | "
                f"brancos {resultado['percentualBrancos']:.2f}% | nulos {resultado['percentualNulos']:.2f}%"
            )
            for candidato in sorted(candidatos, key=lambda c: -(c["votos"] or 0))[:5]:
                logger.info(f"   {descrever(candidato)} [{candidato['situacao']}]")

        governos = sorted((c for c in do_turno if c[0]["cargo"] == "GOVERNADOR"), key=lambda c: c[0]["uf"])
        if governos:
            logger.info("GOVERNADORES")
        for resultado, candidatos, _ in governos:
            destaque = [c for c in candidatos if c["eleito"] or c["segundoTurno"]]
            destaque.sort(key=lambda c: -(c["votos"] or 0))
            rotulo = "2º turno entre" if resultado["haSegundoTurno"] else "eleito"
            logger.info(f"   {resultado['uf']} {rotulo}: " + " × ".join(descrever(c) for c in destaque))

        for cargo, titulo in (("SENADOR", "SENADO"), ("DEPUTADO FEDERAL", "CÂMARA DOS DEPUTADOS")):
            cadeiras = cadeiras_por_partido(do_turno, cargo)
            if cadeiras:
                logger.info(f"{titulo} — {sum(n for _, n in cadeiras)} cadeiras: "
                            + ", ".join(f"{sigla} {n}" for sigla, n in cadeiras))

        assembleias = sum(p["cadeiras"] for r, _, ps in do_turno
                          if r["cargo"] in ("DEPUTADO ESTADUAL", "DEPUTADO DISTRITAL") for p in ps)
        if assembleias:
            logger.info(f"ASSEMBLEIAS LEGISLATIVAS — {assembleias} cadeiras gravadas por UF e partido")


def atualizar_resultados(ano=ANO_PADRAO, gravar_no_banco=True):
    if not gravar_no_banco:
        coletados, falhas = coletar(ano)
        mostrar_resumo(coletados)
        return bool(coletados) and not falhas

    conexao, cursor = get_connection()
    execucao = ExecucaoEtl(conexao, "popular/resultadoEleicao.py")
    try:
        coletados, falhas = coletar(ano)
        execucao.incrementar(erros=falhas)
        if not coletados:
            logger.error(f"Nenhum arquivo de resultado encontrado para {ano}.")
            execucao.finalizar("FALHA", "nenhum arquivo de resultado encontrado")
            return False

        falhas += gravar(conexao, cursor, coletados, execucao)
        mostrar_resumo(coletados)
        execucao.finalizar("SUCESSO" if not falhas else "FALHA")
        logger.info(f"Resultados de {ano}: {len(coletados)} disputas gravadas, {falhas} falha(s).")
        return not falhas
    except Exception as e:
        conexao.rollback()
        execucao.finalizar("FALHA", str(e))
        raise
    finally:
        cursor.close()
        conexao.close()


if __name__ == "__main__":
    argumentos = [a for a in sys.argv[1:] if not a.startswith("--")]
    sucesso = atualizar_resultados(
        ano=int(argumentos[0]) if argumentos else ANO_PADRAO,
        gravar_no_banco="--sem-banco" not in sys.argv,
    )
    if not sucesso:
        sys.exit(1)
