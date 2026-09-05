"""Janela de ajuda: consultar o que o app faz, sem sair do que se estava fazendo.

Ate 2026-09-05 nao havia NENHUMA tela onde consultar os comandos — quem
esquecia uma tecla so podia caçá-la nos menus. Esta janela responde as
tres perguntas do usuario ("como sei / como aprendo / como consulto") na
que e mais barata de responder bem: consultar.

Widget de APRESENTACAO pura, no molde de ui_docs_panel: recebe a lista de
Comando ja pronta e nao conhece app_service, projeto nem barra de menus. A
extracao mora em comandos.py, que e puro e testado sem Qt.

NAO e modal de proposito: consulta-se um atalho ENQUANTO se revisa.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from . import comandos as _comandos
from . import ui_tokens

SITE = "https://antrologos.github.io/Transcritorio/pt/"


def texto_do_manual() -> str:
    """O manual curto que viaja no wheel (assets/manual.md).

    Vem embutido de proposito: e o manual da VERSAO que a pessoa esta
    rodando, e abre sem internet — o site tem a versao longa, com imagens.
    Ausente (canal PyInstaller legado, que copia so os assets da raiz do
    repositorio), a aba degrada com um recado e o botao do site; nunca
    quebra.
    """
    bases = []
    empacotado = getattr(sys, "_MEIPASS", "")
    if empacotado:
        bases.append(Path(empacotado) / "assets")
    bases.append(Path(__file__).resolve().parent / "assets")
    for base in bases:
        alvo = base / "manual.md"
        try:
            if alvo.exists():
                return alvo.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


class HelpWindow(QDialog):
    """Abas de ajuda. Hoje: "Atalhos e comandos"."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajuda do Transcritório")
        self.setModal(False)
        self.resize(900, 620)
        self._comandos: list = []
        self._versao = ""
        self._abas: dict[str, int] = {}

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(ui_tokens.SP_3, ui_tokens.SP_3,
                                ui_tokens.SP_3, ui_tokens.SP_3)
        self._tabs = QTabWidget()
        raiz.addWidget(self._tabs)
        self._abas["manual"] = self._tabs.addTab(self._aba_manual(),
                                                 "Como usar")
        self._abas["atalhos"] = self._tabs.addTab(self._aba_comandos(),
                                                  "Atalhos e comandos")

    # --------------------------------------------------------------- manual
    def _aba_manual(self) -> QWidget:
        pagina = QWidget()
        col = QVBoxLayout(pagina)
        col.setContentsMargins(0, ui_tokens.SP_2, 0, 0)
        col.setSpacing(ui_tokens.SP_2)

        texto = texto_do_manual()
        self.manual_view = QTextBrowser()
        self.manual_view.setOpenExternalLinks(True)
        if texto:
            self.manual_view.setMarkdown(texto)
        else:
            self.manual_view.setPlainText(
                "O manual não veio nesta instalação.\n\n"
                "Ele está no site do Transcritório, no botão abaixo.")
        col.addWidget(self.manual_view, 1)

        rodape = QHBoxLayout()
        rodape.addStretch(1)
        self.site_button = QPushButton("Abrir o manual completo no site")
        self.site_button.setToolTip(
            "Abre o navegador no manual do Transcritório, com imagens e mais detalhes.")
        self.site_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SITE)))
        rodape.addWidget(self.site_button)
        col.addLayout(rodape)
        return pagina

    # ------------------------------------------------------------------ aba
    def _aba_comandos(self) -> QWidget:
        pagina = QWidget()
        col = QVBoxLayout(pagina)
        col.setContentsMargins(0, ui_tokens.SP_2, 0, 0)
        col.setSpacing(ui_tokens.SP_2)

        self.busca = QLineEdit()
        self.busca.setClearButtonEnabled(True)
        self.busca.setPlaceholderText(
            "Digite parte do comando ou uma tecla — por exemplo Alt+P")
        self.busca.textChanged.connect(self._repovoar)
        col.addWidget(self.busca)

        self.arvore = QTreeWidget()
        self.arvore.setColumnCount(3)
        self.arvore.setHeaderLabels(["Comando", "Tecla", "O que faz"])
        self.arvore.setRootIsDecorated(False)
        self.arvore.setAlternatingRowColors(True)
        self.arvore.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.arvore.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cabecalho = self.arvore.header()
        cabecalho.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        cabecalho.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        cabecalho.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.arvore.setColumnWidth(0, 300)
        self.arvore.setColumnWidth(1, 150)
        col.addWidget(self.arvore, 1)

        rodape = QHBoxLayout()
        self.contagem = QLabel("")
        self.contagem.setStyleSheet(
            f"color: {ui_tokens.TEXT_MUTED}; font-size: {ui_tokens.FONT_CAPTION}px;")
        rodape.addWidget(self.contagem)
        rodape.addStretch(1)
        self.copiar_button = QPushButton("Copiar lista")
        self.copiar_button.setToolTip(
            "Copia a lista inteira como texto, para colar onde quiser.")
        self.copiar_button.clicked.connect(self._copiar)
        rodape.addWidget(self.copiar_button)
        self.salvar_button = QPushButton("Salvar como texto…")
        self.salvar_button.setToolTip(
            "Grava a lista num arquivo de texto — dá para imprimir e deixar ao lado.")
        self.salvar_button.clicked.connect(self._salvar)
        rodape.addWidget(self.salvar_button)
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(self.close)
        rodape.addWidget(fechar)
        col.addLayout(rodape)
        return pagina

    # ----------------------------------------------------------------- dados
    def set_comandos(self, lista, versao: str = "") -> None:
        """Recebe o catalogo ja extraido do menu. Unica entrada de dados."""
        self._comandos = list(lista)
        self._versao = versao or ""
        self._repovoar()

    def _visiveis(self) -> list:
        return _comandos.filtrar(self._comandos, self.busca.text())

    def _repovoar(self) -> None:
        achados = self._visiveis()
        self.arvore.clear()
        grupo_atual = None
        pai = None
        for cmd in achados:
            if cmd.caminho != grupo_atual or pai is None:
                grupo_atual = cmd.caminho
                pai = QTreeWidgetItem(self.arvore, [cmd.caminho or "Outros comandos"])
                fonte = pai.font(0)
                fonte.setBold(True)
                pai.setFont(0, fonte)
                pai.setFirstColumnSpanned(True)
                pai.setFlags(Qt.ItemFlag.ItemIsEnabled)
            rotulo = f"{cmd.rotulo} (liga/desliga)" if cmd.chave else cmd.rotulo
            teclas = " / ".join(cmd.atalhos_exibidos or cmd.atalhos) or "—"
            # So a primeira linha na coluna: os tooltips de AI tem 6 linhas
            # e esticariam a altura de todas as linhas da tabela. O texto
            # inteiro fica no tooltip do item.
            linhas = [ln for ln in cmd.dica.split("\n") if ln.strip()]
            resumo = linhas[0] if linhas else ""
            if len(linhas) > 1:
                resumo = f"{resumo} …"
            item = QTreeWidgetItem(pai, [rotulo, teclas, resumo])
            if cmd.dica:
                item.setToolTip(2, cmd.dica)
        self.arvore.expandAll()
        com_tecla = sum(1 for c in achados if c.atalhos)
        total = len(self._comandos)
        if len(achados) == total:
            self.contagem.setText(f"{total} comandos, {com_tecla} com tecla de atalho.")
        else:
            self.contagem.setText(
                f"{len(achados)} de {total} comandos, {com_tecla} com tecla de atalho.")

    # ----------------------------------------------------------------- saida
    def _texto_atual(self) -> str:
        hoje = datetime.date.today().strftime("%d/%m/%Y")
        return _comandos.texto_da_cola(self._visiveis(), versao=self._versao, data=hoje)

    def _copiar(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._texto_atual())
        self.contagem.setText("Lista copiada. Cole onde quiser.")

    def _salvar(self) -> None:
        caminho, _filtro = QFileDialog.getSaveFileName(
            self, "Salvar a lista de comandos", "atalhos-transcritorio.txt",
            "Arquivo de texto (*.txt)")
        if not caminho:
            return
        from pathlib import Path

        Path(caminho).write_text(self._texto_atual(), encoding="utf-8")
        self.contagem.setText(f"Lista salva em {caminho}")

    # ------------------------------------------------------------------ abas
    def mostrar_aba(self, chave: str) -> None:
        indice = self._abas.get(chave)
        if indice is not None:
            self._tabs.setCurrentIndex(indice)

    def showEvent(self, event) -> None:  # noqa: N802 (nome do Qt)
        super().showEvent(event)
        # Quem abre a janela quase sempre vem procurar UMA coisa.
        self.busca.setFocus(Qt.FocusReason.OtherFocusReason)
        self.busca.selectAll()
