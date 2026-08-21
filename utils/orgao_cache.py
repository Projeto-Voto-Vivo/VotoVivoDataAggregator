import mysql.connector

from utils.db import get_connection


def _limitar(valor, tamanho):
    if valor is None:
        return None
    valor = str(valor)
    return valor[:tamanho] if len(valor) > tamanho else valor


class OrgaoCache:
    """Resolve idApi -> idOrgao de uma casa, criando um placeholder quando o
    órgão ainda não é conhecido.

    Decisões deliberadas:
    - As escritas em `orgao` usam uma CONEXÃO PRÓPRIA e são commitadas na hora.
      Órgão é entidade de referência, independente da transação de tramitação/
      votação do chamador: um rollback do chamador não pode desfazer um órgão
      já criado, e o commit daqui não pode commitar a transação de dados dele.
    - Placeholder não vence dado bom: quem chegar depois com sigla/nome reais
      atualiza a linha "N/A / Órgão não mapeado" — uma única vez por campo.
    - A melhoria é best-effort: se a linha estiver travada (lock de FK de um
      INSERT do chamador ainda não commitado), não se espera 50 s — desiste-se
      desta execução e tenta-se na próxima. Chamadores que abrem transações
      longas devem resolver os órgãos ANTES de começar a inserir.

    `db` e `cursor` são aceitos por compatibilidade de assinatura, mas não são
    usados para escrita.
    """

    LOCK_WAIT_SEGUNDOS = 3

    def __init__(self, db, cursor, casa, logger=None):
        self.casa = casa
        self.logger = logger
        self.cache = {}
        self.placeholders = {}   # idOrgao -> {"sigla", "nome"} (campos ainda por preencher)

        self.db, self.cursor = get_connection(buffered=True)
        try:
            self.cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {self.LOCK_WAIT_SEGUNDOS}")
        except mysql.connector.Error:
            pass

        self.cursor.execute(
            "SELECT idOrgao, idApi, sigla, nome FROM orgao WHERE casa = %s", (casa,)
        )
        for id_orgao, id_api, sigla, nome in self.cursor.fetchall():
            self.cache[str(id_api)] = id_orgao
            self._registrar_faltantes(id_orgao, sigla, nome)

    def _registrar_faltantes(self, id_orgao, sigla, nome):
        faltantes = set()
        if not sigla or sigla == "N/A":
            faltantes.add("sigla")
        if not nome or nome.startswith("Órgão não mapeado"):
            faltantes.add("nome")
        if faltantes:
            self.placeholders[id_orgao] = faltantes

    def _melhorar(self, id_orgao, sigla, nome):
        faltantes = self.placeholders.get(id_orgao)
        if not faltantes:
            return
        novos = {}
        if sigla and "sigla" in faltantes:
            novos["sigla"] = _limitar(sigla, 50)
        if nome and "nome" in faltantes:
            novos["nome"] = _limitar(nome, 1000)
        if not novos:
            return
        try:
            sets = ", ".join(f"{coluna} = %s" for coluna in novos)
            self.cursor.execute(f"UPDATE orgao SET {sets} WHERE idOrgao = %s", (*novos.values(), id_orgao))
            self.db.commit()
            faltantes -= set(novos)
            if not faltantes:
                del self.placeholders[id_orgao]
        except mysql.connector.Error as e:
            self.db.rollback()
            if e.errno == 1205:   # lock wait timeout: linha presa pela transação do chamador
                self.placeholders.pop(id_orgao, None)   # não insistir nesta execução
                if self.logger:
                    self.logger.debug(f"Órgão {id_orgao} travado por outra transação; melhoria adiada.")
            else:
                raise

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
                self._registrar_faltantes(id_orgao, res[1], res[2])

        if id_orgao is not None:
            self._melhorar(id_orgao, sigla, nome)
            return id_orgao

        sigla_final = _limitar(sigla, 50) or "N/A"
        nome_final = _limitar(nome, 1000) or f"Órgão não mapeado ({sigla or id_api_str})"
        self.cursor.execute(
            "INSERT INTO orgao (idApi, sigla, nome, casa) VALUES (%s, %s, %s, %s)",
            (id_api_str, sigla_final, nome_final, self.casa),
        )
        self.db.commit()
        id_novo = self.cursor.lastrowid
        self.cache[id_api_str] = id_novo
        self._registrar_faltantes(id_novo, sigla_final, nome_final)
        if self.logger:
            self.logger.info(f"Novo órgão criado: {sigla or id_api_str} (Casa: {self.casa}, ID API: {id_api_orgao})")
        return id_novo

    def fechar(self):
        try:
            self.cursor.close()
            self.db.close()
        except Exception:
            pass
