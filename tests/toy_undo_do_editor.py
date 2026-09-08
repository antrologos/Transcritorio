"""Toy: de quem é o Ctrl+Z? — 2026-09-04.

Dois comandos disputam o Ctrl+Z: desfazer a edição da transcrição
(`undo_action`, pilha do editor) e desfazer o envio de um arquivo para a
Lixeira (`trash_undo_action`). Até 2026-09-04 a pergunta era "o foco está num
QTextEdit editável?" — e logo depois de clicar em "Juntar com próximo" ou
"Dividir bloco" o foco fica no BOTÃO. Resultado: o Ctrl+Z escapava do editor;
no melhor caso não fazia nada, no pior restaurava um ARQUIVO da Lixeira em vez
de desfazer a fusão que a pessoa acabara de fazer.

`_undo_belongs_to_editor()` passa a perguntar se o foco está em qualquer lugar
DENTRO do painel de revisão. Este teste fixa as quatro respostas.

Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

from PySide6.QtGui import QUndoCommand  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import (  # noqa: E402
    DEFAULT_CONFIG, ensure_directories, make_paths, write_config)

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text("interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_undo"])

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow  # noqa: E402

win = ReviewStudioWindow(project_root=tmp)
win.show()
win.activateWindow()
win.text_edit.setEnabled(True)      # sem entrevista aberta ele nasce desligado
app.processEvents()


def focar(widget) -> None:
    widget.setFocus()
    app.processEvents()


# --- nada a desfazer: o Ctrl+Z nunca e do editor ---
assert not win.undo_action.isEnabled(), "pilha do editor deveria comecar vazia"
focar(win.text_edit)
assert win._undo_belongs_to_editor() is False, "sem nada na pilha, o editor nao reivindica o Ctrl+Z"
print("PASS: pilha vazia devolve o Ctrl+Z para a Lixeira")

# --- a partir daqui ha uma edicao desfeivel na pilha ---
win.undo_stack.push(QUndoCommand("edicao de teste"))
app.processEvents()
assert win.undo_action.isEnabled()

# 1. foco no texto do bloco (o caso obvio, que ja funcionava)
focar(win.text_edit)
assert win._undo_belongs_to_editor() is True
print("PASS: foco no texto -> Ctrl+Z desfaz a edicao")

# 2. foco no BOTAO de juntar — o caso do defeito.
# Sem entrevista aberta os botoes nascem desligados; aqui interessa so a
# pergunta "de quem e o Ctrl+Z", entao eles sao ligados a mao.
for botao in (win.merge_button, win.split_button, win.merge_prev_button):
    botao.setEnabled(True)
focar(win.merge_button)
assert win.merge_button.hasFocus(), "o teste depende do foco no botao"
assert win._undo_belongs_to_editor() is True, \
    "depois de Juntar/Dividir o foco fica no botao: o Ctrl+Z tem de continuar sendo do editor"
focar(win.split_button)
assert win._undo_belongs_to_editor() is True
focar(win.merge_prev_button)
assert win._undo_belongs_to_editor() is True
print("PASS: foco nos botoes do bloco -> Ctrl+Z ainda desfaz a edicao")

# 3. foco na lista de arquivos: a Lixeira mantem o Ctrl+Z dela
focar(win.interview_table)
assert win.interview_table.hasFocus(), "o teste depende do foco na lista"
assert win._undo_belongs_to_editor() is False, \
    "com o foco na lista de arquivos o Ctrl+Z e o de desfazer a exclusao"
print("PASS: foco na lista de arquivos -> Ctrl+Z e o da Lixeira")

# 4. sem foco nenhum
win.interview_table.clearFocus()
app.processEvents()
if QApplication.focusWidget() is None:
    assert win._undo_belongs_to_editor() is False
    print("PASS: sem foco, ninguem reivindica")
else:
    print("(pulado: o Qt reatribuiu o foco sozinho neste ambiente)")

print("PASS: toy_undo_do_editor")
