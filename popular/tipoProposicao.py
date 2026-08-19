import os
from utils.http_client import http_client
from utils.db import get_connection

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

db, cursor = get_connection(buffered=True)


TIPOS_FIXOS = [
    
    ("PDC", "Camara", "Projeto de Decreto Legislativo"),
    ("PL", "Camara", "Projeto de Lei"),
    ("PLP", "Camara", "Projeto de Lei Complementar"),
    ("MPV", "Camara", "Medida Provisória"),
    ("PLV", "Camara", "Projeto de Lei de Conversão"),
    ("PDL", "Camara", "Projeto de Decreto Legislativo"),
    ("PEC", "Camara", "Proposta de Emenda à Constituição"),
    ("VET", "Camara", "Veto Presidencial"),
    
    
    ("PLS", "Senado", "Projeto de Lei do Senado Federal"),
    ("PLC", "Senado", "Projeto de Lei da Câmara dos Deputados (SF)"),
    ("PDS", "Senado", "Projeto de Decreto Legislativo (SF)"),
    ("PEC", "Senado", "Proposta de Emenda à Constituição"),
    ("VET", "Senado", "Veto Presidencial"),
    
   
    ("PL", "Senado", "Projeto de Lei"),
    ("PDL", "Senado", "Projeto de Decreto Legislativo"),
    ("MPV", "Senado", "Medida Provisória"),
    
    
    ("PDC", "Congresso", "Projeto de Decreto Legislativo de Autorização do Congresso Nacional"),
    ("PDL", "Congresso", "Projeto de Decreto Legislativo de Autorização do Congresso Nacional"),
    ("PLN", "Congresso", "Projeto de Lei (CN)"),
    ("PDN", "Congresso", "Projeto de Decreto Legislativo (CN)"),
]


def ja_existe(sigla, casa):
    cursor.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM tipoProposicao WHERE sigla = %s AND casa = %s
        )
    """,
        (sigla, casa),
    )
    return cursor.fetchone()[0] == 1


def popular_tipo_proposicao():
    print("=" * 60)
    print(" POPULANDO TABELA tipoProposicao")
    print("=" * 60)

    print("\n Inserindo/atualizando tipos...")
    inseridos = {"Camara": 0, "Senado": 0, "Congresso": 0}

    for sigla, casa, nome in TIPOS_FIXOS:
        cursor.execute(
            """
            INSERT INTO tipoProposicao (sigla, nome, casa)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE nome = VALUES(nome)
        """,
            (sigla, nome, casa),
        )

        inseridos[casa] += 1
        print(f"    [{casa}] {sigla} — {nome}")

    db.commit()

    print("\n" + "=" * 60)
    print(" FINALIZADO!")
    print("=" * 60)

    print("\n RESUMO POR CASA:")
    for casa, total in inseridos.items():
        print(f"   {casa}: {total} tipos")

    print(f"\n TOTAL GERAL: {len(TIPOS_FIXOS)} tipos")

    print("\n LISTA COMPLETA DOS TIPOS:")
    cursor.execute(
        """
        SELECT idTipoProposicao, casa, sigla, nome 
        FROM tipoProposicao 
        ORDER BY casa, idTipoProposicao
    """
    )

    for id_, casa, sigla, nome in cursor.fetchall():
        print(f"   [{id_:02}] [{casa}] {sigla} — {nome}")


def buscar_tipos_das_apis():
    """
    Função opcional para buscar tipos das APIs
    (mantida para referência, mas não usada no script principal)
    """
    print("\n Buscando tipos da API da Câmara...")
    url_camara = "https://dadosabertos.camara.leg.br/api/v2/referencias/proposicoes/siglaTipo"

    try:
        dados = http_client.get_safe(url_camara, timeout=15).json().get("dados", [])
        print(f"   Encontrados {len(dados)} tipos na API da Câmara")
    except Exception as e:
        print(f"    Erro: {e}")

    print("\n Buscando tipos da API do Senado...")
    url_senado = "https://legis.senado.leg.br/dadosabertos/materia/tipos"

    try:
        res = http_client.get_safe(
            url_senado, headers={"Accept": "application/json"}, timeout=15
        ).json()
        lista = (
            res.get("ListaTiposMateria", {})
            .get("TiposMateria", {})
            .get("TipoMateria", [])
        )
        print(f"   Encontrados {len(lista)} tipos na API do Senado")
    except Exception as e:
        print(f"    Erro: {e}")


if __name__ == "__main__":
    try:
        popular_tipo_proposicao()
    except Exception as e:
        print(f" Erro geral: {e}")
        import traceback

        traceback.print_exc()
    finally:
        cursor.close()
        db.close()
        print("\n CONEXÃO COM O BANCO ENCERRADA")
