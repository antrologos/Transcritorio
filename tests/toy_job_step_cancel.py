"""Toy test: cancelar um job nao marca o arquivo como "Falha" (F5).

Bug da revisao adversarial (2026-08-30): o cancelamento chegava ao
whisperx como "1 falha(s)" e o job_step gravava status "Falha" com
last_error — e o novo friendly_state passou a EXIBIR isso na coluna
Transcricao para uma acao que o proprio usuario pediu.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
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
    job_step = rs.ReviewStudioWindow.job_step

    def __init__(self):
        self.context = SimpleNamespace(jobs={})


gravados: list[dict] = []


def _fake_update_job(ctx, iid, data):
    gravados.append(dict(data))
    return ctx


def _boom(progress, should_cancel):
    raise RuntimeError("explodiu")


def _roda(should_cancel):
    gravados.clear()
    janela = _Janela()
    step = janela.job_step("msg", "E1", "transcrever", 0, 50, _boom,
                           accepts_progress=True)
    run = step[1]
    with patch.object(rs.app_service, "update_job", _fake_update_job):
        try:
            run(lambda d: None, should_cancel)
        except RuntimeError:
            pass
    return gravados[-1]


# cancelamento pedido: o job volta a Pendente, sem last_error
final = _roda(lambda: True)
assert final["status"] == "Pendente", final
assert not final.get("last_error"), final
print("PASS: cancelar volta o job a Pendente")

# falha real (sem cancelamento): continua Falha com last_error
final = _roda(lambda: False)
assert final["status"] == "Falha", final
assert final.get("last_error"), final
print("PASS: falha real continua Falha")

print("PASS: toy_job_step_cancel")
