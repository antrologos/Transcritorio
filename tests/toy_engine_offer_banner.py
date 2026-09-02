"""Toy: faixa da lista que migra quem ja estava instalado para o TAGARELA.

2026-09-02: beta tester "atualizei mas continuou o Whisper small" — o
padrao persistido nao migra e a QMessageBox ao Transcrever era facil de
nao ver. Faixa visivel em maquina sem GPU com projeto no Whisper:
aceitar troca projeto + padrao da maquina (e baixa o modelo); recusar
grava a flag e some. Offscreen, ambiente redirecionado.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_tmp = tempfile.mkdtemp(prefix="engine_offer_")
os.environ["TRANSCRITORIO_HOME"] = _tmp
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp) / "models")
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "cpu"

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_service, app_settings, runtime
from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

app = QApplication.instance() or QApplication([])

# Sem GPU real nesta rodada: a faixa decide pelo device EFETIVO do
# runtime (torch); em maquina com CUDA a faixa nao deve aparecer. O toy
# forca CPU no projeto (asr_device: cpu) — resolve_device("cpu") = cpu.
root = Path(_tmp) / "proj.transcricao"
ctx = app_service.create_project(root, "faixa")
ctx = app_service.update_engine_config(ctx, {"asr_model": "small", "asr_device": "cpu"})

flag = runtime.app_data_dir() / "parakeet_cpu_offer_dismissed.flag"
assert not flag.exists()

win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()
# 2026-09-02: TAGARELA padrao em TODAS as maquinas — a faixa aparece em
# qualquer SO/device quando o projeto esta no Whisper.
assert win.engine_offer_banner.isVisibleTo(win), "faixa deveria aparecer: projeto no Whisper"
print("PASS: faixa aparece com projeto no Whisper")

# Recusar: flag gravada, faixa some e nao volta no proximo refresh
win._on_engine_offer_decline()
assert flag.exists()
assert not win.engine_offer_banner.isVisibleTo(win)
win.refresh_interviews()
app.processEvents()
assert not win.engine_offer_banner.isVisibleTo(win)
print("PASS: recusa e lembrada")

# Aceitar (sem download real: stub no gate de modelos)
flag.unlink()
win.refresh_interviews()
app.processEvents()
assert win.engine_offer_banner.isVisibleTo(win)
chamadas: list[dict] = []
win.ensure_models_ready = lambda **kw: chamadas.append(kw) or True  # type: ignore[method-assign]
win._on_engine_offer_accept()
app.processEvents()
assert win.context.config.get("asr_model") == "parakeet-pt"
assert win.context.config.get("asr_language") == "pt"        # projeto sem idioma -> pt
assert app_settings.asr_model_default() == "parakeet-pt"     # projetos novos herdam
assert chamadas and chamadas[0].get("asr_variants") == ["parakeet-pt"], chamadas
assert not win.engine_offer_banner.isVisibleTo(win)          # motor ja e o TAGARELA
print("PASS: aceitar troca projeto, padrao da maquina e pede o download")

# Escolha explicita de um Whisper (outro idioma): a oferta nao volta neste
# projeto durante a sessao, mesmo com o projeto de volta ao Whisper
assert win._switch_engine_to_whisper("small")
assert win.context.config.get("asr_model") == "small"
win.refresh_interviews()
app.processEvents()
assert not win.engine_offer_banner.isVisibleTo(win), "faixa voltou apos escolha explicita do Whisper"
# ...e projeto em outro idioma nunca ve a faixa
win._engine_user_choice.clear()
win.context = app_service.update_engine_config(win.context, {"asr_language": "es"})
win.refresh_interviews()
app.processEvents()
assert not win.engine_offer_banner.isVisibleTo(win), "faixa com projeto em espanhol"
win.context = app_service.update_engine_config(win.context, {"asr_language": "pt"})
win.refresh_interviews()
app.processEvents()
assert win.engine_offer_banner.isVisibleTo(win)
print("PASS: escolha explicita do Whisper e idioma fora do pt silenciam a oferta")
# de volta ao TAGARELA para o selo abaixo
assert win._switch_engine_to_parakeet(machine_default=False)

# Selo da statusbar mostra o modelo CONFIGURADO e diz "(não instalado)"
# quando outro modelo ja baixado seria usado no lugar (resolve_asr_model
# substitui em silencio; no cache vazio do toy nao ha substituto — simular).
from transcribe_pipeline import model_manager as _mm
assert "parakeet-pt" in win.project_header_text()
_orig = _mm.resolve_asr_model
_mm.resolve_asr_model = lambda key: "small"  # type: ignore[assignment]
try:
    assert "parakeet-pt (não instalado)" in win.project_header_text()
finally:
    _mm.resolve_asr_model = _orig
print("PASS: selo honesto")

print("PASS: toy_engine_offer_banner")
