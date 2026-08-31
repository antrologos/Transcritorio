"""Toy R1: fabricas da casca (ui_shell) — statusbar e toolbar reais."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow,
                               QProgressBar, QPushButton, QToolButton)

from transcribe_pipeline import ui_shell

app = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------- statusbar
win = QMainWindow()
atividade = QLabel("Pronto.")
barra = QProgressBar()
cancelar = QPushButton("Cancelar")
salvo = QLabel("Salvo.")
selo = QLabel("Motor: CPU")
sb = ui_shell.build_status_bar(
    win, activity_label=atividade, progress_bar=barra,
    cancel_button=cancelar, save_label=salvo, engine_badge=selo)
win.setStatusBar(sb)
assert win.statusBar() is sb
assert not sb.isSizeGripEnabled()
# widgets sobrevivem no statusbar (parentesco correto)
for w in (atividade, barra, cancelar, salvo, selo):
    assert w.parent() is not None, w
assert barra.maximumWidth() <= 280, "barra de progresso deve ser compacta"
print("PASS: build_status_bar posiciona os widgets")

# selo opcional
sb2 = ui_shell.build_status_bar(
    QMainWindow(), activity_label=QLabel(), progress_bar=QProgressBar(),
    cancel_button=QPushButton(), save_label=QLabel(), engine_badge=None)
assert sb2 is not None
print("PASS: engine_badge opcional")

# ---------------------------------------------------------------- toolbar
win2 = QMainWindow()
tb = ui_shell.build_tool_bar(win2)
win2.addToolBar(tb)
assert not tb.isMovable() and not tb.isFloatable()
assert tb.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly
assert tb.contextMenuPolicy() == Qt.ContextMenuPolicy.PreventContextMenu

acao = QAction("Salvar", win2)
acao.setToolTip("Salvar a transcrição.")
tb.addAction(acao)
botao = tb.widgetForAction(acao)
assert isinstance(botao, QToolButton)
assert botao.text() == "Salvar"
# espelhamento nativo: desabilitar a acao desabilita o botao NA HORA
acao.setEnabled(False)
assert not botao.isEnabled()
acao.setEnabled(True)
assert botao.isEnabled()
# mudou o tooltip da acao -> botao acompanha
acao.setToolTip("Outro tooltip")
assert botao.toolTip() == "Outro tooltip"
print("PASS: build_tool_bar espelha a QAction nativamente")

print("PASS: toy_ui_shell")
