"""Aba "Documentos" (Programa R, R2): a casa dos resultados.

Widget de APRESENTACAO pura: recebe entradas prontas (DocEntry) da
janela e emite sinais; nao conhece app_service nem caminhos do projeto.
Cada linha e um documento em um de tres estados (dossie RD, secao 3):
existe (data + Abrir + Mostrar na pasta), ausente ("ainda nao..." +
botao de gerar/exportar ali mesmo) ou gerando.

O pesquisador ve NOMES DE COISAS ("Resumo com temas"), nunca pastas
numeradas (05/06/07).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from . import ui_tokens


@dataclass(frozen=True)
class DocEntry:
    """Uma linha da aba Documentos."""
    chave: str                 # id estavel ("resumo", "export_docx", ...)
    titulo: str
    ai: bool = False           # ✨ AI assistiva (cor + selo)
    estado: str = "ausente"    # "existe" | "ausente" | "gerando"
    detalhe: str = ""          # "exportada em 21/08" / "ainda não gerado"
    caminho: str = ""          # arquivo para Abrir/Mostrar (quando existe)
    acao_rotulo: str = ""      # botao no estado ausente ("Exportar…")
    acao_chave: str = ""       # emitido em action_requested
    extras: tuple = field(default_factory=tuple)  # [(rotulo, acao_chave)]


class DocsPanel(QWidget):
    """Painel rolavel com as secoes "Desta entrevista" e "Do projeto"."""

    open_requested = Signal(str)             # caminho do arquivo
    show_in_folder_requested = Signal(str)   # caminho do arquivo
    action_requested = Signal(str)           # acao_chave

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._corpo = QWidget()
        self._corpo_layout = QVBoxLayout(self._corpo)
        self._corpo_layout.setContentsMargins(
            ui_tokens.SP_3, ui_tokens.SP_3, ui_tokens.SP_3, ui_tokens.SP_3)
        self._corpo_layout.setSpacing(ui_tokens.SP_3)
        self._scroll.setWidget(self._corpo)
        raiz.addWidget(self._scroll)
        self.set_sections(None, [], [])

    # -- montagem -----------------------------------------------------------
    def set_sections(
        self,
        entrevista_titulo: str | None,
        desta_entrevista: list[DocEntry],
        do_projeto: list[DocEntry],
    ) -> None:
        """Reconstroi o painel. entrevista_titulo=None = nada aberto."""
        while self._corpo_layout.count():
            item = self._corpo_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # setParent(None) IMEDIATO: deleteLater e diferido e o
                # widget orfao continuaria pintado por cima do conteudo
                # novo ate o event loop girar (visto na galeria da R2).
                w.setParent(None)
                w.deleteLater()

        if entrevista_titulo is None and not do_projeto:
            vazio = QLabel(
                "Abra uma entrevista para ver os documentos dela.\n"
                "Os documentos do projeto aparecem aqui também.")
            vazio.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vazio.setStyleSheet(
                f"color: {ui_tokens.TEXT_MUTED}; font-size: {ui_tokens.FONT_TITLE}px;")
            self._corpo_layout.addStretch(1)
            self._corpo_layout.addWidget(vazio)
            self._corpo_layout.addStretch(2)
            return

        if entrevista_titulo is not None:
            self._corpo_layout.addWidget(
                self._secao(f"DESTA ENTREVISTA ({entrevista_titulo})",
                            desta_entrevista))
        if do_projeto:
            self._corpo_layout.addWidget(self._secao("DO PROJETO", do_projeto))

        rodape = QHBoxLayout()
        nota = QLabel("Tudo isso fica na pasta Resultados do projeto.")
        nota.setStyleSheet(f"color: {ui_tokens.TEXT_MUTED};")
        botao_pasta = QPushButton("Mostrar na pasta Resultados")
        botao_pasta.clicked.connect(
            lambda: self.action_requested.emit("abrir_resultados"))
        rodape.addWidget(nota)
        rodape.addStretch(1)
        rodape.addWidget(botao_pasta)
        rodape_w = QWidget()
        rodape_w.setLayout(rodape)
        self._corpo_layout.addWidget(rodape_w)
        self._corpo_layout.addStretch(1)

    def _secao(self, titulo: str, entradas: list[DocEntry]) -> QWidget:
        caixa = QFrame()
        caixa.setStyleSheet(
            f"QFrame {{ background: {ui_tokens.BG_RAISED}; "
            f"border: 1px solid {ui_tokens.BORDER}; border-radius: 8px; }}")
        layout = QVBoxLayout(caixa)
        layout.setContentsMargins(0, 0, 0, ui_tokens.SP_1)
        layout.setSpacing(0)
        cab = QLabel(titulo)
        cab.setStyleSheet(
            f"color: {ui_tokens.TEXT_MUTED}; font-size: {ui_tokens.FONT_CAPTION}px; "
            f"letter-spacing: 1px; padding: {ui_tokens.SP_2}px {ui_tokens.SP_3}px; "
            f"border: none; border-bottom: 1px solid {ui_tokens.BORDER};")
        layout.addWidget(cab)
        for entrada in entradas:
            layout.addWidget(self._linha(entrada))
        return caixa

    def _linha(self, e: DocEntry) -> QWidget:
        linha = QWidget()
        linha.setObjectName(f"doc_{e.chave}")
        lay = QHBoxLayout(linha)
        lay.setContentsMargins(ui_tokens.SP_3, ui_tokens.SP_2,
                               ui_tokens.SP_3, ui_tokens.SP_2)
        titulo = QLabel(("✨ " if e.ai else "") + e.titulo)
        if e.ai:
            titulo.setStyleSheet(f"color: {ui_tokens.AI}; border: none;")
            titulo.setToolTip("AI local — nada sai do seu computador.")
        else:
            titulo.setStyleSheet("border: none;")
        lay.addWidget(titulo)
        lay.addStretch(1)
        if e.detalhe:
            detalhe = QLabel(e.detalhe)
            detalhe.setStyleSheet(
                f"color: {ui_tokens.TEXT_MUTED}; border: none; "
                f"font-size: {ui_tokens.FONT_CAPTION + 1}px;")
            lay.addWidget(detalhe)
        if e.estado == "existe" and e.caminho:
            abrir = QPushButton("Abrir")
            abrir.clicked.connect(
                lambda _c=False, p=e.caminho: self.open_requested.emit(p))
            lay.addWidget(abrir)
            mostrar = QPushButton("▸")
            mostrar.setToolTip("Mostrar na pasta")
            mostrar.setFixedWidth(28)
            mostrar.clicked.connect(
                lambda _c=False, p=e.caminho: self.show_in_folder_requested.emit(p))
            lay.addWidget(mostrar)
        elif e.estado == "gerando":
            gerando = QLabel("gerando…")
            gerando.setStyleSheet(f"color: {ui_tokens.INFO}; border: none;")
            lay.addWidget(gerando)
        elif e.acao_rotulo and e.acao_chave:
            botao = QPushButton(e.acao_rotulo)
            botao.clicked.connect(
                lambda _c=False, a=e.acao_chave: self.action_requested.emit(a))
            lay.addWidget(botao)
        for rotulo, acao in e.extras:
            extra = QPushButton(rotulo)
            extra.clicked.connect(
                lambda _c=False, a=acao: self.action_requested.emit(a))
            lay.addWidget(extra)
        return linha
