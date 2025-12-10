import requests
import mysql.connector
import time

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="06131418",
    database="votovivo"
)

cursor = db.cursor()

print("=" * 80)
print(" IMPORTAÇÃO DE GABINETES")
print("=" * 80)


cursor.execute("SELECT idDeputado FROM Deputado")
deputados_db = cursor.fetchall()

print(f"\n Total de deputados no banco: {len(deputados_db)}")
print(" Buscando gabinetes...\n")

total_gabinetes = 0
gabinetes_unicos = set()
deputados_processados = 0



print("  Importando gabinetes atuais dos deputados\n")

for (id_deputado,) in deputados_db:
    try:
        
        url_deputado = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_deputado}"
        response = requests.get(url_deputado)
        
        if response.status_code == 200:
            dados = response.json()["dados"]
            
           
            ultimo_status = dados.get('ultimoStatus', {})
            gabinete = ultimo_status.get('gabinete', {})
            
            nome_gabinete = gabinete.get('nome')
            sala = gabinete.get('sala')
            predio = gabinete.get('predio')
            andar = gabinete.get('andar')
            
           
            if nome_gabinete or sala:
                chave_gabinete = f"{predio or 'N/A'}_{andar or 'N/A'}_{sala or 'N/A'}_{nome_gabinete or 'N/A'}"
                
                if chave_gabinete not in gabinetes_unicos:
                    gabinetes_unicos.add(chave_gabinete)
                    
                    sql = """
                        INSERT IGNORE INTO Gabinete
                        (andar, emailGabinete, nomeGabinete, predio, sala, telefone)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    
                    valores = (
                        andar,
                        gabinete.get('email'),
                        nome_gabinete,
                        predio,
                        sala,
                        gabinete.get('telefone')
                    )
                    
                    cursor.execute(sql, valores)
                    db.commit()
                    total_gabinetes += 1
                    
                   
                    info_gabinete = f"Prédio {predio or '?'} - Andar {andar or '?'} - Sala {sala or '?'}"
                    print(f" [{total_gabinetes:3d}] {info_gabinete}")
        
        deputados_processados += 1
        
        if deputados_processados % 100 == 0:
            print(f"    Progresso: {deputados_processados}/{len(deputados_db)} deputados processados")
        
        time.sleep(0.05)
        
    except Exception as e:
        print(f" Erro ao processar deputado {id_deputado}: {e}")

print(f"\nConcluído: {total_gabinetes} gabinetes atuais importados")


print("\n" + "=" * 80)
print(" RESUMO FINAL")
print("=" * 80)
print(f" Total de gabinetes únicos importados: {len(gabinetes_unicos)}")
print("=" * 80)

cursor.close()
db.close()