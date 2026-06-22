import os
import subprocess
import sys
import time

from utils.db import get_connection
from utils.checkpoint_manager import CheckpointManager

PIPELINE_SCRIPTS = [
    "camara/parlamentar_camara.py",
    "senado/parlamentar_senado.py",
    "tipoProposicao.py",
    "camara/tema_camara.py",
    "senado/tema_senado.py",
    "camara/orgao_camara.py",
    "senado/orgao_senado.py",
    "camara/proposicao_camara.py",
    "senado/proposicao_senado.py",
    "camara/despesas_camara.py",
    "senado/despesas_senado.py",
    "emenda.py",
    "tipoTramitacao.py",
    "camara/tramitacao_camara.py",
    "senado/tramitacao_senado.py",
    "camara/evento_camara.py",
    "senado/votacao_presenca_senado.py",
    "camara/votacao_camara.py",
    "voto.py",
    "relacionarEmendaParlamentar.py",
]

IDENTIFICADOR_ORQUESTRADOR = "popular/principal.py#pipeline"

try:
    db, cursor = get_connection()
except Exception as err:
    print(f"[!] Erro ao conectar ao banco no orquestrador: {err}")
    sys.exit(1)

chk_manager = CheckpointManager(db)


def obter_ultimo_script_concluido():
    return chk_manager.obter(IDENTIFICADOR_ORQUESTRADOR, default_value=None)


def salvar_checkpoint_pipeline(script_name):
    chk_manager.salvar(IDENTIFICADOR_ORQUESTRADOR, script_name)


def executar_script(script_name):
    print(f"\n==================================================")
    print(f"🚀 Iniciando: {script_name}")
    print(f"==================================================")

    start_time = time.time()

    diretorio_atual = os.path.dirname(os.path.abspath(__file__))
    caminho_absoluto = os.path.join(diretorio_atual, script_name)

    resultado = subprocess.run(
        [sys.executable, caminho_absoluto], capture_output=False
    )

    duration = time.time() - start_time

    if resultado.returncode == 0:
        print(f"✅ {script_name} concluído com sucesso! (Tempo: {duration:.2f}s)")
        return True
    else:
        print(f"❌ ERRO catastrófico ao executar {script_name} (Código de saída: {resultado.returncode})")
        return False


def main():
    print("🌟 INICIANDO CARGA COMPLETA DO BANCO VOTO VIVO (VIA POPULAR) 🌟")
    
    ultimo_concluido = obter_ultimo_script_concluido()
    
    if ultimo_concluido and ultimo_concluido in PIPELINE_SCRIPTS:
        indice_proximo = PIPELINE_SCRIPTS.index(ultimo_concluido) + 1
        fila_execucao = PIPELINE_SCRIPTS[indice_proximo:]
        print(f"[i] Checkpoint detectado! Último script que rodou 100%: {ultimo_concluido}")
        print(f"[i] Retomando pipeline a partir do próximo da fila.")
    else:
        fila_execucao = PIPELINE_SCRIPTS
        print("[i] Nenhum checkpoint de pipeline encontrado. Iniciando do zero absoluto.")

    print(f"Total de scripts a serem processados nesta rodada: {len(fila_execucao)}")
    pipeline_start = time.time()

    for script in fila_execucao:
        sucesso = executar_script(script)

        if not sucesso:
            print("\n🛑 PIPELINE INTERROMPIDO devido a uma falha crítica em um dos módulos.")
            cursor.close()
            db.close()
            sys.exit(1)
            
        salvar_checkpoint_pipeline(script)

    # Se o pipeline chegar ao fim com sucesso, limpa o checkpoint para uma futura nova carga geral
    cursor.execute("DELETE FROM etlCheckpoint WHERE nomeScript = %s", (IDENTIFICADOR_ORQUESTRADOR,))
    db.commit()

    total_duration = time.time() - pipeline_start
    print("\n==================================================")
    print(f"🎉 PARABÉNS! Todo o banco de dados foi populado!")
    print(f"⏱️ Tempo total de execução desta rodada: {total_duration/60:.2f} minutos.")
    print("==================================================")

    cursor.close()
    db.close()


if __name__ == "__main__":
    main()