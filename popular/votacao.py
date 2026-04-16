import requests
import mysql.connector
import time
import sys
import os
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

try:
    db = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "votoVivo"),
        autocommit=False
    )
    cursor = db.cursor()
    print("Conexão estabelecida.")
except mysql.connector.Error as err:
    print(f"Erro de conexão: {err}")
    sys.exit(1)

cursor.execute("SELECT idApi, idProposicao FROM proposicao")
map_proposicoes = {str(row[0]): row[1] for row in cursor.fetchall()}

cursor.execute("SELECT idApi, idProposicao FROM votacao")
votacoes_existentes = {str(row[0]): row[1] for row in cursor.fetchall()}

meses = [
    ("2025-07-01", "2025-07-31"),
    ("2025-08-01", "2025-08-31"),
    ("2025-09-01", "2025-09-30"),
]

url = "https://dadosabertos.camara.leg.br/api/v2/votacoes"
lista_ids_votacoes = []

for data_inicio, data_fim in meses:
    pagina = 1
    
    while True:
        params = {
            "dataInicio": data_inicio,
            "dataFim": data_fim,
            "itens": 100,
            "pagina": pagina
        }
        
        res = requests.get(url, params=params, timeout=60)
        
        if res.status_code != 200:
            break
        
        votacoes = res.json().get("dados", [])
        
        if not votacoes:
            break
        
        for v in votacoes:
            if v.get('id'):
                lista_ids_votacoes.append(v.get('id'))
        
        if len(votacoes) < 100:
            break
        
        pagina += 1
        time.sleep(0.2)

lista_ids_votacoes = list(set(lista_ids_votacoes))

for id_api in tqdm(lista_ids_votacoes):

    try:
        res_det = requests.get(f"{url}/{id_api}", timeout=60)

        if res_det.status_code != 200:
            continue

        v = res_det.json().get("dados", {})

        id_prop_api = None

        for prop in v.get("proposicoesAfetadas", []):
            if prop.get("id"):
                id_prop_api = str(prop.get("id"))
                break

        if not id_prop_api:
            for obj in v.get("objetosPossiveis", []):
                if obj.get("id"):
                    id_prop_api = str(obj.get("id"))
                    break

        id_interno_proposicao = map_proposicoes.get(id_prop_api)

        id_existente = votacoes_existentes.get(str(id_api))

        if id_existente and id_existente == id_interno_proposicao:
            continue

        data_completa = v.get('dataHoraRegistro', '')
        data_votacao = data_completa.split('T')[0] if data_completa else v.get('data')

        aprovacao = v.get('aprovacao')
        if aprovacao == 1:
            resultado = "Aprovado"
        elif aprovacao == 0:
            resultado = "Rejeitado"
        else:
            resultado = v.get('resultado')

        resumo = v.get('descricao')

        tipo_votacao = 'SIMBOLICA'

        sql = """
            INSERT INTO votacao
            (idApi, idProposicao, dataVotacao, resumoMateria, resultadoFinal, tipoVotacao)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            idProposicao = VALUES(idProposicao),
            dataVotacao = VALUES(dataVotacao),
            resumoMateria = VALUES(resumoMateria),
            resultadoFinal = VALUES(resultadoFinal),
            tipoVotacao = VALUES(tipoVotacao)
        """

        cursor.execute(sql, (
            str(id_api),
            id_interno_proposicao,
            data_votacao,
            resumo,
            resultado,
            tipo_votacao
        ))

        votos_necessarios = True

        if id_existente:
            cursor.execute("SELECT COUNT(*) FROM voto WHERE idVotacao = %s", (id_existente,))
            if cursor.fetchone()[0] > 0:
                votos_necessarios = False

        if votos_necessarios:
            res_votos = requests.get(f"{url}/{id_api}/votos", timeout=30)

            if res_votos.status_code == 200:
                votos = res_votos.json().get("dados", [])
                
                if votos:
                    cursor.execute("SELECT idVotacao FROM votacao WHERE idApi = %s", (str(id_api),))
                    id_votacao_interno = cursor.fetchone()[0]

                    votos_batch = []

                    cursor.execute("SELECT idApi, idParlamentar FROM parlamentar")
                    map_parlamentares = {str(row[0]): row[1] for row in cursor.fetchall()}

                    for voto in votos:
                        id_dep_api = str(voto.get('deputado_', {}).get('id'))

                        if id_dep_api in map_parlamentares:
                            id_parlamentar = map_parlamentares[id_dep_api]

                            voto_txt = voto.get('tipoVoto')
                            if voto_txt == 'Sim':
                                voto_enum = 'SIM'
                            elif voto_txt == 'Não':
                                voto_enum = 'NAO'
                            elif voto_txt == 'Abstenção':
                                voto_enum = 'ABSTENCAO'
                            else:
                                continue

                            id_api_voto = f"{id_api}_{id_dep_api}"
                            votos_batch.append((id_parlamentar, id_votacao_interno, id_api_voto, voto_enum))

                    if votos_batch:
                        cursor.executemany("""
                            INSERT IGNORE INTO voto 
                            (idParlamentar, idVotacao, idApi, votoRegistrado)
                            VALUES (%s, %s, %s, %s)
                        """, votos_batch)

        db.commit()

    except:
        continue

cursor.close()
db.close()