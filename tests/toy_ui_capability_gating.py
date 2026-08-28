"""Toy test: a UI reflete a capacidade da maquina (etapa 2).

Prova a regra central sem abrir janela: INCOMPATIVEL com a maquina
desabilita a acao e diz o motivo; falta baixar mantem habilitada
(porque o clique oferece o download). Usa a plataforma offscreen do Qt.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import capabilities as caps
from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

app = QApplication.instance() or QApplication([])

SEM_GPU = caps.Hardware(has_gpu=False, ram_gb=16.0, cores=8, free_disk_gb=50.0)
GPU_BOA = caps.Hardware(has_gpu=True, vram_gb=8.0, ram_gb=32.0, cores=16, free_disk_gb=99.0)
TAMANHOS = {caps.ASR_MODEL_TOKEN: 3.1, "alignment_pt": 1.4, "diarization": 0.07,
            "search_encoder": 0.5, "ner_gliner": 1.1, "llm_qwen": 8.7}


class _Janela:
    """So o suficiente para exercitar os metodos reais da janela."""
    _set_action = ReviewStudioWindow._set_action
    _capability_state = ReviewStudioWindow._capability_state

    def __init__(self, hardware, em_cache):
        self.context = None
        self._caps_cache = (hardware, set(em_cache), TAMANHOS)


# --- _set_action preserva o tooltip original (bug corrigido na etapa 0) ---
acao = QAction("✨ Resumir a entrevista com AI")
ORIGINAL = "Linha um\nLinha dois\nLinha tres"
acao.setToolTip(ORIGINAL)
janela = _Janela(SEM_GPU, set())
janela._set_action(acao, False, "motivo A")
assert acao.toolTip() == f"{ORIGINAL}\n(motivo A)"
janela._set_action(acao, False, "motivo B")
assert acao.toolTip() == f"{ORIGINAL}\n(motivo B)", "tooltip acumulou/truncou"
janela._set_action(acao, True)
assert acao.toolTip() == ORIGINAL, "tooltip original nao voltou"
print("PASS: _set_action preserva tooltip multi-linha")

# --- sem GPU: resumo e INCOMPATIVEL -> acao desabilitada COM motivo ---
janela = _Janela(SEM_GPU, {caps.ASR_MODEL_TOKEN, "llm_qwen"})
estado, motivo, _gb = janela._capability_state("resumo_perguntar")
assert estado == "incompativel" and "NVIDIA" in motivo, (estado, motivo)
acao = QAction("✨ Resumir a entrevista com AI")
acao.setToolTip("tooltip base")
janela._set_action(acao, estado != "incompativel", motivo)
assert acao.isEnabled() is False and "NVIDIA" in acao.toolTip()
print("PASS: sem GPU o resumo fica desabilitado com motivo")

# --- com GPU e modelo ausente: INSTALAVEL -> continua habilitada ---
janela = _Janela(GPU_BOA, {caps.ASR_MODEL_TOKEN})
estado, motivo, gb = janela._capability_state("resumo_perguntar")
assert estado == "instalavel" and gb == 8.7, (estado, gb)
acao = QAction("resumo")
janela._set_action(acao, estado != "incompativel", motivo)
assert acao.isEnabled() is True, "falta baixar nao pode desabilitar: o clique oferece o download"
print("PASS: modelo ausente mantem a acao habilitada")

# --- glossario roda em CPU: nunca incompativel ---
janela = _Janela(SEM_GPU, set())
estado, _motivo, gb = janela._capability_state("glossario_nomes")
assert estado == "instalavel" and gb == 1.1
print("PASS: glossario disponivel mesmo sem GPU")

# --- o cache e usado (nao sonda a maquina a cada consulta) ---
janela = _Janela(GPU_BOA, {caps.ASR_MODEL_TOKEN, "llm_qwen"})
antes = janela._caps_cache
janela._capability_state("resumo_perguntar")
assert janela._caps_cache is antes, "o cache foi recalculado sem necessidade"
print("PASS: cache de capacidades reutilizado")

print("PASS: toy_ui_capability_gating")
