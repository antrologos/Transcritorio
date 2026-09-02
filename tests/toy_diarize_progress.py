"""Toy: progresso honesto da separacao de falantes (2026-09-02).

Beta tester em CPU: "congelou no 88%" — teto do heartbeat calibrado para
GPU. Agora o hook do pyannote alimenta a barra (segmentation por chunk,
embeddings por batch) e o heartbeat e reserva com expectativa por device
e duracao (medido: 0,40x o audio em CPU com 24 nucleos logicos; 0,028x
em GPU). Puras, sem torch/Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.capabilities import (
    batch_time_estimate,
    describe_seconds,
    expected_diarization_seconds,
)
from transcribe_pipeline.diarization import diarize_hook_percent, heartbeat_percent

# --- mapeamento do hook: ordem real do pipeline community-1 ---
assert diarize_hook_percent("segmentation", 0, 10) == 2
assert diarize_hook_percent("segmentation", 5, 10) == 23
assert diarize_hook_percent("segmentation", 10, 10) == 45
assert diarize_hook_percent("speaker_counting", None, None) == 48
assert diarize_hook_percent("embeddings", 0, 8) == 50
assert diarize_hook_percent("embeddings", 4, 8) == 70
assert diarize_hook_percent("embeddings", 8, 8) == 90
assert diarize_hook_percent("discrete_diarization", None, None) == 95
assert diarize_hook_percent("segmentation", None, None) is None   # sem total: heartbeat cobre
assert diarize_hook_percent("embeddings", 3, 0) is None
assert diarize_hook_percent("outro_step", 1, 2) is None
seq = ([diarize_hook_percent("segmentation", i, 10) for i in range(11)] + [48]
       + [diarize_hook_percent("embeddings", i, 8) for i in range(9)] + [95])
assert seq == sorted(seq), "progresso do hook precisa ser monotonico"
print("PASS: mapeamento do hook do pyannote")

# --- expectativa por device e nucleos (referencia: 24 nucleos logicos) ---
assert abs(expected_diarization_seconds(300, "cpu", 24) - (45 + 120)) < 1e-6    # ~104 s medidos + carga
assert abs(expected_diarization_seconds(3600, "cpu", 24) - (45 + 1440)) < 1e-6  # 1 h ~ 25 min
assert abs(expected_diarization_seconds(3600, "cpu", 8) - (45 + 4320)) < 1e-6   # 8 nucleos: 3x
assert abs(expected_diarization_seconds(3600, "cuda", 24) - (20 + 100.8)) < 1e-6
assert expected_diarization_seconds(0, "cpu", 24) == 45.0
assert expected_diarization_seconds(3600, "cpu", 0) > 0   # cores invalido nao explode
print("PASS: expectativa de tempo da diarizacao")

# --- heartbeat: o real vence o creep; cap 0,95; nunca abaixo de lo+1 ---
lo, hi = 20, 90
assert heartbeat_percent(0, 1000, None, lo, hi) == lo + 1
assert heartbeat_percent(10, 1000, None, lo, hi) < heartbeat_percent(100, 1000, None, lo, hi)
assert heartbeat_percent(10, 1000, 60, lo, hi) == lo + int(67 * 0.60)
assert heartbeat_percent(10**6, 1000, None, lo, hi) == lo + int(67 * 0.95)
assert heartbeat_percent(10**6, 1000, 100, lo, hi) == lo + int(67 * 0.95)
# GPU (expectativa curta) sobe rapido; CPU de poucos nucleos, devagar
assert (heartbeat_percent(60, expected_diarization_seconds(3600, "cuda", 24), None, lo, hi)
        > heartbeat_percent(60, expected_diarization_seconds(3600, "cpu", 4), None, lo, hi))
# e o antigo teto de 88 nao existe mais quando o real chega ao fim
assert heartbeat_percent(5, 1000, 95, lo, hi) >= lo + int(67 * 0.95)
print("PASS: heartbeat com real > creep")

# --- estimativa do lote (janela "quantas pessoas falam" em CPU) ---
asr, diar = batch_time_estimate(3600, "parakeet_onnx", "cpu", 24)
assert 200 < asr < 240 and abs(diar - 1485) < 1e-6, (asr, diar)
asr4, diar4 = batch_time_estimate(3600, "parakeet_onnx", "cpu", 8)
assert abs(asr4 - asr) < 1e-6 and diar4 > diar
asr, _ = batch_time_estimate(3600, None, "cpu", 24)
assert abs(asr - 3960) < 1e-6                       # Whisper small 1,1x
asr, diar = batch_time_estimate(3600, "whisper", "cuda", 24)
assert abs(asr - 450) < 1e-6 and diar < 200
assert batch_time_estimate(0, None, "cpu", 24) == (0.0, 0.0)
assert describe_seconds(20) == "menos de 1 min"
assert describe_seconds(240) == "4 min"
assert describe_seconds(4200) == "1 h 10 min"
assert describe_seconds(7200) == "2 h"
print("PASS: estimativa do lote")

print("PASS: toy_diarize_progress")
