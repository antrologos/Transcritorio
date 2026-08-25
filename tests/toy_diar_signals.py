"""Toy test para diar_signals (plano 2026-08-25, lote 2).

Derivacoes puras com arrays sinteticos: regioes por contagem, margens por
segmento, coletor de hook. Depende de numpy; skip se indisponivel.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy as np
    from transcribe_pipeline import diar_signals as ds
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

# --- SignalCollector: guarda o ultimo artefato completo, ignora parciais ---
collector = ds.SignalCollector()
collector.hook("segmentation", "parcial", total=10, completed=3)
assert "segmentation" not in collector.artifacts  # parcial ignorado
collector.hook("segmentation", "cheio", total=10, completed=10)
assert collector.artifacts["segmentation"] == "cheio"
collector.hook("speaker_counting", "contagem")  # sem total/completed = final
assert collector.artifacts["speaker_counting"] == "contagem"
collector.hook("irrelevante", "x")
assert "irrelevante" not in collector.artifacts
collector.hook("embeddings", None)
assert "embeddings" not in collector.artifacts  # None ignorado
print("PASS: SignalCollector")

# --- regions_from_counts: run-length com duracao minima e run no final ---
counts = [0, 0, 0, 1, 2, 2, 1, 0, 0, 0, 0]
# frames de 0.1s comecando em 0.0: zeros em [0.0,0.3) e [0.7,1.1); dois+ em [0.4,0.6)
silences = ds.regions_from_counts(counts, 0.0, 0.1, lambda v: v <= 0)
assert silences == [(0.0, 0.3), (0.7, 1.1)], silences
overlaps = ds.regions_from_counts(counts, 0.0, 0.1, lambda v: v >= 2, min_duration=0.2)
assert overlaps == [(0.4, 0.6)], overlaps
# regiao curta demais e filtrada (0.2s < 0.3 default)
assert ds.regions_from_counts(counts, 0.0, 0.1, lambda v: v >= 2) == []
print("PASS: regions_from_counts")

# --- segment_margins: sintetico com 2 centroides ortogonais ---
dim = 8
centroid_a = np.zeros(dim); centroid_a[0] = 1.0
centroid_b = np.zeros(dim); centroid_b[1] = 1.0
centroids = np.stack([centroid_a, centroid_b])
labels = ["SPEAKER_00", "SPEAKER_01"]

# 4 chunks de 10s a cada 1s (starts 0..3). Local 0 = voz A, local 1 = voz B,
# local 2 = inativo (NaN, como o pyannote produz).
rng = np.random.default_rng(7)
chunks = np.full((4, 3, dim), np.nan)
for c in range(4):
    chunks[c, 0] = centroid_a + rng.normal(0, 0.05, dim)
    chunks[c, 1] = centroid_b + rng.normal(0, 0.05, dim)

segments = [
    {"start": 1.0, "end": 3.0, "speaker": "SPEAKER_00"},   # voz A clara
    {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},   # voz B clara
    {"start": 0.5, "end": 1.5, "speaker": "SPEAKER_99"},   # falante desconhecido
]
chunk_starts = np.array([0.0, 1.0, 2.0, 3.0])
margins = ds.segment_margins(chunks, chunk_starts, 10.0, centroids, labels, segments)
assert len(margins) == 2, margins  # desconhecido descartado
assert all(m["margin"] > 0.7 for m in margins), margins  # vozes ortogonais = margem alta

# Segmento atribuido ao falante ERRADO -> margem negativa
wrong = ds.segment_margins(
    chunks, chunk_starts, 10.0, centroids, labels,
    [{"start": 1.0, "end": 3.0, "speaker": "SPEAKER_01"}],
)
# o melhor canal local para o centroide B e o local 1 (voz B) — segmento com
# audio de A rotulado como B nao e simulavel so por rotulo; o que da para
# garantir: com vozes identicas nos dois centroides a margem despenca.
same = np.stack([centroid_a, centroid_a + 0.01])
close = ds.segment_margins(
    chunks, chunk_starts, 10.0, ds._normalize_rows(same), labels,
    [{"start": 1.0, "end": 3.0, "speaker": "SPEAKER_00"}],
)
assert close and close[0]["margin"] < 0.05, close  # centroides quase iguais = ambiguo
assert wrong  # nao crasha e produz veredito
print("PASS: segment_margins")

# --- speaker_stats ---
stats = ds.speaker_stats(margins)
assert set(stats) == {"SPEAKER_00", "SPEAKER_01"}
assert stats["SPEAKER_00"]["segments"] == 1
print("PASS: speaker_stats")

# --- um so falante: sem margens (nao ha segundo colocado) ---
assert ds.segment_margins(chunks, chunk_starts, 10.0, centroids[:1], ["SPEAKER_00"], segments) == []
print("PASS: um falante -> sem margens")

print("PASS: toy_diar_signals")
