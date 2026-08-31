"""Smoke R0 (rede de seguranca do Programa R): navegabilidade da UI.

Tres gates estruturais, fotografados ANTES da reforma comecar:
1. Nenhuma QAction orfa — toda acao tem casa (menu, toolbar ou widget
   ancorado). As 4 orfas historicas ficam numa lista que SO ENCOLHE
   (a R3 as remove; acao nova sem casa quebra o teste na hora).
2. Colisao de atalhos — dois objetos QAction diferentes com o mesmo
   atalho so nas duplicatas intencionais listadas.
3. Matriz de estados — update_action_states() roda sem excecao nos
   estados basicos (e o detector de AttributeError quando a R3 remover
   acoes: as referencias la sao incondicionais).
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Estado da MAQUINA fora do teste: sem isto, cada rodada gravava
# projetos tmp na lista real de Projetos recentes do usuario.
import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QToolBar

app = QApplication.instance() or QApplication([])

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# smoke_nav"])

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow, _apply_dark_theme

_apply_dark_theme(app)
win = ReviewStudioWindow(project_root=tmp)
app.processEvents()

# ---------------------------------------------------------------- casas das acoes
def acoes_em_menu(menu: QMenu) -> set[int]:
    achadas: set[int] = set()
    for act in menu.actions():
        achadas.add(id(act))
        if act.menu() is not None:
            achadas |= acoes_em_menu(act.menu())
    return achadas

com_casa: set[int] = set()
for act in win.menuBar().actions():
    if act.menu() is not None:
        com_casa |= acoes_em_menu(act.menu())
for tb in win.findChildren(QToolBar):
    com_casa |= {id(a) for a in tb.actions()}
from PySide6.QtWidgets import QPushButton, QWidget

for widget_nome in ("interview_table", "text_edit"):
    w = getattr(win, widget_nome, None)
    if w is not None:
        com_casa |= {id(a) for a in w.actions()}
# Botoes-espelho (action_button ancora a acao no botao) e quaisquer
# outros widgets com acoes registradas.
for w in win.findChildren(QWidget):
    com_casa |= {id(a) for a in w.actions()}
# Acoes ancoradas NA JANELA (atalhos globais dos botoes do player:
# Espaco, Ctrl+Left/Right) — findChildren nao inclui a propria janela.
com_casa |= {id(a) for a in win.actions()}

# Acoes que sao atributos da janela (a superficie de comando oficial).
acoes_janela: dict[str, QAction] = {
    nome: obj for nome, obj in vars(win).items()
    if isinstance(obj, QAction)
}

# Orfas historicas: a R3-c1 (2026-08-31) removeu as 4 do inventario.
# Lista VAZIA e o estado final — acao sem casa e defeito imediato.
ORFAS_CONHECIDAS: set[str] = set()

orfas = {nome for nome, act in acoes_janela.items() if id(act) not in com_casa}
inesperadas = orfas - ORFAS_CONHECIDAS
assert not inesperadas, f"acoes SEM CASA (nem menu, nem toolbar, nem widget): {sorted(inesperadas)}"
sumidas = ORFAS_CONHECIDAS - set(acoes_janela)
extintas = ORFAS_CONHECIDAS - orfas
print(f"OK: {len(acoes_janela)} acoes na janela; orfas conhecidas ainda presentes: "
      f"{len(orfas & ORFAS_CONHECIDAS)}; ja removidas/curadas: {sorted(sumidas | extintas) or 'nenhuma'}")

# ---------------------------------------------------------------- colisoes de atalho
por_atalho: dict[str, set[str]] = {}
for nome, act in acoes_janela.items():
    for seq in act.shortcuts():
        chave = seq.toString()
        if chave:
            por_atalho.setdefault(chave, set()).add(nome)

# Duplicatas intencionais (contextos de shortcut distintos:
# Ctrl+Z desfaz edicao com foco no editor e desfaz exclusao com foco
# na lista — WidgetWithChildrenShortcut resolve; o menu exibe os dois)
DUPLICATAS_OK = {
    "Ctrl+Z": {"undo_action", "trash_undo_action"},
}
for atalho, nomes in sorted(por_atalho.items()):
    if len(nomes) > 1:
        permitido = DUPLICATAS_OK.get(atalho, set())
        assert nomes <= permitido, \
            f"colisao de atalho {atalho}: {sorted(nomes)} (permitido: {sorted(permitido)})"
print(f"OK: {len(por_atalho)} atalhos, colisoes apenas nas intencionais")

# ---------------------------------------------------------------- matriz de estados
win.update_action_states()                       # projeto vazio
win.context.jobs["X1"] = {"status": "Rodando"}   # job ativo simulado
win.update_action_states()
win.context.jobs.clear()
win.update_action_states()
app.processEvents()
print("OK: update_action_states sem excecao em 3 estados")

print("PASS: smoke_nav_ui")
