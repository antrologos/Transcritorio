"""Toy: passo da segmentacao do pyannote (2 s) como padrao.

A/B 2026-09-02 (gabarito sintetico + 10 entrevistas reais + verificador
acustico): passo 0.2 (2 s a cada janela de 10 s) tem a mesma qualidade do
0.1 e e 2x mais rapido em CPU e GPU. `apply_segmentation_step` ajusta os
DOIS lugares que o pyannote le (segmentation_step no pipeline e .step em
segundos na Inference ja construida). Parte real SKIP sem modelo/torch.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.capabilities import expected_diarization_seconds  # noqa: E402
from transcribe_pipeline.config import DEFAULT_CONFIG  # noqa: E402
from transcribe_pipeline.diarization import apply_segmentation_step  # noqa: E402

# 1) chave de configuracao: 0.2 por padrao
assert DEFAULT_CONFIG["diarization_segmentation_step"] == 0.2

# 2) pura, com um pipeline de mentira (janela 10 s, passo 1 s como o pyannote)
def fake():
    return SimpleNamespace(segmentation_step=0.1, _segmentation=SimpleNamespace(duration=10.0, step=1.0))

p = fake()
assert apply_segmentation_step(p, 0.2) == 2.0
assert p.segmentation_step == 0.2 and p._segmentation.step == 2.0
p = fake()
assert apply_segmentation_step(p, 0.1) == 1.0          # igual ao atual: nada muda
assert p.segmentation_step == 0.1 and p._segmentation.step == 1.0
p = fake()
assert apply_segmentation_step(p, 0.0) == 1.0          # 0/negativo: ignorado
assert p.segmentation_step == 0.1
p = SimpleNamespace(segmentation_step=0.1, _segmentation=SimpleNamespace(duration=5.0, step=0.5))
assert apply_segmentation_step(p, 0.2) == 1.0          # fracao da janela, nao segundos fixos
# fracao > 1 (passo maior que a janela pularia audio): recusa sem tocar o pipeline
p = fake()
try:
    apply_segmentation_step(p, 2.0)
    raise AssertionError("2.0 deveria ser recusado")
except ValueError:
    pass
assert p.segmentation_step == 0.1 and p._segmentation.step == 1.0
# pipeline sem a Inference padrao: AttributeError (o chamador mantem o passo original)
try:
    apply_segmentation_step(SimpleNamespace(segmentation_step=0.1), 0.2)
    raise AssertionError("sem _segmentation deveria levantar")
except AttributeError:
    pass
print("PASS: apply_segmentation_step (pura)")

# 3) estimativas do app acompanham a medicao (0,060x CPU 24 nucleos; 0,0065x GPU)
assert abs(expected_diarization_seconds(3600, "cpu", 24) - (45 + 216)) < 1e-6
assert abs(expected_diarization_seconds(3600, "cpu", 4) - (45 + 216 * 6)) < 1e-6
assert abs(expected_diarization_seconds(3600, "cuda", 24) - (20 + 23.4)) < 1e-6
print("PASS: estimativas com o passo de 2 s")

# 4) pipeline real (opcional)
try:
    import torch  # noqa: F401
    from pyannote.audio import Pipeline
    from transcribe_pipeline import model_manager, runtime
    ckpt = model_manager.local_pyannote_checkpoint()
    if not Path(str(ckpt)).exists():
        raise ImportError("checkpoint local ausente")
    pipeline = Pipeline.from_pretrained(ckpt, token=None, cache_dir=str(runtime.model_cache_dir()))
except Exception as exc:  # noqa: BLE001 - CI minimo sem torch/pyannote/modelo
    print(f"SKIP (pipeline real): {exc}")
else:
    assert abs(float(pipeline._segmentation.step) - 1.0) < 1e-6, pipeline._segmentation.step
    assert abs(apply_segmentation_step(pipeline, 0.2) - 2.0) < 1e-6
    assert abs(float(pipeline.segmentation_step) - 0.2) < 1e-9
    print("PASS: pipeline real recebe o passo de 2 s")

print("PASS: toy_diar_step")
