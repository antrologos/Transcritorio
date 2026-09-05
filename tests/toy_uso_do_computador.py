"""Toy: quanto do computador o app usa — 2026-09-05. Puro, sem Qt.

O usuario pediu que a divisao do computador entre as etapas fosse uma ESCOLHA
VISIVEL, nao uma decisao escondida. Este teste fixa as duas garantias que
tornam a escolha segura:

1. o padrao ("tudo", sem sobreposicao) devolve o sentinela (0, 0) — o app nao
   define thread nenhuma, e o caminho de hoje fica identico POR CONSTRUCAO;
2. com sobreposicao o orcamento NUNCA e 0. Medido em 2026-09-05: dois motores
   com pool do tamanho da maquina disputando 4 nucleos custam 5,9x o de um pool
   do tamanho certo. Sobrepor sem orcamento seria pior que o sequencial.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_settings, capabilities as caps  # noqa: E402

# --- a preferencia normaliza qualquer lixo para o padrao ---------------------
assert app_settings.COMPUTER_USE_DEFAULT == "tudo"
assert set(app_settings.COMPUTER_USE_MODES) == {"tudo", "metade"}
print("PASS: modos e padrao")

# --- o padrao nao mexe em nada ----------------------------------------------
for n in (1, 2, 4, 8, 24):
    assert caps.cpu_budget("tudo", n) == (0, 0), f"{n} nucleos"
assert caps.cpu_budget("desconhecido", 4) == (0, 0), "valor invalido cai no padrao"
assert caps.thread_env(0) == {} and caps.thread_env(-1) == {}
print("PASS: modo padrao devolve o sentinela 'nao mexer'")

# --- "metade" deixa o computador utilizavel ---------------------------------
assert caps.cpu_budget("metade", 4) == (2, 2)
assert caps.cpu_budget("metade", 8) == (4, 4)
assert caps.cpu_budget("metade", 1) == (1, 1), "nunca zero threads"
assert caps.cpu_budget("metade", 0) == (1, 1), "contagem invalida nao explode"
print("PASS: metade da maquina")

# --- sobreposicao: soma cabe nos nucleos e nunca e o sentinela ---------------
for modo in ("tudo", "metade"):
    for n in (1, 2, 3, 4, 6, 8, 12, 16, 24):
        asr, diar = caps.cpu_budget(modo, n, concurrent=True)
        assert asr >= 1 and diar >= 1, f"{modo}/{n}: sobrepor sem orcamento custa 5,9x"
        assert asr + diar <= max(2, n), f"{modo}/{n}: {asr}+{diar} nao cabe em {n}"
# Na maquina alvo, a divisao medida como vencedora
assert caps.cpu_budget("tudo", 4, concurrent=True) == (2, 2)
assert caps.cpu_budget("metade", 4, concurrent=True) == (1, 1)
# Numero impar de nucleos: ninguem fica sem, e o resto vai para a separacao
assert caps.cpu_budget("tudo", 3, concurrent=True) == (1, 2)
print("PASS: orcamento da sobreposicao")

# --- as variaveis do filho ---------------------------------------------------
env = caps.thread_env(2)
assert env["OMP_NUM_THREADS"] == "2", env
assert set(env) == {"OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"}
assert all(isinstance(v, str) for v in env.values()), "os.environ so aceita str"
print("PASS: variaveis de ambiente do filho")

print("PASS: toy_uso_do_computador")
