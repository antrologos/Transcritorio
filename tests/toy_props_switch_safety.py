"""Toy 2026-09-01 (auditoria): trocar de entrevista NUNCA grava no alvo errado.

Cenario auditado: aba Propriedades ativa com edicao pendente da
entrevista A; o usuario abre a entrevista B. A cascata de refresh
(open_review -> _update_voice_banner -> _update_diar_failed_banner ->
_on_review_tab_changed) descarta o form de A com aviso e repopula com
B; e mesmo que o form ficasse orfao, o guard do _save_props_from_tab
(_props_loaded_iid != current_interview_id) recusa e re-sincroniza em
vez de gravar os valores de A nos metadados de B.
"""
from __future__ import annotations

import csv
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()
_os_iso.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import (
    DEFAULT_CONFIG, ensure_directories, make_paths, write_config)

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy"])

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

win = ReviewStudioWindow(project_root=tmp)
app.processEvents()


@dataclass
class S:
    interview_id: str
    source_path: str = ""
    duration_sec: str = "10"
    review_exists: bool = True
    canonical_exists: bool = True


win.statuses = [S("A"), S("B")]
win._status_map = {s.interview_id: s for s in win.statuses}
win.review_tabs.setCurrentWidget(win._props_tab)

# Form carregado com A e sujo (usuario digitou)
win.current_interview_id = "A"
win._refresh_props_panel()
assert win._props_loaded_iid == "A"
win._touch_props("contexto")
assert win._props_dirty_fields == {"contexto"}

# "Abriu" a entrevista B: a mesma cascata que open_review dispara
win.current_interview_id = "B"
win._update_voice_banner()  # -> diar_failed -> _on_review_tab_changed(-1)
assert win._props_loaded_iid == "B", win._props_loaded_iid
assert not win._props_dirty_fields, "form sujo de A deveria ser descartado"
print("PASS: cascata do open_* repopula a aba com a entrevista nova")

# Guard do save: mesmo com estado orfao forcado, NUNCA grava no alvo errado
gravacoes: list[tuple] = []
win._apply_metadata_updates = lambda ids, updates: gravacoes.append((ids, updates))
win._props_loaded_iid = "A"           # simula form orfao da entrevista A
win._props_dirty_fields = {"contexto"}
win.current_interview_id = "B"
win._save_props_from_tab()
assert gravacoes == [], "gravou valores de A nos metadados de B!"
assert win._props_loaded_iid == "B", "guard deveria re-sincronizar o form"
print("PASS: guard do save recusa alvo divergente e re-sincroniza")

# Fim de job com edicao pendente reabilita o Salvar (botao ficava preso)
win._touch_props("contexto")
win._props_save_button.setEnabled(False)  # como _touch_props com worker ativo
win.worker = None
win._refresh_props_panel()                 # early-return dirty + mesma entrevista
assert win._props_save_button.isEnabled(), "Salvar deveria reabilitar sem worker"
print("PASS: botao Salvar reabilita apos o fim do job")

print("PASS: toy_props_switch_safety")
