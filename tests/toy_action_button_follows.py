"""Toy test: botao criado por action_button segue a QAction (SL-0).

Bug original: o botao so copiava texto/tooltip na criacao e chamava
action.trigger() no clique — QAction.trigger() dispara mesmo com a acao
desabilitada, entao os botoes da barra (Salvar, Exportar, Perguntar)
contornavam qualquer gate do update_action_states.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

app = QApplication.instance() or QApplication([])


class _Janela:
    """So o suficiente para exercitar o metodo real da janela."""
    action_button = ReviewStudioWindow.action_button
    _set_action = ReviewStudioWindow._set_action


janela = _Janela()

# --- o botao nasce espelhando a acao ---
acao = QAction("✨ Perguntar às entrevistas com AI...")
acao.setToolTip("Pergunte em linguagem natural.")
botao = janela.action_button(acao)
assert botao.isEnabled() is True
assert botao.text() == acao.text()
assert botao.toolTip() == acao.toolTip()

# --- desabilitar a acao desabilita o botao (o bug central) ---
acao.setEnabled(False)
assert botao.isEnabled() is False, "botao nao seguiu setEnabled(False) da acao"

# --- tooltip com motivo (via _set_action) chega ao botao ---
janela._set_action(acao, False, "abra um projeto")
assert "abra um projeto" in botao.toolTip(), botao.toolTip()

# --- reabilitar restaura botao e tooltip base ---
janela._set_action(acao, True)
assert botao.isEnabled() is True
assert botao.toolTip() == "Pergunte em linguagem natural."

# --- acao ja desabilitada na criacao -> botao nasce desabilitado ---
acao2 = QAction("x")
acao2.setEnabled(False)
botao2 = janela.action_button(acao2)
assert botao2.isEnabled() is False, "botao nasceu habilitado com acao desabilitada"

# --- primary preserva o estilo ---
acao3 = QAction("Transcrever")
botao3 = janela.action_button(acao3, primary=True)
assert botao3.isDefault() is True

print("PASS: toy_action_button_follows")
