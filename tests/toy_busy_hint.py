"""Toy: por que as acoes (AI inclusive) ficam cinza durante um lote (2026-09-02).

O usuario achou que as ferramentas de AI "nao vieram instaladas": durante o
lote elas ficavam desabilitadas com o motivo so no tooltip. Decisao: faixa na
lista + linha no menu Analisar + tooltip com o estado do lote, E os itens de AI
continuam clicaveis — o clique explica na faixa, sem modal, e nao executa.
Puras + janela offscreen com worker falso.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_busy_hint_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import (
        ReviewStudioWindow,
        busy_click_text,
        busy_hint_text,
        busy_reason_text,
    )
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

ESTADO = "Arquivo 3 de 5 · G03R_0719 · separando falantes · ~6 min restantes"

# --- puras ---
t = busy_hint_text(ESTADO)
assert t.startswith("⏳") and "Arquivo 3 de 5" in t and "voltam sozinhas" in t and "AI" in t, t
assert busy_hint_text("").startswith("⏳ Tarefa em andamento")
c = busy_click_text("Resumir a entrevista com AI", ESTADO)
assert "uma tarefa por vez" in c and "Em andamento: Arquivo 3 de 5" in c and "\"Resumir a entrevista com AI\"" in c
assert "não é preciso fazer nada" in c
assert "Em andamento" not in busy_click_text("X", "")
r = busy_reason_text(ESTADO)
assert r.startswith("Aguarde o lote terminar (Arquivo 3 de 5") and "voltam sozinhas" in r
assert busy_reason_text("") == "Aguarde a tarefa atual terminar — as ações voltam sozinhas."
assert " IA" not in (t + c + r) and "..." not in (t + c + r)
print("PASS: busy_hint_text / busy_click_text / busy_reason_text")

# --- janela: faixa, linha do menu, tooltips, clique explica e nao executa ---
from transcribe_pipeline import app_service  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp_home) / "proj.transcricao"
ctx = app_service.create_project(root, "lote")
write_manifest([{"interview_id": "E1", "selected": "true", "source_path": "midia/E1.m4a",
                 "source_ext": ".m4a", "wav_path": "Transcricoes/01_audio_wav16k_mono/E1.wav",
                 "status": "pending", "duration_sec": "60"}],
               ctx.paths.manifest_dir / "manifest.csv")
win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()

# ocioso: nada de faixa nem linha; AI habilitada (projeto aberto), sem nota de lote
assert not win.busy_hint_banner.isVisibleTo(win)
assert not win.busy_menu_hint_action.isVisible()
assert win.explore_action.isEnabled() and "Aguarde" not in win.explore_action.toolTip()

# lote rodando (worker falso) com o estado na barra de baixo
win.worker = SimpleNamespace(isRunning=lambda: True)
win.progress_label.setText(ESTADO)
win.update_action_states()
app.processEvents()
assert win.busy_hint_banner.isVisibleTo(win)
assert "Arquivo 3 de 5" in win.busy_hint_label.text() and "voltam sozinhas" in win.busy_hint_label.text()
assert win.busy_menu_hint_action.isVisible() and "Arquivo 3 de 5" in win.busy_menu_hint_action.text()
assert win.busy_menu_hint_separator.isVisible()
# acoes de AI CLICAVEIS, com o motivo no tooltip; Transcrever continua cinza com o mesmo motivo
for acao in (win.explore_action, win.glossario_action, win.spelling_action):
    assert acao.isEnabled(), acao.text()
    assert "Aguarde o lote terminar (Arquivo 3 de 5" in acao.toolTip(), acao.toolTip()
assert not win.transcribe_action.isEnabled()
assert "Aguarde o lote terminar (Arquivo 3 de 5" in win.transcribe_action.toolTip()
# Sem transcricao aberta o Resumir fica cinza pelo motivo PROPRIO ("Abra uma
# transcrição" aqui; no CI sem o modelo de análise, o motivo da capacidade) —
# nunca pelo lote.
assert not win.summarize_action.isEnabled()
assert win.summarize_action.toolTip() and "Aguarde" not in win.summarize_action.toolTip(), win.summarize_action.toolTip()
print("PASS: faixa + linha do menu + tooltips durante o lote")

# clique numa acao de AI durante o lote: explica na faixa, nao abre nada, nao inicia worker
def _boom(*a, **k):
    raise AssertionError("start_worker nao pode ser chamado durante o lote")
win.start_worker = _boom  # type: ignore[method-assign]
win.explore_action.trigger()
app.processEvents()
assert getattr(win, "_explore_dialog", None) is None, "abriu a janela Perguntar durante o lote"
assert "\"Buscar por sentido e perguntar\"" in win.busy_hint_label.text(), win.busy_hint_label.text()
assert "uma tarefa por vez" in win.busy_hint_label.text()
win.run_glossario_job()
assert "\"Glossário de nomes com AI\"" in win.busy_hint_label.text()
win.open_spelling_review()
assert "\"Revisar grafias de nomes\"" in win.busy_hint_label.text()
# enquanto pisca, o refresh do lote nao sobrescreve a resposta ao clique
win.update_action_states()
assert "Revisar grafias" in win.busy_hint_label.text()
print("PASS: clique durante o lote explica e nao executa")

# lote terminou: tudo volta
win.worker = SimpleNamespace(isRunning=lambda: False)
win._busy_click_active = False
win.update_action_states()
app.processEvents()
assert not win.busy_hint_banner.isVisibleTo(win)
assert not win.busy_menu_hint_action.isVisible() and not win.busy_menu_hint_separator.isVisible()
assert win.explore_action.isEnabled() and "Aguarde" not in win.explore_action.toolTip()
assert "Aguarde" not in win.transcribe_action.toolTip()
print("PASS: fim do lote limpa faixa, menu e tooltips")

print("PASS: toy_busy_hint")
