"""Toy: os atalhos do Estúdio chegam ao teclado de quem está digitando — 2026-09-04.

Relato de campo: revisar é "muito clicar". Uma hora de entrevista tem ~219
blocos e cada um custava idas ao mouse. O Estúdio já tinha três atalhos de
player (Espaço, Ctrl+← e Ctrl+→), mas eles NÃO chegam a quem está com o cursor
no texto: o QTextEdit consome os três (sonda empírica). Este teste fixa as duas
metades da correção:

1. o que o QTextEdit sequestra — Espaço e Ctrl+←/→ não podem ser a única tecla
   de nenhuma ação do ciclo de revisão;
2. o que sobrevive — a família Alt e as teclas F disparam com o cursor DENTRO
   do texto, e sem alterar o texto.

Testa o ROTEAMENTO (tecla → ação), não o que cada ação faz: as conexões reais
são trocadas por contadores, para nenhuma janela modal abrir no teste. O
comportamento das ações fica em toy_merge_previous.py e nos toys de review_store.

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
# Estado da MAQUINA fora do teste (mesma isolacao do smoke_nav_ui).
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QTabWidget  # noqa: E402

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
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_atalhos"])

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow  # noqa: E402

win = ReviewStudioWindow(project_root=tmp)
win.show()
app.processEvents()

# ---------------------------------------------------------------- o mapa combinado
# (atributo da acao, atalho como o Qt o escreve, tecla, modificadores)
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
NADA = Qt.KeyboardModifier.NoModifier

MAPA = [
    ("play_action", "F4", Qt.Key.Key_F4, NADA),
    ("repeat_turn_action", "F3", Qt.Key.Key_F3, NADA),
    ("seek_back_action", "Alt+Left", Qt.Key.Key_Left, ALT),
    ("seek_forward_action", "Alt+Right", Qt.Key.Key_Right, ALT),
    ("seek_back_short_action", "Alt+Shift+Left", Qt.Key.Key_Left, ALT | SHIFT),
    ("seek_forward_short_action", "Alt+Shift+Right", Qt.Key.Key_Right, ALT | SHIFT),
    ("speed_down_action", "F7", Qt.Key.Key_F7, NADA),
    ("speed_up_action", "F8", Qt.Key.Key_F8, NADA),
    ("next_block_action", "Alt+Down", Qt.Key.Key_Down, ALT),
    ("prev_block_action", "Alt+Up", Qt.Key.Key_Up, ALT),
    ("next_flagged_action", "Alt+Shift+Down", Qt.Key.Key_Down, ALT | SHIFT),
    ("prev_flagged_action", "Alt+Shift+Up", Qt.Key.Key_Up, ALT | SHIFT),
    ("focus_toggle_action", "F6", Qt.Key.Key_F6, NADA),
    ("merge_prev_block_action", "Alt+Shift+J", Qt.Key.Key_J, ALT | SHIFT),
    ("merge_block_action", "Alt+J", Qt.Key.Key_J, ALT),
    ("split_block_action", "Alt+D", Qt.Key.Key_D, ALT),
    # Passar a fala para o bloco vizinho (2026-09-05): o conserto da fronteira
    # que a separacao automatica colocou no lugar errado. Espelha o par
    # Alt+J / Alt+Shift+J, que e a operacao vizinha.
    ("move_tail_action", "Alt+P", Qt.Key.Key_P, ALT),
    ("move_head_action", "Alt+Shift+P", Qt.Key.Key_P, ALT | SHIFT),
    # Quando a fala e de alguem que NAO e vizinho, nao ha fronteira a mover:
    # o jeito e trocar o falante, e ate 2026-09-05 o seletor so era alcancavel
    # pelo mouse.
    ("focus_speaker_action", "Alt+E", Qt.Key.Key_E, ALT),
]

# ---------------------------------------------------------------- 1. existem e tem a tecla
for nome, atalho, _k, _m in MAPA:
    acao = getattr(win, nome, None)
    assert isinstance(acao, QAction), f"acao {nome} nao existe na janela"
    escritos = [seq.toString() for seq in acao.shortcuts()]
    assert atalho in escritos, f"{nome}: esperado {atalho}, tem {escritos}"
print(f"OK: {len(MAPA)} acoes do Estudio com o atalho declarado")

# As tres antigas mantem a tecla antiga TAMBEM (quem ja aprendeu nao perde).
assert "Space" in [s.toString() for s in win.play_action.shortcuts()]
assert "Ctrl+Left" in [s.toString() for s in win.seek_back_action.shortcuts()]
assert "Ctrl+Right" in [s.toString() for s in win.seek_forward_action.shortcuts()]
print("OK: Espaco e Ctrl+setas continuam valendo")

# ---------------------------------------------------------------- 2. tem casa no menu
def acoes_do_menu(menu: QMenu) -> set[int]:
    achadas: set[int] = set()
    for act in menu.actions():
        achadas.add(id(act))
        if act.menu() is not None:
            achadas |= acoes_do_menu(act.menu())
    return achadas


no_menu: set[int] = set()
titulo_bloco = None
for act in win.menuBar().actions():
    if act.menu() is not None and act.text() == "Editar":
        for sub in act.menu().actions():
            if sub.menu() is not None:
                titulo_bloco = sub.text()
                no_menu |= acoes_do_menu(sub.menu())
assert titulo_bloco == "Bloco e reprodução", f"submenu do Estudio: {titulo_bloco!r}"
sem_casa = [nome for nome, *_ in MAPA if id(getattr(win, nome)) not in no_menu]
assert not sem_casa, f"atalho invisivel (fora do submenu Editar > Bloco): {sem_casa}"
print(f"OK: os {len(MAPA)} atalhos aparecem escritos no menu Editar > Bloco e reprodução")

# ---------------------------------------------------------------- 3. disparam DENTRO do texto
# Troca as conexoes reais por contadores: aqui se testa o roteamento da tecla,
# nao o que a acao faz (e nenhuma janela modal pode abrir no teste).
disparos: dict[str, int] = {}
for nome, *_ in MAPA:
    acao = getattr(win, nome)
    try:
        acao.triggered.disconnect()
    except RuntimeError:
        pass
    acao.triggered.connect(lambda _c=False, n=nome: disparos.__setitem__(n, disparos.get(n, 0) + 1))
    acao.setEnabled(True)


def mostrar_o_texto() -> None:
    """Traz o editor de bloco para a frente: num QTabWidget o widget de uma aba
    escondida nao aceita foco, e o teste inteiro depende do foco estar la."""
    alvo = win.text_edit
    pai = alvo.parentWidget()
    while pai is not None:
        if isinstance(pai, QTabWidget):
            pai.setCurrentWidget(alvo)
            return
        alvo, pai = pai, pai.parentWidget()


mostrar_o_texto()
win.activateWindow()
win.raise_()
# Sem entrevista aberta o editor nasce desabilitado; aqui interessa so o
# caminho da tecla, entao ele e ligado a mao.
win.text_edit.setEnabled(True)
app.processEvents()
win.text_edit.setPlainText("o cursor da revisora esta aqui dentro")
win.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
app.processEvents()
assert win.text_edit.hasFocus(), "o teste precisa do foco no texto do bloco"
antes = win.text_edit.toPlainText()

for nome, _atalho, tecla, mod in MAPA:
    QTest.keyClick(win.text_edit, tecla, mod)
    app.processEvents()

mudos = [nome for nome, *_ in MAPA if not disparos.get(nome)]
assert not mudos, f"atalho que NAO chega com o cursor no texto: {mudos}"
assert win.text_edit.toPlainText() == antes, "os atalhos nao podem escrever no texto do bloco"
print(f"OK: os {len(MAPA)} disparam com o cursor no texto e nao mexem no texto")

# ---------------------------------------------------------------- 4. o que o texto sequestra
# Documenta POR QUE existe a segunda tecla: a mesma acao play_action responde a
# F4 e nao a Espaco quando o foco esta no editor.
disparos.clear()
for tecla, mod, rotulo in [(Qt.Key.Key_Space, NADA, "Espaço"),
                           (Qt.Key.Key_Left, CTRL, "Ctrl+←"),
                           (Qt.Key.Key_Right, CTRL, "Ctrl+→")]:
    QTest.keyClick(win.text_edit, tecla, mod)
    app.processEvents()
assert not disparos, (
    "Espaco/Ctrl+setas chegaram ao editor nesta versao do Qt — se isso mudou, "
    f"reveja o desenho dos atalhos: {sorted(disparos)}")
print("OK: Espaco e Ctrl+setas continuam sequestrados pelo editor (motivo da familia Alt/F)")

# ---------------------------------------------------------------- 5. vocabulario
PROIBIDAS = ("fundir", "merge", "manifesto", " QC")
for nome, *_ in MAPA:
    acao = getattr(win, nome)
    texto = f"{acao.text()} {acao.toolTip()}".lower()
    for palavra in PROIBIDAS:
        assert palavra.strip().lower() not in texto, f"{nome} usa termo proibido {palavra!r}"
print("OK: vocabulario dos atalhos dentro do guia verbal")

print("PASS: toy_atalhos_estudio")
