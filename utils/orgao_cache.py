from utils.db import get_connection


class OrgaoCache:
    """Resolve idApi -> idOrgao de uma casa, criando um placeholder quando o
    órgão ainda não é conhecido.

    Decisões deliberadas:
    - As escritas em `orgao` usam uma CONEXÃO PRÓPRIA e são commitadas na hora.
      Órgão é entidade de referência, independente da transação de tramitação/
      votação do chamador: um rollback do chamador não pode desfazer um órgão
      já criado (e deixar o cache apontando para um id inexistente), e o commit
      daqui não pode commitar a transação de dados do chamador.
    - Placeholder não vence dado bom: quem chegar depois com sigla/nome reais
      atualiza a linha "N/A / Órgão não mapeado".

    `db` e `cursor` são aceitos por compatibilidade de assinatura, mas não são
    usados para escrita.
    """

    def __init__(self, db, cursor, casa, logger=None):
        self.casa = casa
        self.logger = logger
        self.cache = {}
        self.placeholders = set()

        self.db, self.cursor = get_connection(buffered=True)
        self.cursor.execute(
            "SELECT idOrgao, idApi, sigla, nome FROM orgao WHERE casa = %s", (casa,)
        )
        for id_orgao, id_api, sigla, nome in self.cursor.fetchall():
            self.cache[str(id_api)] = id_orgao
            if sigla == "N/A" or (nome or "").startswith("Órgão não mapeado"):
                self.placeholders.add(id_orgao)

    def garantir(self, id_api_orgao, sigla=None, nome=None):
        if not id_api_orgao:
            return None
        id_api_str = str(id_api_orgao)

        id_orgao = self.cache.get(id_api_str)
        if id_orgao is None:
            self.cursor.execute(
                "SELECT idOrgao, sigla, nome FROM orgao WHERE idApi = %s AND casa = %s",
                (id_api_str, self.casa),
            )
            res = self.cursor.fetchone()
            if res:
                id_orgao = res[0]
                self.cache[id_api_str] = id_orgao
                if res[1] == "N/A" or (res[2] or "").startswith("Órgão não mapeado"):
                    self.placeholders.add(id_orgao)

        if id_orgao is not None:
            if id_orgao in self.placeholders and (sigla or nome):
                self.cursor.execute(
                    """UPDATE orgao SET sigla = COALESCE(%s, sigla), nome = COALESCE(%s, nome)
                       WHERE idOrgao = %s AND (sigla = 'N/A' OR nome LIKE 'Órgão não mapeado%%')""",
                    (sigla, nome, id_orgao),
                )
                self.db.commit()
                if sigla and nome:
                    self.placeholders.discard(id_orgao)
            return id_orgao

        self.cursor.execute(
            "INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
            (id_api_str, sigla or "N/A", nome or f"Órgão não mapeado ({sigla or id_api_str})", self.casa),
        )
        self.db.commit()
        id_novo = self.cursor.lastrowid
        self.cache[id_api_str] = id_novo
        if not (sigla and nome):
            self.placeholders.add(id_novo)
        if self.logger:
            self.logger.info(f"Novo órgão criado: {sigla or id_api_str} (Casa: {self.casa}, ID API: {id_api_orgao})")
        return id_novo

    def fechar(self):
        try:
            self.cursor.close()
            self.db.close()
        except Exception:
            pass
