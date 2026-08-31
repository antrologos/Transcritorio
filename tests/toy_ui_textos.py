"""Toy R0 (guarda de vocabulario): textos visiveis vs guia verbal do RD.

MODO RELATORIO durante a varredura da R0: imprime as violacoes por
categoria e passa sempre. Vira GATE (ENFORCING = True) no ultimo commit
da R0, quando a varredura zera as categorias que a R3 nao vai matar.

Regras cobertas (dossie, secao 6): termos proibidos; mojibake; "IA"
(guia manda "AI"); reticencias "..." ASCII (guia manda "…"); palavras
frequentes sem acento (wordlist curada).
"""
from __future__ import annotations

import csv
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENFORCING = False  # flip no fim da R0

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel

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

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow, _apply_dark_theme

_apply_dark_theme(app)
win = ReviewStudioWindow(project_root=tmp)
app.processEvents()

# ---------------------------------------------------------------- coleta
textos: list[tuple[str, str]] = []  # (origem, texto)
for nome, obj in vars(win).items():
    if isinstance(obj, QAction):
        if obj.text():
            textos.append((f"action:{nome}", obj.text()))
        if obj.toolTip() and obj.toolTip() != obj.text():
            textos.append((f"tooltip:{nome}", obj.toolTip()))
for btn in win.findChildren(QAbstractButton):
    if btn.text():
        textos.append(("botao", btn.text()))
for lbl in win.findChildren(QLabel):
    if lbl.text() and "<" not in lbl.text():
        textos.append(("label", lbl.text()))

# ---------------------------------------------------------------- regras
PROIBIDOS = re.compile(r"\b(QC|manifesto|canonical|merge|fundir|Infocitizen)\b", re.I)
MOJIBAKE = re.compile(r"[ÃÂ][\x80-\xbf€™œ£§]|Ã[a-z]?Â")
IA_SOLTA = re.compile(r"\bIA\b")
RETICENCIAS_ASCII = re.compile(r"\.\.\.")
# Wordlist curada: formas SEM acento que nao deveriam existir na UI
SEM_ACENTO = re.compile(
    r"\b(transcricao|transcricoes|midia|midias|rotulo|rotulos|"
    r"exportacao|exportacoes|documentacao|creditos|comecar|exclusao|"
    r"configuracao|configuracoes|nao|voce|atencao|concluido|concluida|"
    r"grafia[s]?\b.*\bnomes|revisao|separacao|identificacao|audio|audios|"
    r"video|videos|ultima|ultimo|proxima|proximo|numero|pagina)\b")

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
    for item in itens[:8]:
        print(f"  [{categoria}] {item}")
    if len(itens) > 8:
        print(f"  [{categoria}] ... +{len(itens) - 8}")

if ENFORCING:
    assert total == 0, f"{total} violacoes do guia verbal (dossie RD, secao 6)"
    print("PASS: guia verbal aplicado (enforcing)")
else:
    print("PASS: toy_ui_textos (modo relatorio — vira gate no fim da R0)")
