import requests
import mysql.connector
import time

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="",
    database="votovivo"
)

cursor = db.cursor()

print("=" * 80)
print(" IMPORTAÇÃO DE HISTÓRICO DOS DEPUTADOS")
print("=" * 80)


cursor.execute("SELECT idDeputado FROM Deputado")
deputados_db = cursor.fetchall()

print(f"\n Total de deputados no banco: {len(deputados_db)}")
print(" Iniciando importação de histórico...\n")

total_historicos = 0
deputados_processados = 0
deputados_com_historico = 0
partidos_nao_encontrados = set()
gabinetes_criados = 0

for (id_deputado,) in deputados_db:
    historicos_deputado = 0
    
    try:
        
        url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_deputado}"
        response = requests.get(url)
        
        if response.status_code == 200:
            dados = response.json()["dados"]
            ultimo_status = dados.get('ultimoStatus')
            
            if ultimo_status:
                
                sigla_partido = ultimo_status.get('siglaPartido')
                id_partido = None
                
                if sigla_partido:
                    cursor.execute(
                        "SELECT idPartido FROM Partido WHERE siglaPartido = %s", 
                        (sigla_partido,)
                    )
                    resultado_partido = cursor.fetchone()
                    
                    if resultado_partido:
                        id_partido = resultado_partido[0]
                    else:
                        partidos_nao_encontrados.add(sigla_partido)
                        
                        cursor.execute("""
                            INSERT IGNORE INTO Partido (siglaPartido, nome)
                            VALUES (%s, %s)
                        """, (sigla_partido, sigla_partido))
                        db.commit()
                        
                        cursor.execute(
                            "SELECT idPartido FROM Partido WHERE siglaPartido = %s", 
                            (sigla_partido,)
                        )
                        resultado_partido = cursor.fetchone()
                        if resultado_partido:
                            id_partido = resultado_partido[0]
                
               
                gabinete = ultimo_status.get('gabinete', {})
                nome_gabinete = gabinete.get('nome')
                sala_gabinete = gabinete.get('sala')
                predio_gabinete = gabinete.get('predio')
                andar_gabinete = gabinete.get('andar')
                id_gabinete = None
                
                if nome_gabinete or sala_gabinete:
                    
                    cursor.execute("""
                        SELECT idGabinete FROM Gabinete 
                        WHERE (nomeGabinete <=> %s) 
                        AND (sala <=> %s)
                        AND (predio <=> %s)
                        AND (andar <=> %s)
                        LIMIT 1
                    """, (nome_gabinete, sala_gabinete, predio_gabinete, andar_gabinete))
                    
                    resultado_gabinete = cursor.fetchone()
                    
                    if resultado_gabinete:
                        id_gabinete = resultado_gabinete[0]
                    else:
                        
                        cursor.execute("""
                            INSERT INTO Gabinete 
                            (andar, emailGabinete, nomeGabinete, predio, sala, telefone)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (
                            andar_gabinete,
                            gabinete.get('email'),
                            nome_gabinete,
                            predio_gabinete,
                            sala_gabinete,
                            gabinete.get('telefone')
                        ))
                        db.commit()
                        id_gabinete = cursor.lastrowid
                        gabinetes_criados += 1
                
               
                if id_partido and id_gabinete:
                    sql_historico = """
                        INSERT IGNORE INTO Historico
                        (idDeputado, idPartido, idGabinete, uriHistorico, condicaoEleitoral,
                         urlFoto, emailHistorico, dataHistorico, descricaoStatus, nomeParlamentar,
                         situacao, nomeEleitoral, siglaUF)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """
                    
                    valores_historico = (
                        id_deputado,
                        id_partido,
                        id_gabinete,
                        ultimo_status.get('uri'),
                        ultimo_status.get('condicaoEleitoral'),
                        ultimo_status.get('urlFoto'),
                        gabinete.get('email'), 
                        ultimo_status.get('data'),
                        ultimo_status.get('descricaoStatus'),
                        ultimo_status.get('nome'), 
                        ultimo_status.get('situacao'),
                        ultimo_status.get('nomeEleitoral'),
                        ultimo_status.get('siglaUf')
                    )
                    
                    cursor.execute(sql_historico, valores_historico)
                    db.commit()
                    total_historicos += 1
                    historicos_deputado += 1
                
                if historicos_deputado > 0:
                    deputados_com_historico += 1
        else:
            print(f" Erro ao buscar deputado {id_deputado}: Status {response.status_code}")
        
        deputados_processados += 1
        
        if deputados_processados % 50 == 0:
            print(f"    Progresso: {deputados_processados}/{len(deputados_db)} deputados processados")
        
        time.sleep(0.1)  
        
    except Exception as e:
        print(f" Erro ao processar deputado {id_deputado}: {e}")

print("\n" + "=" * 80)
print(" RESUMO FINAL")
print("=" * 80)
print(f" Deputados processados: {deputados_processados}")
print(f" Deputados com histórico: {deputados_com_historico}")
print(f" Total de registros históricos importados: {total_historicos}")
if gabinetes_criados > 0:
    print(f" Gabinetes criados automaticamente: {gabinetes_criados}")
if partidos_nao_encontrados:
    print(f"  Partidos criados automaticamente: {', '.join(partidos_nao_encontrados)}")
print("=" * 80)

cursor.close()
db.close()