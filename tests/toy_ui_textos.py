"""Toy R0/R4 (guarda de vocabulario): textos visiveis vs guia verbal.

R0: gate ENFORCING sobre a janela principal.
R4: a guarda cresceu — o gate da R0 so via a janela principal, e as
violacoes de wizard/dialogos passaram invisiveis por meses. Agora
varre tambem FirstRunWizard, ProjectChooserDialog, NewProjectDialog,
ModelSetupDialog e ModelManagerDialog (construidos sem exec), inclui
titulos de QGroupBox, placeholders de QLineEdit, tooltips de botoes e
titulos de janela, aplica re.IGNORECASE na wordlist de acentos e
proibe CAMINHOS DE MENU MORTOS (menu Transcrever/Arquivo, acoes
removidas na R3) — instrucao que aponta menu inexistente e beco.

Regras cobertas (dossie, secao 6): termos proibidos; mojibake; "IA"
(guia manda "AI"); reticencias "..." ASCII (guia manda "…"); palavras
frequentes sem acento (wordlist curada); caminhos mortos.
"""
from __future__ import annotations

import csv
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Estado da MAQUINA fora do teste: sem isto, cada rodada gravava
# projetos tmp na lista real de Projetos recentes do usuario.
import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

ENFORCING = True  # ligado no fim da R0 (2026-08-31)

# Acoes toleradas (mecanica herdada da R3; VAZIA desde a R3-c4 — a
# guarda vale para 100% dos textos e a lista so pode encolher).
EXCECOES_R3: set[str] = set()

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QGroupBox, QLabel, QLineEdit)

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
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_textos"])

from transcribe_pipeline.review_studio_qt import (
    FirstRunWizard,
    ModelManagerDialog,
    ModelSetupDialog,
    NewProjectDialog,
    ProjectChooserDialog,
    ReviewStudioWindow,
    _apply_dark_theme,
)

_apply_dark_theme(app)
win = ReviewStudioWindow(project_root=tmp)
app.processEvents()

# ---------------------------------------------------------------- coleta
textos: list[tuple[str, str]] = []  # (origem, texto)


def coletar(prefixo: str, raiz) -> None:
    """Varre um widget-raiz completo: botoes (texto+tooltip), labels,
    titulos de grupo, placeholders e o titulo da janela."""
    for btn in raiz.findChildren(QAbstractButton):
        if btn.text():
            textos.append((f"{prefixo}botao", btn.text()))
        if btn.toolTip():
            textos.append((f"{prefixo}tooltip-botao", btn.toolTip()))
    for lbl in raiz.findChildren(QLabel):
        if lbl.text() and "<" not in lbl.text():
            textos.append((f"{prefixo}label", lbl.text()))
    for grp in raiz.findChildren(QGroupBox):
        if grp.title():
            textos.append((f"{prefixo}grupo", grp.title()))
    for edt in raiz.findChildren(QLineEdit):
        if edt.placeholderText():
            textos.append((f"{prefixo}placeholder", edt.placeholderText()))
    if raiz.windowTitle():
        textos.append((f"{prefixo}titulo", raiz.windowTitle()))


# Janela principal: actions por NOME de atributo (diagnostico melhor).
for nome, obj in vars(win).items():
    if isinstance(obj, QAction):
        if obj.text():
            textos.append((f"action:{nome}", obj.text()))
        if obj.toolTip() and obj.toolTip() != obj.text():
            textos.append((f"tooltip:{nome}", obj.toolTip()))
coletar("", win)

# R4: dialogos e wizard tambem sao UI. Construir sem exec() basta para
# varrer os textos; deleteLater no fim evita widgets orfaos.
dialogos = {
    "wizard/": FirstRunWizard(),
    "chooser/": ProjectChooserDialog(None),
    "novo-projeto/": NewProjectDialog(),
    "preparar-modelos/": ModelSetupDialog(),
    "gerenciar-modelos/": ModelManagerDialog(lambda: None),
}
for prefixo, dlg in dialogos.items():
    for act in dlg.findChildren(QAction):
        if act.text():
            textos.append((f"{prefixo}action", act.text()))
        if act.toolTip() and act.toolTip() != act.text():
            textos.append((f"{prefixo}tooltip", act.toolTip()))
    coletar(prefixo, dlg)
    dlg.deleteLater()
