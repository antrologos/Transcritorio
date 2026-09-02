"""Toy: a causa real de uma falha do render chega ao usuario.

Incidente 2026-09-02: o dialogo dizia so "montando transcricao editavel...:
1 falha(s)." — a causa ("Missing WhisperX JSON") ia para o console. Agora
render_outputs anexa a causa legivel em `failure_log`, render_interviews a
devolve em JobResult.message e a GUI a acrescenta ao texto da falha
(failure_summary, pura).
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_service  # noqa: E402
from transcribe_pipeline.render import render_outputs  # noqa: E402

ID = "20260820-FMC2_MinSaude_Sonia Venancio"

with tempfile.TemporaryDirectory() as tmp:
    ctx = app_service.create_project(Path(tmp) / "p.transcricao", "toy")
    rows = [{"interview_id": ID, "selected": "true", "wav_path": ""}]

    # 1) render_outputs: sem failure_log continua devolvendo so o inteiro
    assert render_outputs(rows, ctx.config, ctx.paths, ids=[ID]) == 1

    # 2) com failure_log: causa legivel, com o id e a pasta
    log: list[str] = []
    assert render_outputs(rows, ctx.config, ctx.paths, ids=[ID], failure_log=log) == 1
    assert len(log) == 1 and ID in log[0] and "02_asr_raw" in log[0], log

    # 3) render_interviews: a causa vai no JobResult.message
    ctx2 = dataclasses.replace(ctx, rows=rows)
    result = app_service.render_interviews(ctx2, ids=[ID])
    assert result.failures == 1 and ID in result.message, result

    # 4) sucesso nao inventa mensagem (nenhuma entrevista selecionada)
    ok = app_service.render_interviews(dataclasses.replace(ctx, rows=[]), ids=[ID])
    assert ok.failures == 0 and ok.message == "", ok

# 5) texto da GUI (pura; depende de PySide6 para importar o modulo)
try:
    from transcribe_pipeline.review_studio_qt import failure_summary
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP (parte GUI): PySide6 ausente ({exc})")
else:
    assert failure_summary(1, "") == "1 falha(s)."
    assert failure_summary(1, None) == "1 falha(s)."  # type: ignore[arg-type]
    assert failure_summary(2, "  Não encontrei X.  ") == "2 falha(s). Não encontrei X."
    assert failure_summary(1, result.message) == f"1 falha(s). {result.message}"

print("PASS: toy_render_failure_message")
