"""Casca da janela (Programa R, R1): QStatusBar e QToolBar reais.

Fabricas burras de proposito: recebem widgets JA CRIADOS pela janela e
apenas os posicionam. A janela mantem os nomes de atributo
(self.progress_bar etc.) — regra de ferro da migracao: os handlers do
PipelineWorker e o update_action_states escrevem por nome, e o aliasing
preserva tudo sem editar nenhum deles.

Convencao (dossie RD): estado EMBAIXO (statusbar: atividade a esquerda,
salvamento e selo do motor a direita), comando EM CIMA (toolbar fixa,
ordem = jornada de trabalho).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStatusBar, QToolBar, QWidget

from . import ui_tokens


def build_status_bar(
    parent: QWidget,
    *,
    activity_label: QWidget,
    progress_bar: QWidget,
    cancel_button: QWidget,
    save_label: QWidget,
    engine_badge: QWidget | None = None,
) -> QStatusBar:
    """Monta a barra de status: atividade/progresso/cancelar a esquerda
    (somem quando ociosos), salvamento e selo do motor fixos a direita."""
    bar = QStatusBar(parent)
    bar.setSizeGripEnabled(False)
    bar.setStyleSheet(
        f"QStatusBar {{ border-top: 1px solid {ui_tokens.BORDER}; }} "
        "QStatusBar::item { border: none; }")
    progress_bar.setMaximumWidth(280)
    bar.addWidget(activity_label, 1)
    bar.addWidget(progress_bar)
    bar.addWidget(cancel_button)
    bar.addPermanentWidget(save_label)
    if engine_badge is not None:
        bar.addPermanentWidget(engine_badge)
    return bar


def build_tool_bar(parent: QWidget) -> QToolBar:
    """Toolbar principal: fixa (sem arrastar/flutuar/ocultar), texto puro.

    Botoes adicionados via addAction ESPELHAM a QAction nativamente
    (rotulo, tooltip, enabled) — um ponto de verdade so, sem a copia
    manual que o action_button fazia.
    """
    bar = QToolBar("Ações principais", parent)
    bar.setObjectName("main_toolbar")
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    bar.setStyleSheet(
        f"QToolBar {{ border-bottom: 1px solid {ui_tokens.BORDER}; "
        f"padding: {ui_tokens.SP_1}px {ui_tokens.SP_2}px; "
        f"spacing: {ui_tokens.SP_2}px; }}")
    return bar
