"""
Cria o banco de dados e executa todos os scripts de população em modo de
teste (TEST_MODE=True), gerando um dataset reduzido para desenvolvimento.

Uso:
    python popular/seed_teste.py                    # tempo padrão (60s/script)
    python popular/seed_teste.py --tempo 30         # 30s por script
    python popular/seed_teste.py --sem-schema       # pula criação do schema
    python popular/seed_teste.py --force            # recria schema sem perguntar
"""

import os
import sys
import time
import subprocess
import mysql.connector
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SCRIPTS_DIR    = Path(__file__).parent
SCHEMA_PATH    = SCRIPTS_DIR / "schema.sql"
TEMPO_PADRAO   = 60  # segundos por script

SCRIPTS_ORDEM = [
    "parlamentar.py",
    "partidos.py",
    "redeSocial.py",
    "gabinete.py",
    "tipoProposicao.py",
    "proposicao.py",
    "autoriaProposicao.py",
    "tema.py",
    "vincular_tema.py",
    "votacao.py",
    "voto.py",
    "despesas.py",
    "tipoTramitacao.py",
    "orgao.py",
    "tramitacao.py",
    "historico.py",
    "emenda.py",
    "relacionarEmendaParlamentar.py",
    "presenca.py",
]


# ── Schema ────────────────────────────────────────────────────────────────────

def criar_schema(force=False):
    DB_HOST     = os.getenv("DB_HOST", "localhost")
    DB_USER     = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME     = os.getenv("DB_NAME", "votoVivo")

    try:
        conn   = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD)
        cursor = conn.cursor()
    except mysql.connector.Error as err:
        print(f" [!] Falha na conexão: {err}")
        sys.exit(1)

    cursor.execute("SHOW DATABASES LIKE %s", (DB_NAME,))
    existe = cursor.fetchone()

    if existe:
        if not force:
            resp = input(f" [!] O banco '{DB_NAME}' já existe. Recriar? (s/N): ").strip()
            if resp.lower() != "s":
                cursor.close()
                conn.close()
                return False
        print(f" [-] Removendo banco '{DB_NAME}'...")
        cursor.execute(f"DROP DATABASE `{DB_NAME}`")

    print(f" [+] Criando banco '{DB_NAME}'...")
    cursor.execute(
        f"CREATE DATABASE `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.execute(f"USE `{DB_NAME}`")

    with open(SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()

    ok = 0
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        upper = stmt.upper().lstrip()
        if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
            continue
        try:
            cursor.execute(stmt)
            palavras = stmt.split()
            if len(palavras) >= 3 and palavras[0].upper() == "CREATE":
                print(f"     {palavras[2].strip('`(')}")
            ok += 1
        except mysql.connector.Error as err:
            print(f" [!] Erro: {err}")

    conn.commit()
    cursor.close()
    conn.close()
    print(f" [+] Schema aplicado ({ok} statements).")
    return True


# ── Execução dos scripts ──────────────────────────────────────────────────────

def executar_script_teste(nome, max_segundos):
    caminho = SCRIPTS_DIR / nome
    if not caminho.exists():
        print(f" [!] Script não encontrado: {nome}")
        return False

    env = os.environ.copy()
    env["TEST_MODE"]         = "True"
    env["MAX_TIME_SECONDS"]  = str(max_segundos)

    inicio = time.time()
    resultado = subprocess.run(
        [sys.executable, str(caminho)],
        env=env,
    )
    duracao = time.time() - inicio

    if resultado.returncode == 0:
        print(f" [+] {nome} concluído em {duracao:.1f}s")
        return True
    else:
        print(f" [!] {nome} falhou (código {resultado.returncode}) em {duracao:.1f}s")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    pular_schema = "--sem-schema" in sys.argv
    force        = "--force" in sys.argv

    tempo = TEMPO_PADRAO
    if "--tempo" in sys.argv:
        idx = sys.argv.index("--tempo")
        if idx + 1 < len(sys.argv):
            try:
                tempo = int(sys.argv[idx + 1])
            except ValueError:
                print(" [!] --tempo requer um número inteiro de segundos.")
                sys.exit(1)

    print("=" * 60)
    print(" SEED DE DADOS DE TESTE")
    print(f" TEST_MODE=True | MAX_TIME_SECONDS={tempo}s por script")
    print("=" * 60)

    if not pular_schema:
        print("\n── Criando schema " + "─" * 40)
        ok = criar_schema(force=force)
        if not ok:
            print(" [i] Operação cancelada.")
            sys.exit(0)

    print(f"\n── Executando {len(SCRIPTS_ORDEM)} scripts em modo teste " + "─" * 12)

    resultados = {}
    inicio_total = time.time()

    for i, script in enumerate(SCRIPTS_ORDEM, 1):
        print(f"\n[{i:02}/{len(SCRIPTS_ORDEM):02}] {script}")
        resultados[script] = executar_script_teste(script, tempo)

    duracao_total = time.time() - inicio_total

    sucesso = sum(1 for ok in resultados.values() if ok)
    falhas  = len(resultados) - sucesso

    print("\n" + "=" * 60)
    print(" RESUMO")
    print("=" * 60)
    for script, ok in resultados.items():
        simbolo = "✓" if ok else "✗"
        print(f"   {simbolo} {script}")

    print(f"\n {sucesso}/{len(SCRIPTS_ORDEM)} scripts concluídos "
          f"em {duracao_total:.1f}s.")

    if falhas:
        print(f" {falhas} falha(s). Verifique os logs acima.")
        sys.exit(1)


if __name__ == "__main__":
    main()
