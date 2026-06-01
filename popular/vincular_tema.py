import os
import requests
import mysql.connector
import time
import sys
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

is_test_mode = os.getenv("TEST_MODE", "False").lower() == "true"
tempo_limite_segundos = int(os.getenv("MAX_TIME_SECONDS", "0"))

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo")
    )
    cursor = db.cursor(buffered=True)
except mysql.connector.Error:
    sys.exit(1)

chk_camara = "popular/tema_proposicao.py#camara"
chk_senado = "popular/tema_proposicao.py#senado"

def obter_ultimo_checkpoint(nome_script, default_value="0"):
    query = "SELECT ultimoParametro FROM etlCheckpoint WHERE nomeScript = %s"
    cursor.execute(query, (nome_script,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else default_value

def salvar_checkpoint_transacao(nome_script, valor_parametro):
    query = """
        INSERT INTO etlCheckpoint (nomeScript, ultimoParametro) 
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE ultimoParametro = VALUES(ultimoParametro)
    """
    cursor.execute(query, (nome_script, str(valor_parametro)))

cursor.execute("SELECT codigoExterno, casa, idTema FROM tema")
mapa_temas = {(row[0], row[1]): row[2] for row in cursor.fetchall()}

def vincular_camara():
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Camara' AND p.idApi IS NOT NULL
        ORDER BY p.idProposicao ASC
    """)
    props = cursor.fetchall()

    checkpoint_atual = int(obter_ultimo_checkpoint(chk_camara, default_value="0"))
    fila_props = [p for p in props if p[0] > checkpoint_atual]

    if is_test_mode:
        fila_props = fila_props[:5]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_props, desc="Temas Câmara"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id_api}/temas"
            res = requests.get(url, timeout=30).json().get("dados", [])
            
            for t in res:
                id_tema = mapa_temas.get((str(t['codTema']), 'Camara'))
                if id_tema:
                    cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))
            
            salvar_checkpoint_transacao(chk_camara, id_interno)
            db.commit()
            time.sleep(0.1)
        except Exception:
            db.rollback()
            continue

def vincular_senado():
    cursor.execute("""
        SELECT p.idProposicao, p.idApi FROM proposicao p
        JOIN tipoProposicao t ON p.idTipoProposicao = t.idTipoProposicao
        WHERE t.casa = 'Senado' AND p.idApi IS NOT NULL
        ORDER BY p.idProposicao ASC
    """)
    props = cursor.fetchall()

    checkpoint_atual = int(obter_ultimo_checkpoint(chk_senado, default_value="0"))
    fila_props = [p for p in props if p[0] > checkpoint_atual]

    if is_test_mode:
        fila_props = fila_props[:5]

    start_time = time.time()

    for id_interno, id_api in tqdm(fila_props, desc="Temas Senado"):
        if tempo_limite_segundos > 0 and (time.time() - start_time) > tempo_limite_segundos:
            break

        try:
            url = f"https://legis.senado.leg.br/dadosabertos/processo/{id_api}?v=1"
            res = requests.get(url, headers={"Accept": "application/json"}, timeout=30).json()

            processo = res.get("Processo", {}) if "Processo" in res else res
            classificacoes = processo.get("classificacoes", []) or []
            
            if isinstance(classificacoes, dict):
                classificacoes = [classificacoes]

            for c in classificacoes:
                codigo = c.get("codigo")
                if not codigo:
                    continue

                codigo_str = str(codigo)
                id_tema = mapa_temas.get((codigo_str, 'Senado'))
                
                if not id_tema:
                    cursor.execute("""
                        INSERT IGNORE INTO tema (codigoExterno, casa, descricao, nivel)
                        VALUES (%s, 'Senado', %s, 'UNICO')
                    """, (codigo_str, c.get("descricao", "")))
                    
                    cursor.execute("SELECT idTema FROM tema WHERE codigoExterno=%s AND casa='Senado'", (codigo_str,))
                    row = cursor.fetchone()
                    if row:
                        id_tema = row[0]
                        mapa_temas[(codigo_str, 'Senado')] = id_tema

                if id_tema:
                    cursor.execute("INSERT IGNORE INTO temaProposicao VALUES (%s, %s)", (id_interno, id_tema))

            salvar_checkpoint_transacao(chk_senado, id_interno)
            db.commit()
            time.sleep(0.1)
        except Exception:
            db.rollback()
            continue

if __name__ == "__main__":
    try:
        vincular_camara()
        vincular_senado()
    except KeyboardInterrupt:
        pass
    finally:
        cursor.close()
        db.close()