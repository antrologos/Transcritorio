"""Galeria R0: capturas offscreen da janela em estados canonicos.

NAO faz parte da suite (sem prefixo toy_/smoke_): e a ata VISUAL do
Programa R — rodar antes e depois de cada etapa e comparar a olho.
Salva PNGs em build/ui_gallery/<sha-curto>/ (gitignored).

Uso:  python -B tests/gallery_ui.py
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Plataforma NATIVA de proposito: o offscreen do Windows nao carrega a
# base de fontes (todo texto vira tofu) e a ata visual perde o sentido.
# grab() renderiza widget OCULTO — nenhuma janela pisca na tela.
os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

RAIZ = Path(__file__).resolve().parent.parent
try:
    sha = subprocess.run(["git", "-C", str(RAIZ), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "sem-git"
except OSError:
    sha = "sem-git"
destino = RAIZ / "build" / "ui_gallery" / sha
destino.mkdir(parents=True, exist_ok=True)

from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config
from transcribe_pipeline.review_studio_qt import ReviewStudioWindow, _apply_dark_theme

_apply_dark_theme(app)


def captura(win: ReviewStudioWindow, nome: str) -> None:
    win.resize(1440, 900)
    app.processEvents()
    ok = win.grab().save(str(destino / f"{nome}.png"))
    print(f"  {nome}.png {'ok' if ok else 'FALHOU'}")


# Estado 1: projeto vazio (empty-state de midia)
tmp1 = Path(tempfile.mkdtemp())
config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp1)
paths = make_paths(config, base_dir=tmp1)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# gallery"])

win = ReviewStudioWindow(project_root=tmp1)
print(f"Galeria em {destino}")
captura(win, "01_projeto_vazio")

# Estado 2: projeto com arquivos na lista (linhas simuladas)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    w = csv.DictWriter(h, fieldnames=[
        "interview_id", "source_path", "selected", "duration_sec"])
    w.writeheader()
    w.writerow({"interview_id": "E01_0001", "source_path": "a.m4a",
                "selected": "true", "duration_sec": "6127"})
    w.writerow({"interview_id": "E02_0002", "source_path": "b.m4a",
                "selected": "true", "duration_sec": "3510"})
win.refresh_interviews()
app.processEvents()
captura(win, "02_lista_com_arquivos")

# Estado 3: job simulado (barra de progresso visivel)
win.progress_bar.setVisible(True)
win.progress_bar.setRange(0, 100)
win.progress_bar.setValue(43)
win.progress_label.setText("Transcrevendo E01_0001...")
win.cancel_job_button.setVisible(True)
app.processEvents()
captura(win, "03_job_em_andamento")

# Estado 4: aba Documentos (R2)
if hasattr(win, "review_tabs"):
    win.review_tabs.setCurrentWidget(win.docs_panel)
    app.processEvents()
    captura(win, "04_aba_documentos")
    win.review_tabs.setCurrentIndex(0)

print("OK: galeria gerada")
