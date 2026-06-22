class OrgaoCache:
    """Resolve idApi -> idOrgao for a single casa, creating a placeholder row when the órgão isn't known yet."""

    def __init__(self, db, cursor, casa, logger=None):
        self.db = db
        self.cursor = cursor
        self.casa = casa
        self.logger = logger
        self.cache = {}

        self.cursor.execute("SELECT idOrgao, idApi FROM orgao WHERE casa = %s", (casa,))
        for id_orgao, id_api in self.cursor.fetchall():
            self.cache[str(id_api)] = id_orgao

    def garantir(self, id_api_orgao, sigla=None, nome=None):
        if not id_api_orgao:
            return None
        id_api_str = str(id_api_orgao)
        if id_api_str in self.cache:
            return self.cache[id_api_str]

        self.cursor.execute(
            "SELECT idOrgao FROM orgao WHERE idApi = %s AND casa = %s",
            (id_api_str, self.casa),
        )
        res = self.cursor.fetchone()
        if res:
            self.cache[id_api_str] = res[0]
            return res[0]

        self.cursor.execute(
            "INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
            (id_api_str, sigla or "N/A", nome or f"Órgão não mapeado ({sigla or id_api_str})", self.casa),
        )
        self.db.commit()
        id_novo = self.cursor.lastrowid
        self.cache[id_api_str] = id_novo
        if self.logger:
            self.logger.info(f"Novo órgão criado: {sigla} (Casa: {self.casa}, ID API: {id_api_orgao})")
        return id_novo
