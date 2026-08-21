"""Resolução determinística da `siglaBancada` das orientações da Câmara.

Nos dumps de orientações a bancada aparece abreviada e truncada:
    "Bl AvanSolidPrd..."   bloco  -> AVANTE, SOLIDARIEDADE, PRD
    "Bl UniPpPsd..."       bloco  -> UNIÃO, PP, PSD, REPUBLICANOS, ... (truncado)
    "Fdr PT-PCdoB-PV"      federação
    "Fdr PSDB-CIDADAN"     federação, truncada
    "Solidaried" / "Republican" / "Podemos"   partido (sigla ou nome truncados)

Regra para blocos/federações: a abreviação é a concatenação, NA ORDEM DO NOME
do bloco, de um prefixo da sigla de cada partido (federação dentro do bloco
aparece como "Fdr" + prefixo do 1º partido). Um bloco casa quando é possível
consumir a abreviação inteira como sequência de prefixos dos seus partidos em
ordem — e só ele casa (ambiguidade => não resolve). Não há inferência a partir
de letras soltas: ou a sequência bate, ou fica NULL.
"""

import re
import unicodedata


def normalizar(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize("NFD", str(texto))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def tipo_bancada(sigla_bancada):
    s = (sigla_bancada or "").strip()
    if s.startswith("Bl "):
        return "bloco"
    if s.lower().startswith("fdr ") or s.lower().startswith("federa"):
        return "federacao"
    if s in ("Governo", "Maioria", "Minoria", "Oposição", "Oposicao"):
        return "lideranca"
    return "partido"


def parsear_nome_bloco(nome, federacoes_por_nome):
    """Converte o nome do bloco ("UNIÃO, PP, Federação PSDB CIDADANIA, PODE")
    numa sequência ordenada de partidos. Entradas "Federação ..." são expandidas
    via `federacoes_por_nome` {nome_normalizado: [siglas]} e marcadas como
    federação (a abreviação usa "Fdr" + prefixo do 1º partido)."""
    sequencia = []
    for parte in (nome or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if normalizar(parte).startswith("federacao"):
            siglas = federacoes_por_nome.get(normalizar(parte))
            if not siglas:
                # nome não casou com nenhuma federação conhecida: usa as palavras
                siglas = [p for p in parte.split()[1:] if p.upper() == p or p.lower() != p]
            sequencia.append({"federacao": True, "siglas": siglas})
        else:
            sequencia.append({"federacao": False, "siglas": [parte]})
    return sequencia


def _consumir_prefixo(restante, sigla):
    """Quantos caracteres de `restante` são prefixo de `sigla` (normalizados).
    Exige pelo menos 2 caracteres, salvo quando a sigla ou o restante têm menos."""
    alvo = normalizar(sigla)
    n = 0
    while n < len(restante) and n < len(alvo) and restante[n] == alvo[n]:
        n += 1
    minimo = min(2, len(alvo), len(restante))
    return n if n >= minimo and n > 0 else 0


def casar_abreviacao_bloco(abreviacao, sequencia):
    """True se `abreviacao` (sem o prefixo 'Bl ' e sem '...') pode ser lida
    como prefixos, em ordem, dos partidos da `sequencia`."""
    restante = normalizar(abreviacao)
    if not restante:
        return False
    for entrada in sequencia:
        if not restante:
            break
        if entrada["federacao"]:
            if not restante.startswith("fdr"):
                return False
            restante = restante[3:]
            if restante and entrada["siglas"]:
                n = _consumir_prefixo(restante, entrada["siglas"][0])
                if n == 0:
                    return False
                restante = restante[n:]
                # demais partidos da federação podem ou não aparecer
                for sigla in entrada["siglas"][1:]:
                    n = _consumir_prefixo(restante, sigla)
                    if n == 0:
                        break
                    restante = restante[n:]
        else:
            n = _consumir_prefixo(restante, entrada["siglas"][0])
            if n == 0:
                return False
            restante = restante[n:]
    return not restante


def resolver_bloco(sigla_bancada, blocos):
    """blocos: lista de dicts {idBloco, federacao(bool), sequencia}. Devolve o
    idBloco único que casa, ou None (nenhum ou mais de um)."""
    tipo = tipo_bancada(sigla_bancada)
    s = (sigla_bancada or "").strip().replace("...", "")
    candidatos = []
    if tipo == "bloco":
        abrev = s[3:]
        for b in blocos:
            if not b["federacao"] and casar_abreviacao_bloco(abrev, b["sequencia"]):
                candidatos.append(b["idBloco"])
    elif tipo == "federacao":
        corpo = re.sub(r"^(fdr|federacao)\s*", "", normalizar(s).replace("fdr", "fdr ", 1), flags=re.I).strip()
        partes = [p for p in re.split(r"[-\s]+", s.split(" ", 1)[1] if " " in s else "") if p]
        for b in blocos:
            if not b["federacao"]:
                continue
            siglas = [x for e in b["sequencia"] for x in e["siglas"]]
            if len(partes) <= len(siglas) and all(
                _consumir_prefixo(normalizar(p), sg) == len(normalizar(p)) for p, sg in zip(partes, siglas)
            ) and partes:
                candidatos.append(b["idBloco"])
    candidatos = sorted(set(candidatos))
    return candidatos[0] if len(candidatos) == 1 else None


def resolver_partido(sigla_bancada, partidos):
    """partidos: lista de (sigla, nome). Casa por igualdade normalizada ou por
    prefixo ÚNICO da sigla ou do nome (ex.: 'Solidaried' -> SOLIDARIEDADE,
    'Podemos' -> PODE, 'Republican' -> REPUBLICANOS). Devolve a sigla ou None."""
    alvo = normalizar(sigla_bancada)
    if not alvo:
        return None
    exatos = {sigla for sigla, nome in partidos if normalizar(sigla) == alvo or normalizar(nome) == alvo}
    if len(exatos) == 1:
        return next(iter(exatos))
    prefixos = {
        sigla for sigla, nome in partidos
        if normalizar(sigla).startswith(alvo) or normalizar(nome).startswith(alvo)
    }
    return next(iter(prefixos)) if len(prefixos) == 1 else None
