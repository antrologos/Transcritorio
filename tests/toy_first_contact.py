"""Toy R4-c5: primeiro contato sem becos.

Cobre: (1) drag-and-drop SEM projeto aberto deixa de ser ignorado em
silencio — o dragEnter aceita e o drop oferece criar o projeto e
ingere os arquivos em seguida; (2) a enfase da toolbar caminha com a
jornada: sem midia -> Adicionar primary; midia toda pendente ->
Transcrever primary; projeto andando -> toolbar neutra.
"""
from __future__ import annotations

import csv
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])

from transcribe_pipeline import review_studio_qt as rs
from transcribe_pipeline import ui_tokens

# ------------------------------------------------ janela SEM projeto
win = rs.ReviewStudioWindow()
app.processEvents()
assert win.context is None

mime = QMimeData()
mime.setUrls([QUrl.fromLocalFile(r"C:\tmp\entrevista.mp3")])
evento = QDragEnterEvent(
    QPoint(10, 10), Qt.DropAction.CopyAction, mime,
    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
win.dragEnterEvent(evento)
assert evento.isAccepted(), "drop sem projeto deve ser ACEITO (nao ignorado)"
print("PASS: dragEnter aceita arquivos mesmo sem projeto")

# ------------------------------------------------ oferta de criar projeto
chamadas: list[str] = []

# Recusa: nada acontece
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
win._offer_project_for_dropped([Path(r"C:\tmp\entrevista.mp3")])
assert win.context is None and not chamadas
print("PASS: recusa da oferta nao cria nada")

# Aceite: new_project roda e, se criar contexto, os paths sao ingeridos
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
win.new_project = lambda *a, **k: chamadas.append("new_project") or setattr(
    win, "context", object())
win._ingest_media_paths = lambda paths: chamadas.append(f"ingest:{len(paths)}")
win._offer_project_for_dropped([Path(r"C:\tmp\a.mp3"), Path(r"C:\tmp\b.wav")])
assert chamadas == ["new_project", "ingest:2"], chamadas
print("PASS: aceite cria o projeto e ingere os arquivos arrastados")

# Aceite mas o usuario CANCELA o dialogo de novo projeto: nada e ingerido
chamadas.clear()
win.context = None
win.new_project = lambda *a, **k: chamadas.append("new_project")  # nao cria
win._offer_project_for_dropped([Path(r"C:\tmp\a.mp3")])
assert chamadas == ["new_project"], chamadas
print("PASS: cancelar o novo projeto nao ingere nada")

# ------------------------------------------------ enfase da jornada
@dataclass
class S:
    interview_id: str
    review_exists: bool = False
    canonical_exists: bool = False


win2 = rs.ReviewStudioWindow()
app.processEvents()
media = win2._media_button_ref
transcrever = win2.transcribe_button

win2.statuses = []
win2._update_add_media_emphasis(False)
assert ui_tokens.ACCENT in media.styleSheet(), "sem midia: Adicionar primary"
assert ui_tokens.ACCENT not in transcrever.styleSheet()

win2.statuses = [S("a"), S("b")]
win2._update_add_media_emphasis(True)
assert ui_tokens.ACCENT not in media.styleSheet()
assert ui_tokens.ACCENT in transcrever.styleSheet(), \
    "midia toda pendente: Transcrever primary"

win2.statuses = [S("a", review_exists=True), S("b")]
win2._update_add_media_emphasis(True)
assert ui_tokens.ACCENT not in media.styleSheet()
assert ui_tokens.ACCENT not in transcrever.styleSheet(), \
    "projeto andando: toolbar neutra"
print("PASS: enfase caminha com a jornada (Adicionar -> Transcrever -> neutra)")

print("PASS: toy_first_contact")
