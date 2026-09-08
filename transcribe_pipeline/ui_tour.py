"""Volta guiada: o painel de passos e o halo que aponta o lugar real.

Apresentacao pura (molde de ui_help/ui_docs_panel): recebe os passos de
volta_guiada.PASSOS e um "resolvedor" que devolve o widget-alvo de cada
passo; nao conhece app_service nem projeto. A janela principal liga os
sinais e decide aba/entrevista.

Por que NAO um QWizard: um assistente modal trava a janela — e a volta
existe justamente para apontar a janela viva. O painel e uma ferramenta
(Qt.Tool) que fica por cima sem bloquear, e o halo e um QFrame filho da
janela principal, transparente a cliques, com a borda em ACCENT sobre a
geometria do alvo. Redimensionou ou moveu a janela? O halo acompanha.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from . import ui_tokens
from .volta_guiada import Passo, rotulo_contador

# O widget-alvo de um passo, ou None quando nao ha o que apontar.
Resolvedor = Callable[[Passo], "tuple[QWidget | None, str]"]


class Halo(QFrame):
    """Borda em volta do alvo. Filho da janela principal, nunca captura o mouse."""

    MARGEM = 6

    def __init__(self, janela: QWidget) -> None:
        super().__init__(janela)
        self._janela = janela
        self._alvo: QWidget | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            f"QFrame {{ border: 3px solid {ui_tokens.ACCENT}; border-radius: 8px; "
            "background: transparent; }}")
        self.hide()
        janela.installEventFilter(self)

    def cobrir(self, alvo: QWidget | None) -> None:
        self._alvo = alvo
        if alvo is None:
            self.hide()
            return
        self._reposicionar()
        self.show()
        self.raise_()

    def _reposicionar(self) -> None:
        alvo = self._alvo
        if alvo is None:
            return
        canto = alvo.mapTo(self._janela, QPoint(0, 0))
        m = self.MARGEM
        self.setGeometry(QRect(canto, alvo.size()).adjusted(-m, -m, m, m))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (nome do Qt)
        if obj is self._janela and event.type() in (QEvent.Type.Resize, QEvent.Type.Move) \
                and self.isVisible():
            self._reposicionar()
        return False


class TourPanel(QDialog):
    """O cartao flutuante: titulo, texto, '3 de 8', Anterior / Próximo / Fechar."""

    passo_mudou = Signal(int)
    encerrado = Signal()

    def __init__(self, parent: QWidget | None, passos: tuple[Passo, ...]) -> None:
        super().__init__(parent)
        self._passos = passos
        self._indice = 0
        self.setModal(False)
        self.setWindowTitle("Volta guiada")
        # Ferramenta: fica por cima da janela principal sem trava-la.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.CustomizeWindowHint
                            | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setMinimumWidth(380)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(ui_tokens.SP_4, ui_tokens.SP_3, ui_tokens.SP_4, ui_tokens.SP_3)
        raiz.setSpacing(ui_tokens.SP_2)
        self.contador = QLabel("")
        self.contador.setStyleSheet(
            f"color: {ui_tokens.TEXT_MUTED}; font-size: {ui_tokens.FONT_CAPTION}px;")
        raiz.addWidget(self.contador)
        self.titulo = QLabel("")
        self.titulo.setStyleSheet(f"font-size: {ui_tokens.FONT_TITLE}px; font-weight: 600;")
        self.titulo.setWordWrap(True)
        raiz.addWidget(self.titulo)
        self.texto = QLabel("")
        self.texto.setWordWrap(True)
        self.texto.setTextFormat(Qt.TextFormat.PlainText)
        raiz.addWidget(self.texto)
        self.nota = QLabel("")
        self.nota.setWordWrap(True)
        self.nota.setStyleSheet(f"color: {ui_tokens.TEXT_MUTED};")
        raiz.addWidget(self.nota)

        botoes = QHBoxLayout()
        self.anterior_button = QPushButton("← Anterior")
        self.anterior_button.clicked.connect(lambda: self.ir_para(self._indice - 1))
        botoes.addWidget(self.anterior_button)
        botoes.addStretch(1)
        self.fechar_button = QPushButton("Fechar")
        self.fechar_button.clicked.connect(self.close)
        botoes.addWidget(self.fechar_button)
        self.proximo_button = QPushButton("Próximo →")
        self.proximo_button.setDefault(True)
        self.proximo_button.clicked.connect(self._avancar)
        botoes.addWidget(self.proximo_button)
        raiz.addLayout(botoes)

    # ------------------------------------------------------------ navegacao
    @property
    def indice(self) -> int:
        return self._indice

    def passo_atual(self) -> Passo:
        return self._passos[self._indice]

    def ir_para(self, indice: int) -> None:
        self._indice = max(0, min(len(self._passos) - 1, indice))
        passo = self.passo_atual()
        self.contador.setText(rotulo_contador(self._indice))
        self.titulo.setText(passo.titulo)
        self.texto.setText(passo.texto)
        self.anterior_button.setEnabled(self._indice > 0)
        ultimo = self._indice == len(self._passos) - 1
        self.proximo_button.setText("Concluir" if ultimo else "Próximo →")
        self.passo_mudou.emit(self._indice)

    def _avancar(self) -> None:
        if self._indice >= len(self._passos) - 1:
            self.close()
            return
        self.ir_para(self._indice + 1)

    def mostrar_nota(self, texto: str) -> None:
        """A frase de reserva (Estudio fechado), ou vazio."""
        self.nota.setText(texto)
        self.nota.setVisible(bool(texto))

    def closeEvent(self, event) -> None:  # noqa: N802 (nome do Qt)
        self.encerrado.emit()
        super().closeEvent(event)
