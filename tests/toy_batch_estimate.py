"""Toy: janela do lote com estimativa + caixa "Separar falantes agora".

2026-09-02: em maquina sem GPU a separacao de falantes domina o tempo do
lote (0,4x o audio) e o texto pode ficar pronto em minutos. A janela que
ja perguntava "quantas pessoas falam" ganhou a estimativa honesta e a
caixa; desmarcada, vale SO para o lote (job_step_flags), nunca grava
diarize:false no projeto. Depende de PySide6 (offscreen).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import SpeakerCountDialog, job_step_flags

app = QApplication.instance() or QApplication([])

# --- pura: decisao do lote ---
assert job_step_flags(True, True, None) == (True, True)
assert job_step_flags(True, True, True) == (True, True)
assert job_step_flags(True, True, False) == (False, False)   # caixa desmarcada: nem conferencia
assert job_step_flags(True, False, None) == (True, False)
assert job_step_flags(False, True, True) == (False, False)   # projeto sem diarizacao: caixa nao liga
print("PASS: job_step_flags")

# --- GPU / comportamento antigo: so contagens, sem caixa ---
d = SpeakerCountDialog(3)
assert d.windowTitle() == "Quantas pessoas falam?"
assert d.interview_radio is not None and d.interview_radio.isChecked()
assert d.diarize_now_check is None and d.diarize_now() is True
assert d.updates()["speaker_setup"] == "true" and d.updates()["speaker_count"] == "2"

# --- CPU com contagens pendentes: radios + estimativa + caixa marcada ---
texto = "Neste computador (sem placa de vídeo), para 2 h de áudio: transcrição ≈ 7 min · separação de falantes ≈ 49 min."
d = SpeakerCountDialog(2, estimate_text=texto, ask_counts=True)
assert d.diarize_now_check is not None and d.diarize_now_check.isChecked()
assert d.diarize_now() is True
d.diarize_now_check.setChecked(False)
assert d.diarize_now() is False
assert d.updates()["speaker_setup"] == "true"   # contagens continuam sendo gravadas

# --- CPU com contagens ja configuradas: so estimativa + caixa; updates vazio ---
d = SpeakerCountDialog(0, estimate_text=texto, ask_counts=False)
assert d.windowTitle() == "Transcrever"
assert d.interview_radio is None and d.diarize_now_check is not None
assert d.updates() == {}
assert d.diarize_now() is True
print("PASS: SpeakerCountDialog com estimativa e caixa")

print("PASS: toy_batch_estimate")
