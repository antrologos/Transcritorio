"""Toy: embeddings do pyannote com a rede 1x por janela (diar_fast).

2026-09-02: 94% da separacao de falantes em CPU era a ResNet34 rodando
3x por janela (uma por vaga de falante), sendo que a mascara so entra no
pooling. Secao A: puras (numpy). Secao B (so com torch + pyannote +
checkpoint local em cache; senao SKIP): igualdade numerica contra o
get_embeddings ORIGINAL no mesmo objeto de pipeline, com waveform e
segmentacao sinteticos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.diar_fast import (
    embedding_batch_count,
    fast_get_embeddings,
    install_fast_embeddings,
    select_used_masks,
)

# ---------------- Secao A: puras ----------------
masks = np.array([[1, 1, 0], [1, 1, 0], [1, 1, 0], [1, 0, 0], [1, 0, 0], [1, 0, 0]], dtype=np.float32)
clean = masks * (masks.sum(axis=1, keepdims=True) < 2)
sel = select_used_masks(masks, clean, 2)
assert sel.shape == (3, 6) and sel.dtype == np.float32
assert sel[0].tolist() == [0, 0, 0, 1, 1, 1]      # limpa suficiente
assert sel[1].tolist() == [1, 1, 1, 0, 0, 0]      # limpa curta -> cheia
assert sel[2].tolist() == [0] * 6                  # vaga vazia -> zeros (sem NaN)
assert select_used_masks(masks, clean, 3)[0].tolist() == [1] * 6   # "> min" estrito
assert np.array_equal(select_used_masks(masks, masks, -1), masks.T)  # exclude_overlap=False
m_nan = masks.copy(); m_nan[0, 0] = np.nan
assert select_used_masks(m_nan, m_nan, -1)[0, 0] == 0.0
assert embedding_batch_count(291, 32) == 10 and embedding_batch_count(33, 32) == 2
assert embedding_batch_count(0, 32) == 0 and embedding_batch_count(5, 0) == 5


class _SemCaminhoSeparado:
    class _Emb:
        device = "cpu"
        model_ = object()   # sem forward_frames/forward_embedding
    _embedding = _Emb()
    _audio = object()
    def get_embeddings(self): ...


assert install_fast_embeddings(_SemCaminhoSeparado()) is False
assert install_fast_embeddings(object()) is False
print("PASS: puras do diar_fast")

# ---------------- Secao B: igualdade numerica no modelo real ----------------
try:
    import torch
    from pyannote.audio import Pipeline
    from pyannote.core import SlidingWindow, SlidingWindowFeature
    from transcribe_pipeline import model_manager, runtime
    checkpoint = model_manager.local_pyannote_checkpoint()
    if not checkpoint or not Path(str(checkpoint)).exists():
        raise ImportError("checkpoint pyannote nao esta em cache")
except Exception as exc:  # pragma: no cover - CI minimo sem torch/modelo
    print(f"SKIP secao B (modelo real): {exc}")
    print("PASS: toy_diar_fast (so puras)")
    sys.exit(0)

runtime.apply_secure_hf_environment(offline=True, token_env="TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN")
pipeline = Pipeline.from_pretrained(checkpoint, token=None, cache_dir=str(runtime.model_cache_dir())).to(torch.device("cpu"))
assert install_fast_embeddings(pipeline) is True, "community-1 deveria expor forward_frames/forward_embedding"
original = pipeline._get_embeddings_original

# Audio sintetico de 30 s (ruido colorido + tons) e segmentacao binaria
# sintetica no layout do pipeline: (num_chunks, num_frames, 3).
sr = 16000
rng = np.random.default_rng(7)
t = np.arange(30 * sr) / sr
wave = (0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 0.5 * t) > 0)
        + 0.2 * np.sin(2 * np.pi * 330 * t) * (np.sin(2 * np.pi * 0.5 * t) <= 0)
        + 0.05 * rng.standard_normal(t.shape)).astype(np.float32)
file = {"waveform": torch.from_numpy(wave)[None], "sample_rate": sr, "uri": "toy"}

duration = float(pipeline._segmentation.duration)
step = float(pipeline._segmentation.step)
num_frames = int(pipeline._segmentation.model.num_frames(int(duration * sr)))
num_chunks = int(np.floor((30 - duration) / step)) + 1
seg = np.zeros((num_chunks, num_frames, 3), dtype=np.float32)
metade = num_frames // 2
seg[:, :metade, 0] = 1.0                 # falante 0: primeira metade de cada janela
seg[:, metade:, 1] = 1.0                 # falante 1: segunda metade
seg[::3, metade - 20:metade + 20, 0] = 1  # sobreposicao curta em 1/3 das janelas
seg[5, :, 2] = 1.0                       # uma janela com a 3a vaga ativa
seg[7, :, :] = 0.0                       # uma janela totalmente vazia
window = SlidingWindow(start=0.0, duration=duration, step=step)
binary = SlidingWindowFeature(seg, window)

eventos: list[tuple] = []
def hook(name, artifact=None, *, file=None, total=None, completed=None):
    eventos.append((name, None if artifact is None else np.asarray(artifact).shape, total, completed))

ref = original(file, binary, exclude_overlap=True, hook=None)
eventos.clear()
fast = fast_get_embeddings(pipeline, file, binary, exclude_overlap=True, hook=hook)

assert fast.shape == ref.shape == (num_chunks, 3, 256), (fast.shape, ref.shape)
assert fast.dtype == ref.dtype == np.float32
diff = np.abs(fast - ref).max()
num = (fast * ref).sum(axis=-1)
den = np.linalg.norm(fast, axis=-1) * np.linalg.norm(ref, axis=-1) + 1e-12
cos = num / den
assert diff < 1e-4, f"max|delta| = {diff}"
assert cos.min() > 0.99999, f"cos-sim minima = {cos.min()}"
assert np.isfinite(fast).all()
# contrato do hook: total = lotes de JANELAS, ultimo completed == total, lotes 2-D (c s) d
assert eventos[0] == ("embeddings", None, embedding_batch_count(num_chunks, pipeline.embedding_batch_size), 0)
assert eventos[-1][3] == eventos[-1][2] and eventos[-1][1][1] == 256
assert sum(e[1][0] for e in eventos[1:]) == num_chunks * 3
print(f"PASS: igualdade numerica (max|delta|={diff:.2e}, cos-sim min={cos.min():.6f}, "
      f"{len(eventos) - 1} lotes)")

# exclude_overlap=False tambem identico
ref2 = original(file, binary, exclude_overlap=False, hook=None)
fast2 = fast_get_embeddings(pipeline, file, binary, exclude_overlap=False, hook=None)
assert np.abs(fast2 - ref2).max() < 1e-4
print("PASS: toy_diar_fast")
