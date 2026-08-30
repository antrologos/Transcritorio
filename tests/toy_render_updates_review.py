"""Toy test: render e reprocessar falantes tem EFEITO visivel (F6).

Bugs da checagem geral (2026-08-30): "Atualizar transcricao editavel"
so regravava canonical/MD/DOCX — a transcricao editavel (review) nunca
era atualizada, apesar do nome; e "Reprocessar falantes" rodava so a
diarizacao, sem render nem review: o par de acoes nunca mudava o que o
usuario ve. O passo correto ja existia (refresh_unedited_reviews, que
preserva edicoes humanas) e agora fecha os dois fluxos.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs

app = QApplication.instance() or QApplication([])


class _Janela:
    run_render_job = rs.ReviewStudioWindow.run_render_job

    def __init__(self):
        self.context = object()
        self.steps = None

    def save_current_turn(self, force=False):
        return True

    def selected_ids_for_job(self, fallback_current=False):
        return ["E1", "E2"]

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
janela.run_render_job()
assert janela.steps is not None
# 2 renders (um por arquivo) + 1 refresh das reviews no final
assert len(janela.steps) == 3, [s[0] for s in janela.steps]
with patch.object(rs.app_service, "render_interviews", _fake_render), \
     patch.object(rs.app_service, "refresh_unedited_reviews", _fake_refresh):
    for step in janela.steps:
        step[1]()
assert chamadas == [("render", ["E1"]), ("render", ["E2"]),
                    ("refresh", ["E1", "E2"])], chamadas
print("PASS: Atualizar transcricao editavel atualiza a review")

print("PASS: toy_render_updates_review")
