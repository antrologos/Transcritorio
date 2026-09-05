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
# 0,060x com a rede 1x por janela + passo de 2 s (A/B 2026-09-02: 323 min
# de audio em 19,3 min); GPU 0,0065x (62 min em 23 s).
assert abs(expected_diarization_seconds(300, "cpu", 24) - (45 + 18)) < 1e-6
assert abs(expected_diarization_seconds(3600, "cpu", 24) - (45 + 216)) < 1e-6   # 1 h ~ 4,4 min
# 2026-09-05: a escala com nucleos NAO e linear. Medido com afinidade em
# 4 nucleos fisicos e entrevistas inteiras: 0,100 s por segundo de audio,
# contra os 0,36 que a escala linear previa — expoente 0,3, nao 1.
_quatro = expected_diarization_seconds(3600, "cpu", 4)
assert abs(_quatro - (45 + 216 * (24 / 4) ** 0.3)) < 1e-6
assert 340 < _quatro < 430, f"1 h em 4 nucleos deve dar ~7 min, deu {_quatro/60:.1f} min"
assert expected_diarization_seconds(3600, "cpu", 4) > expected_diarization_seconds(3600, "cpu", 8)     > expected_diarization_seconds(3600, "cpu", 24), "menos nucleos, mais tempo"
assert abs(expected_diarization_seconds(3600, "cuda", 24) - (20 + 23.4)) < 1e-6
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
assert 200 < asr < 240 and abs(diar - 261) < 1e-6, (asr, diar)   # 45 + 0,06 x 3600 (passo 2 s)
asr4, diar4 = batch_time_estimate(3600, "parakeet_onnx", "cpu", 8)
# ATE 2026-09-04 este teste exigia asr4 == asr — ou seja, fixava o defeito:
# o TAGARELA prometia o mesmo tempo em qualquer maquina, porque a correcao por
# nucleos so era aplicada ao Whisper. Agora ele desce com os nucleos, mas
# SATURANDO (limitado por banda de memoria; ver toy_estimativa_tempo).
assert asr4 > asr, "a estimativa do TAGARELA tem de piorar com menos nucleos"
assert asr4 < asr * (24 / 8), "mas nao linearmente, como a do Whisper"
assert diar4 > diar
asr, _ = batch_time_estimate(3600, None, "cpu", 24)
assert abs(asr - 3960) < 1e-6                       # Whisper small 1,1x
asr, diar = batch_time_estimate(3600, "whisper", "cuda", 24)
assert abs(asr - 450) < 1e-6 and diar < 200
assert batch_time_estimate(0, None, "cpu", 24) == (0.0, 0.0)
# TAGARELA numa maquina CUDA sem o pacote onnx-gpu: transcricao em CPU (agora
# COM escala por nucleos, saturando), separacao em GPU (2026-09-02)
a, d = batch_time_estimate(3600, "parakeet_onnx", "cuda", 24, asr_device="cpu")
assert abs(a - 3600 / 16.5) < 1e-6 and abs(d - (20 + 0.0065 * 3600)) < 1e-6, (a, d)
a8, d8 = batch_time_estimate(3600, "parakeet_onnx", "cuda", 8, asr_device="cpu")
assert a8 > a, "a transcricao em CPU tambem depende dos nucleos aqui"
assert abs(d8 - d) < 1e-6, "a separacao segue em GPU, sem escala por nucleos"
g, _ = batch_time_estimate(3600, "parakeet_onnx", "cuda", 24, asr_device="cuda")
assert abs(g - 3600 / 62.0) < 1e-6
assert d < a
assert describe_seconds(20) == "menos de 1 min"
assert describe_seconds(240) == "4 min"
assert describe_seconds(4200) == "1 h 10 min"
assert describe_seconds(7200) == "2 h"
print("PASS: estimativa do lote")

print("PASS: toy_diarize_progress")
