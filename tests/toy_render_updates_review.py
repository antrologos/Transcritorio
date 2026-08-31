"""Toy: remontar documentos apos mudar rotulos tem EFEITO visivel.

Historia: os bugs da checagem geral (2026-08-30) mostraram que render
sem refresh_unedited_reviews nunca mudava o que o usuario ve. As acoes
antigas ("Atualizar transcricao editavel" / "Reprocessar falantes")
morreram na R3-c4/R4-c7; a invariante render-por-arquivo + refresh das
reviews pristinas sobrevive nos fluxos vivos — aqui, o tail de
_offer_rerender_after_label_change (oferta apos salvar rotulos).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs

app = QApplication.instance() or QApplication([])


class _Status:
    canonical_exists = True
    review_exists = False


class _Janela:
    _offer_rerender_after_label_change = (
        rs.ReviewStudioWindow._offer_rerender_after_label_change)
    worker = None

    def __init__(self):
        self.context = object()
        self.steps = None

    def status_by_interview_id(self, interview_id):
        return _Status()

    def _render_source_overrides(self, interview_id):
        return {}

    def start_worker(self, label, steps, weights=None):
        self.steps = steps


chamadas: list[tuple[str, list[str]]] = []


def _fake_render(ctx, ids=None, overrides=None):
    chamadas.append(("render", list(ids or [])))


def _fake_refresh(ctx, ids):
    chamadas.append(("refresh", list(ids)))


janela = _Janela()
with patch.object(rs.QMessageBox, "question",
                  staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)):
    janela._offer_rerender_after_label_change(["E1", "E2"])
assert janela.steps is not None
# 2 renders (fonte de falantes decidida POR arquivo) + 1 refresh final
assert len(janela.steps) == 3, [s[0] for s in janela.steps]
with patch.object(rs.app_service, "render_interviews", _fake_render), \
     patch.object(rs.app_service, "refresh_unedited_reviews", _fake_refresh):
    for step in janela.steps:
        step[1]()
assert chamadas == [("render", ["E1"]), ("render", ["E2"]),
                    ("refresh", ["E1", "E2"])], chamadas
print("PASS: remontagem pos-rotulos = render por arquivo + refresh das reviews")

# Recusa da oferta: nenhum worker inicia
janela2 = _Janela()
with patch.object(rs.QMessageBox, "question",
                  staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)):
    janela2._offer_rerender_after_label_change(["E1"])
assert janela2.steps is None
print("PASS: recusar a oferta nao remonta nada")

print("PASS: toy_render_updates_review")
