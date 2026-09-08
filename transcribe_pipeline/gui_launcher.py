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

import os as _os

# Instancia unica POR CANAL: a versao de teste (beta) e a estavel tem de
# poder ficar abertas ao mesmo tempo na mesma maquina — com uma chave so, o
# atalho da beta apenas acordaria a janela da estavel. O canal vem do
# lancador (`TRANSCRITORIO_CHANNEL=beta`); sem ele, o comportamento e o de
# sempre. A chave NAO leva a versao de proposito: duas versoes do MESMO
# canal precisam se enxergar para o aviso de "build antigo" funcionar.
_CHANNEL = "".join(c for c in _os.environ.get("TRANSCRITORIO_CHANNEL", "") if c.isalnum())[:16]
_SINGLE_INSTANCE_KEY = "TranscritorioSingleInstance" + (f"-{_CHANNEL}" if _CHANNEL else "")


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


_IDENTITY_CACHE: str | None = None


def _instance_identity() -> str:
    """Identidade DESTE PROCESSO para o protocolo de instancia unica.

    A versao vem do wheel instalado (importlib.metadata — __version__
    pode divergir e __build__ fica "dev" no canal uv/PyPI) + __build__
    (local-<sha> nos wheels locais, timestamp no frozen legado).
    CONGELADA na primeira chamada (auditoria 2026-09-01): apos um
    `uv tool upgrade`, o disco ja tem a versao NOVA, e uma janela
    antiga que recomputasse a cada ping se declararia identica ao
    processo novo — anulando o aviso exatamente no cenario que o
    motivou. A identidade descreve quem esta RODANDO, nao o disco."""
    global _IDENTITY_CACHE
    if _IDENTITY_CACHE is None:
        from . import __build__, __version__
        try:
            from importlib.metadata import version
            pkg = version("transcritorio")
        except Exception:  # noqa: BLE001 - rodando da fonte sem instalacao
            pkg = __version__
        _IDENTITY_CACHE = f"{pkg}+{__build__}"
    return _IDENTITY_CACHE


def _stale_decision(resposta: bytes, still_connected: bool) -> bool:
    """True = a janela aberta e de um build pre-protocolo (pura).

    Servidor pre-protocolo ACEITA e FECHA sem ler nem responder: o
    cliente ve o socket desconectado sem resposta. Timeout com o
    socket AINDA conectado significa janela ocupada (ex.: abrindo,
    imports pesados) — nao e prova de versao antiga; avisar ai era um
    falso positivo (auditoria 2026-09-01)."""
    return not resposta.strip() and not still_connected


def _warn_stale_window() -> None:
    """Aviso da 2a instancia (a que morre): a janela aberta e de um build
    que nem entende o protocolo — anterior ao instalado com certeza."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("Versão mais nova instalada")
    box.setText(
        "A janela aberta do Transcritório parece ser de uma versão "
        "anterior à que está instalada neste computador.\n\n"
        "Feche aquela janela e abra o aplicativo de novo para usar a "
        "versão atual.")
    box.exec()


def _request_activate(key: str = _SINGLE_INSTANCE_KEY, on_stale=None) -> bool:
    """Tenta acordar uma instancia ja aberta. True = existe, ja foi acordada.

    Envia "activate <identidade>" e espera a identidade do servidor de
    volta. Divisao do aviso de build antigo (R4): se o servidor RESPONDE
    diferente, ele mesmo avisa na janela aberta; se NAO responde, e um
    build pre-protocolo (anterior por definicao) e `on_stale` e chamado
    AQUI, na instancia nova — unico jeito de avisar quando a janela
    aberta e velha demais para ler o payload. Servidores antigos fecham
    sem ler: o payload extra e inofensivo para eles.

    `key` parametrizada para os toys usarem um socket proprio — com o app
    REAL aberto na maquina, o probe na chave global encontraria a janela
    de verdade e o teste viraria refem do ambiente."""
    from PySide6.QtNetwork import QLocalSocket

    probe = QLocalSocket()
    probe.connectToServer(key)
    if not probe.waitForConnected(300):
        return False
    minha = _instance_identity()
    probe.write(f"activate {minha}".encode("utf-8"))
    probe.flush()
    probe.waitForBytesWritten(300)
    resposta = b""
    if probe.waitForReadyRead(1500):
        resposta = bytes(probe.readAll())
    still_connected = (
        probe.state() == QLocalSocket.LocalSocketState.ConnectedState)
    probe.disconnectFromServer()
    if on_stale is not None and _stale_decision(resposta, still_connected):
        on_stale()
    return True


def main() -> int:
    import sys

    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication, QSplashScreen

    app = QApplication(sys.argv)
    app.setApplicationName("Transcritorio")

    if _request_activate(on_stale=_warn_stale_window):
        return 0
    QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)  # socket orfao de crash anterior
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_KEY):
        # Corrida rara: outra instancia ganhou o listen entre o probe e aqui.
        if _request_activate(on_stale=_warn_stale_window):
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
