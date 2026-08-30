"""Toy test: painel do arquivo mostra o modelo que PRODUZIU a review (SL-C1).

A review guarda o modelo em transcript.asr_model (copiado do canonical
no render), mas nada na UI lia isso — o cabecalho mostra o modelo
CONFIGURADO, que pode ser outro. Com "Transcrever novamente" e mais de
um modelo instalado, o usuario precisa ver qual gerou o texto atual.
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

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

app = QApplication.instance() or QApplication([])


class _Janela:
    _review_title_text = ReviewStudioWindow._review_title_text

    def __init__(self, review):
        self.review = review


# review com modelo: titulo nomeia o modelo (rotulo amigavel)
titulo = _Janela({"transcript": {"asr_model": "small"}})._review_title_text("E01")
assert titulo.startswith("Transcrição: E01"), titulo
assert "small" in titulo, titulo

# variante desconhecida: cai para a chave crua, sem quebrar
titulo = _Janela({"transcript": {"asr_model": "modelo-x"}})._review_title_text("E02")
assert "modelo-x" in titulo, titulo

# sem modelo registrado (reviews antigas) ou sem review: so o id
assert _Janela({"transcript": {}})._review_title_text("E03") == "Transcrição: E03"
assert _Janela({})._review_title_text("E04") == "Transcrição: E04"
assert _Janela(None)._review_title_text("E05") == "Transcrição: E05"

print("PASS: toy_review_title_model")
