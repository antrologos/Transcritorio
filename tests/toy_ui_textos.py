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

# Estado da MAQUINA fora do teste: sem isto, cada rodada gravava
# projetos tmp na lista real de Projetos recentes do usuario.
import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

ENFORCING = True  # ligado no fim da R0 (2026-08-31)

# Acoes que a R3 mata ou renomeia (consolidacao de comandos): as
# violacoes DELAS sao toleradas ate la. Esta lista SO ENCOLHE — cada
# familia consolidada na R3 remove suas entradas no mesmo commit.
EXCECOES_R3 = {
    "export_selected_action", "export_current_action",
    "delete_transcription_action", "name_voices_action",
    "improve_speakers_action", "render_action",
}

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
    r"revisao|separacao|identificacao|audio|audios|"
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
    print(f"PASS: guia verbal aplicado (enforcing; {total} toleradas nas "
          f"acoes que a R3 consolida)")
else:
    print("PASS: toy_ui_textos (modo relatorio)")
