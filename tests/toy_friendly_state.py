"""Toy test: estado "Falha" visivel na tabela (SL-C4). Offscreen.

Bug original: um job com status "Falha" aparecia como "Não transcrita"
— o erro so existia na Fila de tarefas, que o usuario nao abre.
Regra: falha em RETRANSCRICAO nao esconde transcricao utilizavel
(review/canonical existentes vencem a falha).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
    friendly_state = ReviewStudioWindow.friendly_state


def _status(review=False, canonical=False):
    return SimpleNamespace(review_exists=review, canonical_exists=canonical)


janela = _Janela()
# rodando vence tudo
assert janela.friendly_state(_status(), {"status": "Rodando", "progress": 42}) == "Processando 42%"
# transcrita segue transcrita, mesmo com falha registrada (retranscricao falhou)
assert janela.friendly_state(_status(review=True), {"status": "Falha"}) == "Transcrita"
# falha sem nada utilizavel aparece como Falha
assert janela.friendly_state(_status(), {"status": "Falha"}) == "Falha"
# estados normais
assert janela.friendly_state(_status(canonical=True), {}) == "Transcrita"
assert janela.friendly_state(_status(), {}) == "Não transcrita"
assert janela.friendly_state(_status(), None) == "Não transcrita"

print("PASS: toy_friendly_state")
