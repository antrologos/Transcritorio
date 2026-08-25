"""Toy test para boundary_check (plano 2026-08-25, lote 1).

Funcoes puras: pares candidatos, janelas com clamp, flags idempotentes.
Depende so de numpy (import indireto do modulo); skip se indisponivel.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline import boundary_check as bc
except ImportError as exc:  # numpy ausente no ambiente minimo
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def turn(start: float, end: float, speaker: str, **extra) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "text": "bla", **extra}


# --- candidate_boundaries: so pares com falantes distintos ---
turns = [
    turn(0.0, 5.0, "SPEAKER_00"),
    turn(5.1, 9.0, "SPEAKER_01"),
    turn(9.2, 12.0, "SPEAKER_01"),
    turn(12.1, 20.0, "SPEAKER_00"),
]
pairs = bc.candidate_boundaries(turns)
assert [p[0] for p in pairs] == [0, 2], pairs
assert pairs[0][1] is turns[0] and pairs[0][2] is turns[1]
print("PASS: candidate_boundaries")

# --- boundary_windows: janela cheia, clamp e rejeicao de lado curto ---
full = bc.boundary_windows(turn(0.0, 10.0, "A"), turn(10.2, 20.0, "B"), window=2.0)
assert full == ((8.0, 10.0), (10.2, 12.2)), full

clamped = bc.boundary_windows(turn(9.0, 10.0, "A"), turn(10.2, 20.0, "B"), window=2.0)
assert clamped == ((9.0, 10.0), (10.2, 12.2)), clamped  # esquerda presa ao turno (1.0s >= 0.7)

short = bc.boundary_windows(turn(9.5, 10.0, "A"), turn(10.2, 20.0, "B"), window=2.0)
assert short is None  # 0.5s < 0.7s

right_short = bc.boundary_windows(turn(0.0, 10.0, "A"), turn(10.2, 10.5, "B"), window=2.0)
assert right_short is None
print("PASS: boundary_windows")

# --- flag_turn: idempotente, preserva flags/notas existentes ---
t = turn(0.0, 5.0, "SPEAKER_00", flags=["inaudivel"], notes="ruido de fundo")
note = bc.boundary_note(0.87)
assert bc.BOUNDARY_NOTE_MARKER in note and "87%" in note
assert bc.flag_turn(t, "duvida", note, bc.BOUNDARY_NOTE_MARKER) is True
assert t["flags"] == ["inaudivel", "duvida"]
assert t["notes"].startswith("ruido de fundo | ")
assert bc.flag_turn(t, "duvida", note, bc.BOUNDARY_NOTE_MARKER) is False  # 2a vez: nada
assert t["flags"].count("duvida") == 1 and t["notes"].count("87%") == 1

clean = turn(0.0, 5.0, "SPEAKER_00")
assert bc.flag_turn(clean, "sobreposicao", bc.OVERLAP_NOTE, bc.OVERLAP_NOTE) is True
assert clean["flags"] == ["sobreposicao"] and clean["notes"] == bc.OVERLAP_NOTE
assert bc.flag_turn(clean, "sobreposicao", bc.OVERLAP_NOTE, bc.OVERLAP_NOTE) is False
print("PASS: flag_turn idempotente")

# --- embed_window: janela curta demais retorna None sem chamar o modelo ---
import numpy as np

waveform = np.zeros((1, 16000), dtype=np.float32)  # 1s @16k
assert bc.embed_window(None, waveform, 16000, 0.0, 0.3) is None
print("PASS: embed_window guarda de janela curta")

print("PASS: toy_boundary_check")
