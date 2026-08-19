import os
import re
import time
import unicodedata

from utils.db import get_connection
from utils.execucao import ExecucaoEtl

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

MESES = [
    int(mes.strip())
    for mes in os.getenv("EMENDAS_MESES", "").split(",")
    if mes.strip()
]

ANO = os.getenv("EMENDAS_ANO")

db, cursor = get_connection(dictionary=True)
execucao = ExecucaoEtl(db, "popular/relacionarEmendaParlamentar.py")


def normalizar_nome(nome):
    if not nome:
        return ""

    nome = nome.upper().strip()
    nome = unicodedata.normalize("NFD", nome)
    nome = "".join(
        char for char in nome
        if unicodedata.category(char) != "Mn"
    )

    nome = re.sub(r"[^A-Z0-9 ]", " ", nome)
    nome = re.sub(r"\s+", " ", nome).strip()

    termos_remover = {
        "DEP",
        "DEPUTADO",
        "DEPUTADA",
        "SEN",
        "SENADOR",
        "SENADORA"
    }

    partes = [
        parte for parte in nome.split()
        if parte not in termos_remover
    ]

    return " ".join(partes)


def tipo_emenda_nao_aplicavel(tipo_emenda, nome_autor):
    texto = f"{tipo_emenda or ''} {nome_autor or ''}".upper()

    termos_coletivos = [
        "BANCADA",
        "COMISSAO",
        "COMISSÃO",
        "COMITE",
        "COMITÊ"
    ]

    return any(termo in texto for termo in termos_coletivos)


def adicionar_nome_no_indice(indice, nome_original, parlamentar, campo_match):
    nome_normalizado = normalizar_nome(nome_original)

    if not nome_normalizado:
        return

    if nome_normalizado not in indice:
        indice[nome_normalizado] = []

    indice[nome_normalizado].append({
        "parlamentar": parlamentar,
        "campoMatch": campo_match,
        "nomeOriginal": nome_original
    })


def buscar_parlamentares_indexados():
    cursor.execute("""
        SELECT
            idParlamentar,
            nomeCivil,
            nomeUrna,
            partidoAtual,
            uf
        FROM parlamentar
    """)

    parlamentares = cursor.fetchall()
    indice = {}

    for parlamentar in parlamentares:
        adicionar_nome_no_indice(
            indice,
            parlamentar.get("nomeCivil"),
            parlamentar,
            "nomeCivil"
        )

        adicionar_nome_no_indice(
            indice,
            parlamentar.get("nomeUrna"),
            parlamentar,
            "nomeUrna"
        )

    return indice


def buscar_emendas_para_relacionar():
    filtros = []
    parametros = []

    sql = """
        SELECT DISTINCT
            e.idEmenda,
            e.codigoEmenda,
            e.autor,
            e.nomeAutor,
            e.tipoEmenda,
            e.ano
        FROM emenda e
    """

    if MESES:
        sql += """
            JOIN emendaDocumento ed
                ON ed.idEmenda = e.idEmenda
        """

        placeholders = ", ".join(["%s"] * len(MESES))
        filtros.append(f"MONTH(ed.data) IN ({placeholders})")
        parametros.extend(MESES)

    if ANO:
        filtros.append("e.ano = %s")
        parametros.append(int(ANO))

    if filtros:
        sql += " WHERE " + " AND ".join(filtros)

    cursor.execute(sql, parametros)
    return cursor.fetchall()


def deduplicar_matches(matches):
    matches_unicos = {}

    for match in matches:
        parlamentar = match["parlamentar"]
        id_parlamentar = parlamentar["idParlamentar"]

        if id_parlamentar not in matches_unicos:
            matches_unicos[id_parlamentar] = match

    return list(matches_unicos.values())


def limpar_vinculos_emenda(id_emenda):
    cursor.execute(
        "DELETE FROM emendaParlamentar WHERE idEmenda = %s",
        (id_emenda,)
    )


