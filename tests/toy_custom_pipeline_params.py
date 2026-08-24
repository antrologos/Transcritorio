"""Toy test para o fix D1.4: hiperparametros de clustering deixam de ser inertes.

Bug: diarization_fa/diarization_fb so eram aplicados se
diarization_clustering_threshold tambem estivesse setado (e o default e
None) — config morto. Fix: cada chave setada vale por si.

Validado empiricamente em 2026-08-23 no modelo community-1 real:
pipeline.instantiate() com dict parcial (por secao E por chave dentro da
secao) preserva os demais parametros do config.yaml do modelo.

Importa diarization.py (numpy no topo); skip condicional no CI minimo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.diarization import _custom_pipeline_params
except ImportError as exc:  # CI minimo sem numpy
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

# Defaults do projeto (threshold/fa/fb None, min_duration_off 0.5):
# comportamento IDENTICO ao anterior — so segmentation
params = _custom_pipeline_params(
    {"diarization_clustering_threshold": None, "diarization_fa": None,
     "diarization_fb": None, "diarization_min_duration_off": 0.5}
)
assert params == {"segmentation": {"min_duration_off": 0.5}}, params
print("PASS: defaults inalterados (so segmentation)")

# Fa sozinho agora e aplicado (antes era config morto)
params = _custom_pipeline_params(
    {"diarization_fa": 0.1, "diarization_min_duration_off": 0.5}
)
assert params == {"clustering": {"Fa": 0.1}, "segmentation": {"min_duration_off": 0.5}}, params
print("PASS: diarization_fa sozinho deixa de ser inerte")

# threshold sozinho nao arrasta mais Fa/Fb hardcoded (instantiate parcial
# preserva os valores do config.yaml do modelo)
params = _custom_pipeline_params({"diarization_clustering_threshold": 0.7})
assert params == {"clustering": {"threshold": 0.7}}, params
print("PASS: threshold sozinho sem Fa/Fb fantasma")

# Tudo setado
params = _custom_pipeline_params(
    {"diarization_clustering_threshold": 0.7, "diarization_fa": 0.05,
     "diarization_fb": 0.9, "diarization_min_duration_off": 0.3}
)
assert params == {
    "clustering": {"threshold": 0.7, "Fa": 0.05, "Fb": 0.9},
    "segmentation": {"min_duration_off": 0.3},
}, params
print("PASS: todas as chaves aplicadas")

# Nada setado -> instantiate nem e chamado
assert _custom_pipeline_params({}) == {}
print("PASS: config vazio -> sem instantiate")

print()
print("PASS: toy_custom_pipeline_params")
