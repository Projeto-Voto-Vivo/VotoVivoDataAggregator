"""
Atualização semanal (cron: domingo 03:00) — despesas, proposições, emendas e votos.

Diferente do principal.py (carga completa, retomável), aqui cada script roda
sempre: todos já sabem fazer o refresh incremental a partir do próprio
checkpoint (ano/mês corrente, ou ids novos). Uma falha não interrompe a rodada —
os scripts seguintes trabalham sobre o que já está no banco — mas o processo
sai com código 1 para o cron/log sinalizar.

    python popular/atualizacao_semanal.py
"""

import os
import subprocess
import sys
import time

from utils.cache_cdn import purgar_cdn

# Ordem respeita as dependências do PIPELINE_SCRIPTS do principal.py
SCRIPTS_SEMANAIS = [
    # Proposições (inclui a ementa)
    "camara/proposicao_camara.py",
    "senado/proposicao_senado.py",
    "relacionarProposicaoCasas.py",
    # Despesas
    "camara/despesas_camara.py",
    "senado/despesas_senado.py",
    # Emendas
    "emenda.py",
    # Votações e votos (dependem das proposições)
    "senado/votacao_presenca_senado.py",
    "camara/votacao_camara.py",
    "camara/orientacao_camara.py",
    "voto.py",
    "relacionarEmendaParlamentar.py",
]


def executar_script(script_name):
    print(f"\n==================================================")
    print(f"🚀 Iniciando: {script_name}")
    print(f"==================================================", flush=True)

    start_time = time.time()
    caminho_absoluto = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name)
    resultado = subprocess.run([sys.executable, caminho_absoluto])
    duration = time.time() - start_time

    if resultado.returncode == 0:
        print(f"✅ {script_name} concluído com sucesso! (Tempo: {duration:.2f}s)", flush=True)
        return True
    print(f"❌ {script_name} falhou (Código de saída: {resultado.returncode}, Tempo: {duration:.2f}s)", flush=True)
    return False


def main():
    print(f"🗓️ ATUALIZAÇÃO SEMANAL — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    inicio = time.time()
    falhas = []

    try:
        for script in SCRIPTS_SEMANAIS:
            if not executar_script(script):
                falhas.append(script)
    finally:
        purgar_cdn()

    print("\n==================================================")
    print(f"⏱️ Tempo total: {(time.time() - inicio) / 60:.2f} minutos.")
    if falhas:
        print(f"🛑 {len(falhas)} script(s) com falha: {', '.join(falhas)}")
        print("==================================================")
        sys.exit(1)
    print("🎉 Atualização semanal concluída sem falhas.")
    print("==================================================")


if __name__ == "__main__":
    main()
