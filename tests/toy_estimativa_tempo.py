"""Toy: estimativa de tempo do lote — 2026-09-04.

Nasceu de um relato real: um usuário pôs 4 h 22 min de áudio na fila num
i7-10610U e o app prometeu ~16 minutos; o lote levou 3 h 39. Duas causas:
(a) a correção por número de núcleos era aplicada só ao Whisper — o TAGARELA
usava a tabela da máquina de referência (24 threads) em qualquer computador;
(b) nenhuma fórmula enxerga a velocidade de CADA núcleo: os 8 threads dele
entregaram 0,46 s por segundo de áudio, contra 0,08 que a contagem de núcleos
previa — 5,7x, tudo em clock, IPC e redução térmica.

Daí as duas camadas testadas aqui: a fórmula corrigida (que satura, porque o
TAGARELA é limitado por banda de memória) e a MEDIÇÃO do histórico da própria
máquina, que manda quando existe.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import capabilities as caps  # noqa: E402

HORA = 3600.0

# --- measured_ratio: mediana, e silêncio quando há amostra de menos ---
assert caps.measured_ratio([]) is None
assert caps.measured_ratio([(60.0, 30.0)]) is None, "uma amostra só não decide"
assert caps.measured_ratio([(60.0, 30.0)], minimo=1) == 0.5
assert caps.measured_ratio([(60.0, 30.0), (60.0, 30.0)]) == 0.5
# mediana: o arquivo atípico não contamina
assert caps.measured_ratio([(60.0, 30.0), (60.0, 30.0), (60.0, 600.0)]) == 0.5
# pares inválidos são ignorados (duração zero, tempo zero, negativos)
assert caps.measured_ratio([(0.0, 10.0), (60.0, 0.0), (-1.0, 5.0)]) is None
assert caps.measured_ratio([(0.0, 10.0), (60.0, 30.0), (60.0, 30.0)]) == 0.5
assert caps.estimate_is_measured([(60.0, 30.0), (60.0, 30.0)]) is True
assert caps.estimate_is_measured([(60.0, 30.0)]) is False
assert caps.estimate_is_measured(None) is False
print("PASS: measured_ratio")

# --- a fórmula: TAGARELA passa a cair com os núcleos, mas SATURANDO ---
ref, _ = caps.batch_time_estimate(HORA, "parakeet_onnx", "cpu", 24)
poucos, _ = caps.batch_time_estimate(HORA, "parakeet_onnx", "cpu", 4)
assert poucos > ref, "antes o TAGARELA prometia o mesmo em qualquer maquina"
fator = poucos / ref
assert 1.4 < fator < 1.7, f"esperado ~1,57 (medido 0,093 vs 0,061); veio {fator:.2f}"
# ...e NÃO cai linearmente como o Whisper (24/4 = 6x), que exageraria
w_ref, _ = caps.batch_time_estimate(HORA, "whisper", "cpu", 24)
w_poucos, _ = caps.batch_time_estimate(HORA, "whisper", "cpu", 4)
assert abs(w_poucos / w_ref - 6.0) < 0.01, "Whisper continua linear"
assert fator < w_poucos / w_ref, "o TAGARELA satura; o Whisper nao"
# GPU não leva correção de núcleos
g4, _ = caps.batch_time_estimate(HORA, "parakeet_onnx", "cuda", 4)
g24, _ = caps.batch_time_estimate(HORA, "parakeet_onnx", "cuda", 24)
assert g4 == g24
print(f"PASS: formula corrigida (TAGARELA em 4 nucleos: {fator:.2f}x a referencia)")

# --- a medição manda quando existe ---
# o caso real: 4h22 de áudio, e o histórico diz 0,46 s por segundo
audio = 261.8 * 60
hist = [(4097.1, 2252.75), (5019.7, 1996.47), (714.6, 313.68), (5809.9, 2578.58)]
com, _ = caps.batch_time_estimate(audio, "parakeet_onnx", "cpu", 8, asr_samples=hist)
sem, _ = caps.batch_time_estimate(audio, "parakeet_onnx", "cpu", 8)
real = 119.7 * 60
assert abs(com - real) / real < 0.10, f"com historico: {com/60:.0f} min vs {real/60:.0f} reais"
assert sem < com / 3, "sem historico a formula ainda subestima muito esta maquina"
print(f"PASS: historico manda ({com/60:.0f} min estimados vs {real/60:.0f} min reais; "
      f"so pela formula seriam {sem/60:.0f} min)")

# --- a separação de falantes também aprende ---
diar_hist = [(4097.1, 1704.0), (5019.7, 1572.0)]      # 0,416 e 0,313 -> mediana 0,3645
_a, d_form = caps.batch_time_estimate(HORA, "parakeet_onnx", "cpu", 8)
_a, d_hist = caps.batch_time_estimate(HORA, "parakeet_onnx", "cpu", 8, diar_samples=diar_hist)
assert d_hist > d_form, "o historico real desta maquina e mais lento que a formula"
razao = caps.measured_ratio(diar_hist)
assert abs(razao - 0.3645) < 0.001, razao
assert abs(d_hist - (45.0 + HORA * razao)) < 1.0, d_hist
print("PASS: separacao de falantes tambem usa o historico")

# --- nada quebrou: lote vazio, e a assinatura antiga continua valendo ---
assert caps.batch_time_estimate(0, "parakeet_onnx", "cpu", 8) == (0.0, 0.0)
antigo = caps.batch_time_estimate(HORA, "whisper", "cuda", 8, asr_device="cuda")
assert antigo[0] > 0 and antigo[1] > 0
assert caps.describe_seconds(3 * 3600 + 39 * 60) == "3 h 39 min"
print("PASS: bordas e compatibilidade")

# --- o coletor do historico: so etapas boas, e a duracao vem de onde houver ---
from transcribe_pipeline import project_store as ps  # noqa: E402

entradas = [
    {"stage": "transcribe", "status": "ok", "interview_id": "E1", "elapsed_s": 30.0},
    {"stage": "transcribe", "status": "ok", "interview_id": "E2", "elapsed_s": 60.0,
     "audio_seconds": 120.0},                       # duracao na propria entrada
    {"stage": "transcribe", "status": "cancelled", "interview_id": "E3", "elapsed_s": 999.0},
    {"stage": "transcribe", "status": "error", "interview_id": "E4", "elapsed_s": 999.0},
    {"stage": "transcribe", "status": "ok", "interview_id": "E5"},          # sem elapsed_s
    {"stage": "transcribe", "status": "ok", "interview_id": "SUMIU", "elapsed_s": 10.0},
    {"stage": "diarize", "status": "ok", "interview_id": "E1", "elapsed_s": 20.0},
    {"stage": "prepare-audio", "status": "ok", "interview_id": "E1", "elapsed_s": 5.0},
]
duracoes = {"E1": 60.0, "E3": 60.0, "E4": 60.0, "E5": 60.0}
tr = ps.stage_samples(entradas, "transcribe", duracoes)
assert tr == [(60.0, 30.0), (120.0, 60.0)], tr        # cancelada, com erro, sem tempo e sem duracao ficam fora
assert ps.stage_samples(entradas, "diarize", duracoes) == [(60.0, 20.0)]
assert ps.stage_samples([], "transcribe", {}) == []
assert caps.measured_ratio(tr) == 0.5
print("PASS: coletor do historico (stage_samples)")

print("PASS: toy_estimativa_tempo")
