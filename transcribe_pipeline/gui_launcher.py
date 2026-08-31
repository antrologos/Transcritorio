"""Entrada leve da GUI (v0.2): feedback imediato + instancia unica.

Problema real relatado (2026-08-23): o app demorava varios segundos sem
NENHUM feedback (imports de PySide6/numpy/etc.), o usuario clicava de novo e
duas janelas abriam. Este modulo importa apenas o essencial do Qt, resolve os
dois problemas e SO ENTAO importa o resto do app (com o splash na tela):

1. Instancia unica via QLocalServer/QLocalSocket: o segundo clique conecta ao
   socket local da primeira instancia, pede "activate" (a janela existente
   vem para frente) e sai em ~1s.
2. Splash imediato: "Transcritório — abrindo..." aparece antes dos imports
   pesados, que sao a maior parte da espera.
"""
from __future__ import annotations

_SINGLE_INSTANCE_KEY = "TranscritorioSingleInstance"


def _splash_pixmap():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    # Identidade do Programa R: paleta unica em ui_tokens (modulo puro,
    # importa em ~0 ms — nao atrasa o splash).
    from . import ui_tokens

    pixmap = QPixmap(440, 170)
    pixmap.fill(QColor(ui_tokens.BG_BASE))
    painter = QPainter(pixmap)
    painter.setPen(QColor(ui_tokens.ACCENT))
    title_font = QFont()
    title_font.setPointSize(21)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(pixmap.rect().adjusted(28, 34, -28, -80), Qt.AlignmentFlag.AlignLeft, "Transcritório")
    subtitle_font = QFont()
    subtitle_font.setPointSize(10)
    painter.setFont(subtitle_font)
    painter.setPen(QColor(ui_tokens.TEXT_MUTED))
    painter.drawText(
        pixmap.rect().adjusted(28, 96, -28, -28),
        Qt.AlignmentFlag.AlignLeft,
        "Abrindo…\nIsso pode levar alguns segundos.",
    )
    painter.end()
    return pixmap


def _request_activate() -> bool:
    """Tenta acordar uma instancia ja aberta. True = existe, ja foi acordada."""
    from PySide6.QtNetwork import QLocalSocket

    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_INSTANCE_KEY)
    if not probe.waitForConnected(300):
        return False
    probe.write(b"activate")
    probe.flush()
    probe.waitForBytesWritten(300)
    probe.disconnectFromServer()
    return True


def main() -> int:
    import sys

    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication, QSplashScreen

    app = QApplication(sys.argv)
    app.setApplicationName("Transcritorio")

    if _request_activate():
        return 0
    QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)  # socket orfao de crash anterior
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_KEY):
        # Corrida rara: outra instancia ganhou o listen entre o probe e aqui.
        if _request_activate():
            return 0
        server = None  # segue sem instancia unica — abrir e melhor que falhar

    splash = None
    import os
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        splash = QSplashScreen(_splash_pixmap())
        splash.show()
        app.processEvents()

    # Imports pesados acontecem AGORA, com o splash visivel.
    from . import review_studio_qt

    return review_studio_qt.main(splash=splash, single_instance_server=server)


if __name__ == "__main__":
    raise SystemExit(main())
