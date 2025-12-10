import mysql.connector
import sys 

class ServicoDeputado:
    """
    Classe de serviço para executar consultas relacionadas a Deputados.
    """
    def __init__(self, host="localhost", user="root", password="06131418", database="votovivo"):
        
        try:
            self.conn = mysql.connector.connect(
                host=host,
                user=user,
                password=password,
                database=database
            )
            self.cursor = self.conn.cursor(dictionary=True, buffered=True) 
            print("✅ Conexão MySQL estabelecida no ServicoDeputado.")
        except mysql.connector.Error as err:
            print(f"❌ Erro ao conectar ao MySQL: {err}")
            self.conn = None
            self.cursor = None
            sys.exit(1)

    def _executar_select(self, sql, parametros=()):
        """
        Método auxiliar para executar SELECTs e retornar os resultados.
        O SQL de entrada DEVE usar '%s' como placeholder.
        """
        if not self.conn:
            return []
            
        try:
            self.cursor.execute(sql, parametros) 
            return self.cursor.fetchall() 
        except Exception as e:
            print(f"Erro ao executar consulta: {e}")
            return []

    def fechar_conexao(self):
        """
        Fecha a conexão com o banco de dados, se estiver aberta.
        """
        if self.conn and self.conn.is_connected():
            self.cursor.close()
            self.conn.close()
            print("Conexão MySQL fechada.")

    def obter_detalhes_basicos(self, id_deputado):
        """
        Retorna detalhes biográficos, gabinete e partido mais recente do deputado.
        """
        sql = """
            SELECT
                D.idDeputado, D.nomeCivil, D.cpf, D.sexo, D.dataNascimento,
                D.ufNascimento, D.municipioNascimento, D.dataFalecimento,
                D.escolaridade, D.urlDetalhes, D.urlWebsite,

                H.nomeParlamentar, H.situacao, H.siglaUF, H.urlFoto, H.emailHistorico,

                P.siglaPartido, P.nome AS nomePartido,

                G.andar, G.emailGabinete AS emailGabineteAtual, G.nomeGabinete, G.predio, G.sala, G.telefone
            FROM
                Deputado D
            INNER JOIN
                Historico H ON H.idHistorico = (
                    SELECT idHistorico
                    FROM Historico
                    WHERE idDeputado = D.idDeputado
                    ORDER BY dataHistorico DESC, idHistorico DESC
                    LIMIT 1
                )
            INNER JOIN Partido P ON H.idPartido = P.idPartido
            INNER JOIN Gabinete G ON H.idGabinete = G.idGabinete
            WHERE
                D.idDeputado = %s;
        """
        resultado = self._executar_select(sql, (id_deputado,))
        return resultado[0] if resultado else None


    def obter_historico_completo(self, id_deputado):
        """
        Retorna o histórico completo de mandatos, partidos e gabinetes.
        """
        sql = """
            SELECT
                H.dataHistorico, H.situacao, H.descricaoStatus,
                P.siglaPartido, P.nome AS nomePartido,
                G.nomeGabinete, G.telefone
            FROM
                Historico H
            INNER JOIN Partido P ON H.idPartido = P.idPartido
            INNER JOIN Gabinete G ON H.idGabinete = G.idGabinete
            WHERE
                H.idDeputado = %s
            ORDER BY
                H.dataHistorico DESC, H.idHistorico DESC;
        """
        return self._executar_select(sql, (id_deputado,))

    def obter_redes_sociais(self, id_deputado):
        """
        Retorna todos os links de redes sociais do deputado.
        """
        sql = """
            SELECT
                linkRedeSocial
            FROM
                RedeSocial
            WHERE
                idDeputado = %s;
        """
        return self._executar_select(sql, (id_deputado,))


    def obter_despesas_por_periodo(self, id_deputado, ano, mes):
        """
        Retorna as despesas detalhadas filtradas por ano e mês.
        """
        sql = """
            SELECT
                codDocumento, dataDocumento, nomeFornecedor,
                tipoDespesa, valorDocumento, valorLiquido,
                urlDocumento, cnpjCpfFornecedor
            FROM
                Despesa
            WHERE
                idDeputado = %s
                AND ano = %s
                AND mes = %s
            ORDER BY
                dataDocumento DESC;
        """
        return self._executar_select(sql, (id_deputado, ano, mes))

# =================================================================
## BLOCO DE EXECUÇÃO DE TESTE (CORRIGIDO)
# =================================================================
if __name__ == "__main__":
    
    # ID de teste que você forneceu:
    ID_TESTE = 62881 
    
    servico = ServicoDeputado()

    if servico.conn is not None and servico.conn.is_connected():
        try:
            print("\n" + "="*50)
            print(f"EXECUTANDO CONSULTAS PARA ID: {ID_TESTE}")
            print("="*50)

            
            detalhes = servico.obter_detalhes_basicos(ID_TESTE)
            print("\n--- 1. DETALHES BÁSICOS E ATUAIS ---")
            if detalhes:
                # CORREÇÃO 1: Removendo o teste de 'detalhes.get('nomeParlamentar')'
                print(f"Nome Parlamentar: {detalhes.get('nomeParlamentar')}") 
                print(f"Partido Atual: {detalhes.get('siglaPartido')}")
                print(f"Telefone Gabinete: {detalhes.get('telefone')}")
            else:
                print("Detalhes não encontrados. Verifique se o ID existe na tabela Deputado/Historico.")

            
            historico = servico.obter_historico_completo(ID_TESTE)
            print("\n--- 2A. HISTÓRICO DE SITUAÇÃO ---")
            for i, reg in enumerate(historico[:5]): 
                print(f"  {i+1}. {reg.get('dataHistorico')} - {reg.get('situacao')} ({reg.get('siglaPartido')})")

            
            redes = servico.obter_redes_sociais(ID_TESTE)
            print("\n--- 2B. REDES SOCIAIS ---")
            if redes:
                # A função obter_redes_sociais retorna uma lista de dicionários
                for rede in redes:
                    print(f"  - {rede.get('linkRedeSocial')}")
            else:
                print("Nenhuma rede social encontrada.")

          
            ano_teste = 2025 
            mes_teste = 11
            despesas = servico.obter_despesas_por_periodo(ID_TESTE, ano_teste, mes_teste)
            print(f"\n--- 3. DESPESAS ({mes_teste}/{ano_teste}) ---")
            if despesas:
                print(f"Total de Despesas encontradas: {len(despesas)}")
                
                # CORREÇÃO 2: Exibindo até 5 despesas (ou menos, se houver)
                print("\nÚltimas Despesas:")
                for i, despesa in enumerate(despesas[:]):
                    print(f"  {i+1}. Tipo: {despesa.get('tipoDespesa')}, Valor Líquido: R$ {despesa.get('valorLiquido')}, Fornecedor: {despesa.get('nomeFornecedor')}")
            else:
                print(f"Nenhuma despesa encontrada para {mes_teste}/{ano_teste}.")

        finally:
            servico.fechar_conexao()
            print("Conexão MySQL fechada.")
    else:
        print("Não foi possível executar as consultas. Verifique as credenciais do MySQL.")