def inserir_vinculo(
    id_emenda,
    codigo_emenda,
    id_parlamentar,
    nome_autor_portal,
    nome_autor_normalizado,
    metodo_vinculo,
    confianca_vinculo
):
    if not id_parlamentar:
        raise ValueError(
            f"Tentativa de inserir vínculo sem idParlamentar para emenda {codigo_emenda}"
        )

    sql = """
        INSERT INTO emendaParlamentar (
            idEmenda,
            codigoEmenda,
            idParlamentar,
            nomeAutorPortal,
            nomeAutorNormalizado,
            metodoVinculo,
            confiancaVinculo
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            codigoEmenda = VALUES(codigoEmenda),
            nomeAutorPortal = VALUES(nomeAutorPortal),
            nomeAutorNormalizado = VALUES(nomeAutorNormalizado),
            metodoVinculo = VALUES(metodoVinculo),
            confiancaVinculo = VALUES(confiancaVinculo)
    """

    valores = (
        id_emenda,
        codigo_emenda,
        id_parlamentar,
        nome_autor_portal,
        nome_autor_normalizado,
        metodo_vinculo,
        confianca_vinculo
    )

    cursor.execute(sql, valores)


print("=" * 60)
print("Relacionando emendas com parlamentares")
print("=" * 60)

if MESES:
    print(f"Filtrando emendas com documentos nos meses: {MESES}")
else:
    print("Sem filtro de mês. Todas as emendas serão consideradas.")

if ANO:
    print(f"Filtrando emendas do ano: {ANO}")
else:
    print("Sem filtro de ano.")

parlamentares_por_nome = buscar_parlamentares_indexados()
emendas = buscar_emendas_para_relacionar()

print(f"Total de nomes indexados: {len(parlamentares_por_nome)}")
print(f"Total de emendas para analisar: {len(emendas)}")

contador_vinculado = 0
contador_ambiguo = 0
contador_nao_encontrado = 0
contador_nao_aplicavel = 0
contador_sem_nome_autor = 0
contador_erros = 0

start_time = time.time()
for emenda in emendas:
    id_emenda = emenda["idEmenda"]
    codigo_emenda = emenda["codigoEmenda"]
    nome_autor = emenda["nomeAutor"]
    tipo_emenda = emenda["tipoEmenda"]
    nome_normalizado = normalizar_nome(nome_autor)

    try:
        limpar_vinculos_emenda(id_emenda)

        if not nome_normalizado:
            print(f"Sem nome de autor: {codigo_emenda}")
            contador_sem_nome_autor += 1
            db.commit()
            continue

        if tipo_emenda_nao_aplicavel(tipo_emenda, nome_autor):
            print(f"Não aplicável: {codigo_emenda} - {nome_autor}")
            contador_nao_aplicavel += 1
            db.commit()
            continue

        matches = deduplicar_matches(
            parlamentares_por_nome.get(nome_normalizado, [])
        )

        if len(matches) == 1:
            parlamentar = matches[0]["parlamentar"]

            inserir_vinculo(
                id_emenda=id_emenda,
                codigo_emenda=codigo_emenda,
                id_parlamentar=parlamentar["idParlamentar"],
                nome_autor_portal=nome_autor,
                nome_autor_normalizado=nome_normalizado,
                metodo_vinculo=f"match_exato_{matches[0]['campoMatch']}_normalizado",
                confianca_vinculo=100
            )

            contador_vinculado += 1

        elif len(matches) > 1:
            candidatos = [
                f"{match['parlamentar']['idParlamentar']} - "
                f"{match['parlamentar'].get('nomeCivil')} / "
                f"{match['parlamentar'].get('nomeUrna')}"
                for match in matches
            ]

            print(
                f"Ambíguo: {codigo_emenda} - {nome_autor} | "
                f"Candidatos: {candidatos}"
            )

            contador_ambiguo += 1

        else:
            print(f"Não encontrado: {codigo_emenda} - {nome_autor}")
            contador_nao_encontrado += 1

        db.commit()

    except Exception as e:
        db.rollback()
        contador_erros += 1
        print(f"Erro ao relacionar emenda {codigo_emenda}: {e}")

    if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
        print(f"\n[LIMITE DE TEMPO] Interrompido após {tempo_limite_segundos}s.")
        break

execucao.incrementar(processados=len(emendas), registros=contador_vinculado, erros=contador_erros)
execucao.finalizar("SUCESSO" if contador_erros == 0 else "FALHA")

print("\n" + "=" * 60)
print("Relacionamento concluído")
print("=" * 60)
print(f"Vinculados: {contador_vinculado}")
print(f"Ambíguos: {contador_ambiguo}")
print(f"Não encontrados: {contador_nao_encontrado}")
print(f"Não aplicáveis: {contador_nao_aplicavel}")
print(f"Sem nome de autor: {contador_sem_nome_autor}")
print(f"Erros: {contador_erros}")

db.commit()
cursor.close()
db.close()