"""Toy test para gui_launcher (partida com feedback + instancia unica).

Valida offscreen: pixmap do splash, probe sem instancia (False) e probe com
um QLocalServer escutando (True = segunda instancia seria barrada e a
primeira acordada). Skip no CI minimo sem PySide6.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication

    from transcribe_pipeline import gui_launcher
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

app = QApplication.instance() or QApplication([])

pixmap = gui_launcher._splash_pixmap()
assert not pixmap.isNull() and pixmap.width() > 0
print("PASS: splash pixmap gerado")

# Chave PROPRIA do teste: na chave global, um Transcritorio real aberto
# na maquina faria o probe achar a janela de verdade (flake ambiental).
key = f"TranscritorioToyLauncher-{os.getpid()}"
QLocalServer.removeServer(key)
assert gui_launcher._request_activate(key) is False
print("PASS: sem instancia aberta -> probe False (abre normalmente)")

server = QLocalServer()
assert server.listen(key), server.errorString()
assert gui_launcher._request_activate(key) is True
print("PASS: com instancia escutando -> probe True (segunda instancia sai)")

server.close()
QLocalServer.removeServer(key)

print()
print("PASS: toy_gui_launcher")
