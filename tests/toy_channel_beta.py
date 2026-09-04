"""Toy: canal de teste (beta) separado da versão estável — 2026-09-03.

Duas garantias, para as duas versões conviverem na mesma máquina:
- `gui_launcher._SINGLE_INSTANCE_KEY` é POR CANAL (sem isso, o atalho da beta
  só acordaria a janela da estável em vez de abrir);
- o título da janela diz "versão de teste" — e diz **em todos os caminhos**
  que reescrevem o título (com projeto aberto e sem projeto), não só na
  construção da janela.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.mkdtemp(prefix="toy_channel_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp) / "appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("TRANSCRITORIO_CHANNEL", None)

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline import review_studio_qt as rs
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import gui_launcher  # noqa: E402

# --- chave da instancia unica: por canal ---
assert gui_launcher._SINGLE_INSTANCE_KEY == "TranscritorioSingleInstance"
os.environ["TRANSCRITORIO_CHANNEL"] = "beta"
importlib.reload(gui_launcher)
assert gui_launcher._SINGLE_INSTANCE_KEY == "TranscritorioSingleInstance-beta"
os.environ["TRANSCRITORIO_CHANNEL"] = "b e/t\\a!"      # so alfanumerico entra na chave
importlib.reload(gui_launcher)
assert gui_launcher._SINGLE_INSTANCE_KEY == "TranscritorioSingleInstance-beta", \
    gui_launcher._SINGLE_INSTANCE_KEY
os.environ.pop("TRANSCRITORIO_CHANNEL")
importlib.reload(gui_launcher)
assert gui_launcher._SINGLE_INSTANCE_KEY == "TranscritorioSingleInstance"
print("PASS: instancia unica por canal")

# --- sufixo do titulo ---
assert rs.channel_suffix("") == ""
assert rs.channel_suffix(None) == "", "sem a variavel de ambiente, nada muda"
sufixo = rs.channel_suffix("beta")
assert sufixo.startswith(" — versão de teste (beta ") and sufixo.endswith(")"), sufixo
print("PASS: channel_suffix")

# --- a janela REAL: o titulo e reescrito ao abrir/fechar projeto ---
app = QApplication.instance() or QApplication([])
os.environ["TRANSCRITORIO_CHANNEL"] = "beta"
win = rs.ReviewStudioWindow()
app.processEvents()
assert "versão de teste" in win.windowTitle(), win.windowTitle()
win._update_project_label()          # caminho SEM projeto
assert "versão de teste" in win.windowTitle(), win.windowTitle()

from transcribe_pipeline import app_service  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

root = Path(_tmp) / "proj.transcricao"
ctx = app_service.create_project(root, "Meu estudo")
write_manifest([], ctx.paths.manifest_dir / "manifest.csv")
win.switch_project_context(ctx)      # caminho COM projeto
app.processEvents()
assert "Meu estudo" in win.windowTitle() and "versão de teste" in win.windowTitle(), win.windowTitle()

os.environ.pop("TRANSCRITORIO_CHANNEL")
outra = rs.ReviewStudioWindow()
assert "versão de teste" not in outra.windowTitle(), outra.windowTitle()
print("PASS: titulo da janela marca o canal (com e sem projeto)")

print("PASS: toy_channel_beta")
