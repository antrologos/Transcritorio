"""Toy test para deteccao de sobreposicao por varredura (boundary_check).

overlap_intervals + turns_overlapping_intervals com segmentos sinteticos.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline import boundary_check as bc
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def seg(start: float, end: float, speaker: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker}


# Sobreposicao simples: B dentro de A
intervals = bc.overlap_intervals([seg(0, 10, "S0"), seg(5, 8, "S1")])
assert intervals == [(5.0, 8.0)], intervals

# Sobreposicao curta demais (< 0.3s) e filtrada
assert bc.overlap_intervals([seg(0, 10, "S0"), seg(5, 5.2, "S1")]) == []

# Tres segmentos escalonados: >=2 ativos em (3,4) e (5,6); entre 4 e 5 so um
intervals = bc.overlap_intervals([seg(2, 4, "S0"), seg(3, 6, "S1"), seg(5, 7, "S2")])
assert intervals == [(3.0, 4.0), (5.0, 6.0)], intervals

# Segmentos adjacentes (fim == inicio) NAO sao sobreposicao
assert bc.overlap_intervals([seg(0, 5, "S0"), seg(5, 10, "S1")]) == []

# Segmento degenerado (end <= start) e ignorado
assert bc.overlap_intervals([seg(0, 5, "S0"), seg(3, 3, "S1")]) == []
print("PASS: overlap_intervals")

# Regra por FRACAO do turno coberta (>= 0.5): interjeicao curta engolida e
# marcada; turno longo com backchannel breve NAO e.
turns = [
    {"start": 3.2, "end": 4.0, "speaker": "S0"},    # 100% dentro de (3,6) -> flag
    {"start": 0.0, "end": 40.0, "speaker": "S1"},   # 3s de 40s = 7.5% -> sem flag
    {"start": 6.1, "end": 9.0, "speaker": "S0"},    # fora -> sem flag
    {"start": 2.5, "end": 5.5, "speaker": "S1"},    # 2.5s de 3s = 83% -> flag
    {"start": 5.0, "end": 5.0, "speaker": "S0"},    # duracao zero -> ignorado
]
hits = bc.turns_overlapping_intervals(turns, [(3.0, 6.0)])
assert hits == [0, 3], hits

# Fracao soma MULTIPLOS intervalos: 1s+1s de 3s = 67% -> flag
hits = bc.turns_overlapping_intervals(
    [{"start": 0.0, "end": 3.0, "speaker": "S0"}], [(0.0, 1.0), (2.0, 3.0)])
assert hits == [0], hits
print("PASS: turns_overlapping_intervals (fracao)")

print("PASS: toy_overlap_divergence")
