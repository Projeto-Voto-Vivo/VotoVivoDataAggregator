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


cursor.execute("SELECT idDeputado FROM Deputado")
deputados_db = cursor.fetchall()

print(f" Total de deputados no banco: {len(deputados_db)}")
print(" Buscando redes sociais...\n")

contador_redes = 0
contador_deputados_com_redes = 0

for (id_deputado,) in deputados_db:
    
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados/{id_deputado}"
    
    try:
        response = requests.get(url)
        
        if response.status_code == 200:
            dados_dep = response.json()["dados"]
            redes_sociais = dados_dep.get('redeSocial', [])
            
            if redes_sociais:
                for link_rede in redes_sociais:
                    sql = """
                        INSERT IGNORE INTO RedeSocial
                        (idDeputado, linkRedeSocial)
                        VALUES (%s, %s)
                    """
                    
                    cursor.execute(sql, (id_deputado, link_rede))
                    contador_redes += 1
                
                db.commit()
                contador_deputados_com_redes += 1
                print(f" Deputado ID {id_deputado}: {len(redes_sociais)} rede(s) social(is)")
        else:
            print(f" Erro ao buscar deputado ID {id_deputado}: Status {response.status_code}")
        
        time.sleep(0.1)  
        
    except Exception as e:
        print(f" Erro ao processar deputado ID {id_deputado}: {e}")

print("\n" + "="*80)
print(f" Importação concluída!")
print(f" Deputados com redes sociais: {contador_deputados_com_redes}")
print(f" Total de redes sociais importadas: {contador_redes}")
print("="*80)

cursor.close()
db.close()