app.processEvents()

# ---------------------------------------------------------------- regras
# Termos proibidos + CAMINHOS MORTOS: "menu Transcrever"/"menu Arquivo"
# nao existem desde a R1; "Reprocessar falantes" e "Atualizar
# transcricao editavel" morreram na R3-c4. Instrucao com caminho morto
# manda o usuario a um beco.
PROIBIDOS = re.compile(
    r"\b(QC|manifesto|canonical|merge|fundir|Infocitizen)\b"
    r"|menu\s+(Transcrever|Arquivo)\b"
    r"|\bTranscrever\s*(→|->|&gt;|>)"
    r"|\bArquivo\s*(→|->|&gt;|>)\s"
    r"|Reprocessar falantes"
    r"|Atualizar transcri[cç][aã]o edit[aá]vel",
    re.I)
MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf€™œ£§]|Ã[a-z]?Â")
IA_SOLTA = re.compile(r"\bIA\b")
RETICENCIAS_ASCII = re.compile(r"\.\.\.")
# Wordlist curada: formas SEM acento que nao deveriam existir na UI.
# re.I desde a R4 ("Documentacao" capitalizado passava pelo gate).
# "Transcritorio" NAO entra: a extensao ".transcritorio" e o nome de
# token sugerido no Hugging Face sao literais tecnicos sem acento.
SEM_ACENTO = re.compile(
    r"\b(transcricao|transcricoes|midia|midias|rotulo|rotulos|"
    r"exportacao|exportacoes|documentacao|creditos|comecar|exclusao|"
    r"configuracao|configuracoes|nao|voce|atencao|concluido|concluida|"
    r"revisao|separacao|identificacao|audio|audios|"
    r"video|videos|ultima|ultimo|proxima|proximo|numero|pagina|"
    r"usuario|usuarios|orfao|orfaos|espaco|indisponivel|possivel|"
    r"aceleracao|instalacao|permissao|privilegios)\b",
    re.I)

violacoes: dict[str, list[str]] = {
    "proibidos": [], "mojibake": [], "ia": [], "reticencias": [], "acentos": [],
}
for origem, texto in textos:
    plano = unicodedata.normalize("NFC", texto)
    if PROIBIDOS.search(plano):
        violacoes["proibidos"].append(f"{origem}: {plano[:70]}")
    if MOJIBAKE.search(plano):
        violacoes["mojibake"].append(f"{origem}: {plano[:70]}")
    if IA_SOLTA.search(plano):
        violacoes["ia"].append(f"{origem}: {plano[:70]}")
    if RETICENCIAS_ASCII.search(plano):
        violacoes["reticencias"].append(f"{origem}: {plano[:70]}")
    if SEM_ACENTO.search(plano):
        violacoes["acentos"].append(f"{origem}: {plano[:70]}")

total = sum(len(v) for v in violacoes.values())
print(f"[guarda de vocabulario] {len(textos)} textos coletados; "
      f"{total} violacoes: " + ", ".join(f"{k}={len(v)}" for k, v in violacoes.items()))
for categoria, itens in violacoes.items():
    for item in itens[:25]:
        print(f"  [{categoria}] {item}")
    if len(itens) > 25:
        print(f"  [{categoria}] ... +{len(itens) - 25}")

def _tolerada(item: str) -> bool:
    # item = "action:NOME: texto" / "tooltip:NOME: texto" / "botao: ..."
    origem = item.split(": ", 1)[0]
    return any(origem.endswith(":" + nome) for nome in EXCECOES_R3)


if ENFORCING:
    fora_da_excecao = [
        item for itens in violacoes.values() for item in itens
        if not _tolerada(item)
    ]
    assert not fora_da_excecao, (
        f"{len(fora_da_excecao)} violacoes do guia verbal FORA das excecoes "
        f"da R3: {fora_da_excecao[:6]}")
    print("PASS: guia verbal aplicado (enforcing; janela + wizard + 4 dialogos)")
else:
    print("PASS: toy_ui_textos (modo relatorio)")
