"""Toy: o modelo do TAGARELA carregado uma vez por LOTE — 2026-09-05.

Medido em 2026-09-05 (tests/benchmarks/cpu_4nucleos/resultado_2026-09-05.md):
carregar o modelo custa 3,4 a 5,2 s e 2,18 GB de RSS. E `run_parakeet` guardava
o modelo numa variavel LOCAL, enquanto a GUI chama `transcribe_interviews` uma
vez POR ARQUIVO (app_service.py: `for interview_id in selected_ids(...)`) — ou
seja, os 2,4 GB de pesos eram relidos a cada entrevista. O comentario que
estava na linha do `model = None` dizia "uma vez por lote"; nao era verdade.

O precedente estava no mesmo arquivo: `_GPU_FAILED_THIS_SESSION` ja e flag de
MODULO exatamente por isto, e o comentario dela explica o motivo.

Puro: usa um `onnx_asr` FALSO injetado em sys.modules, entao roda no CI minimo
sem os 2,4 GB de pesos e sem onnxruntime.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --- onnx_asr falso, ANTES de importar o runner (ele importa dentro da funcao,
# --- mas o toy chama _load_cpu_model direto) ---------------------------------
cargas: list[str] = []


class _ModeloFalso:
    def __init__(self, pasta: str) -> None:
        self.pasta = pasta

    def with_timestamps(self):
        return self


def _load_model_falso(_nome, pasta, **_kwargs):
    cargas.append(str(pasta))
    return _ModeloFalso(str(pasta))


falso = types.ModuleType("onnx_asr")
falso.load_model = _load_model_falso          # type: ignore[attr-defined]
sys.modules["onnx_asr"] = falso

from transcribe_pipeline import parakeet_runner as pk  # noqa: E402

import tempfile  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="toy_cache_"))
snap_a = tmp / "snap_a"
snap_b = tmp / "snap_b"
for pasta in (snap_a, snap_b):
    pasta.mkdir()
    (pasta / "config.json").write_text("{}", encoding="utf-8")

# --- a chave identifica o modelo carregado -----------------------------------
chave = pk._model_cache_key(snap_a)
assert pk._model_cache_key(snap_a) == chave, "mesma pasta, mesma chave"
assert pk._model_cache_key(snap_b) != chave, "pasta diferente = modelo diferente"
# Um download que sobrescreve o snapshot muda o mtime e tem de invalidar.
import os  # noqa: E402

os.utime(snap_a, (0, 0))
assert pk._model_cache_key(snap_a) != chave, "mtime diferente tem de invalidar"
# Pasta que sumiu nao pode explodir na hora de montar a chave.
pk._model_cache_key(tmp / "nao_existe")
print("PASS: chave do cache")

# --- carrega UMA vez para N arquivos ----------------------------------------
pk.release_cpu_model()
cargas.clear()
modelos = [pk._load_cpu_model(snap_a) for _ in range(5)]
assert len(cargas) == 1, f"5 arquivos deveriam custar 1 carga, custaram {len(cargas)}"
assert all(m is modelos[0] for m in modelos), "tem de ser o MESMO objeto"
print("PASS: uma carga para cinco arquivos")

# --- trocar de modelo invalida ----------------------------------------------
outro = pk._load_cpu_model(snap_b)
assert len(cargas) == 2 and outro is not modelos[0]
de_volta = pk._load_cpu_model(snap_a)
assert len(cargas) == 3, "voltar ao primeiro recarrega (guardamos so um)"
print("PASS: trocar de modelo invalida")

# --- soltar o modelo ao fim do lote ------------------------------------------
pk.release_cpu_model()
assert pk._CPU_MODEL is None and pk._CPU_MODEL_KEY is None
pk.release_cpu_model()          # idempotente: on_worker_done, failed e closeEvent
assert pk._CPU_MODEL is None
cargas.clear()
pk._load_cpu_model(snap_a)
assert len(cargas) == 1, "depois de soltar, recarrega"
pk.release_cpu_model()
print("PASS: release_cpu_model solta e e idempotente")

# --- a GUI precisa saber que a funcao existe ---------------------------------
# (o gate toy_metodos_existem nasceu de um metodo renomeado sem propagar)
assert callable(getattr(pk, "release_cpu_model", None))
print("PASS: release_cpu_model exportada")

print("PASS: toy_parakeet_model_cache")
