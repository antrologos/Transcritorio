"""Teste exaustivo F1+F2+F3: instancia GUI real, simula teclas, valida DOM.

Roda com: QT_QPA_PLATFORM=offscreen python -B tests/smoke_f1_f2_f3.py
"""
from __future__ import annotations

import sys
import tempfile
import csv
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

app = QApplication.instance() or QApplication([])

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
(tmp / "audio").mkdir()
for iid in ["A01", "A02", "A03"]:
    (tmp / "audio" / f"{iid}.mp3").write_bytes(b"FAKE" * 50)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    w = csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"])
    w.writeheader()
    for iid in ["A01", "A02", "A03"]:
        w.writerow({"interview_id": iid, "source_path": f"audio/{iid}.mp3", "selected": "true"})
(paths.manifest_dir / "speakers_map.csv").write_text("interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# test"])

from transcribe_pipeline import app_service, project_store
from transcribe_pipeline.review_studio_qt import (
    ReviewStudioWindow, _apply_dark_theme, saved_status_message, saved_status_tooltip,
    ExportDialog, ExportResultDialog,
)
_apply_dark_theme(app)
win = ReviewStudioWindow(project_root=tmp)
app.processEvents()


def header(s: str) -> None:
    print()
    print("=" * 60)
    print(s)
    print("=" * 60)


def check(label: str) -> None:
    print(f"[{label}] OK")


# ==== FASE 1 ====
header("FASE 1 - MENUS")

menus = [a.text() for a in win.menuBar().actions()]
assert menus == ["Arquivo", "Editar", "Transcrever", "Ajuda"], menus
check("1.1 4 top-level: " + " / ".join(menus))

# Reunir todos os shortcuts declarados (action.shortcut) dos menus
shortcuts_by_action: dict[str, list[str]] = {}
for menu_act in win.menuBar().actions():
    submenu = menu_act.menu()
    if submenu is None:
        continue
    for a in submenu.actions():
        sc = a.shortcut().toString()
        if sc:
            shortcuts_by_action.setdefault(sc, []).append(a.text())
        sub = a.menu()
        if sub is not None:
            for sa in sub.actions():
                ssc = sa.shortcut().toString()
                if ssc:
                    shortcuts_by_action.setdefault(ssc, []).append(sa.text())

# Ctrl+Z e Ctrl+Shift+Z podem aparecer 2x (editor + trash): intencional.
dup = {k: v for k, v in shortcuts_by_action.items() if len(v) > 1}
expected_dup_keys = {"Ctrl+Z", "Ctrl+Shift+Z"}
assert set(dup.keys()).issubset(expected_dup_keys), f"Duplicatas inesperadas: {dup}"
check(f"1.2 {len(shortcuts_by_action)} atalhos, dup apenas Ctrl+Z/Shift+Z")

# Required shortcuts presentes
required = {"Ctrl+N", "Ctrl+O", "F5", "Ctrl+S", "Ctrl+E", "F2", "Ctrl+Alt+Up", "Ctrl+Alt+Down", "Del", "Ctrl+Z", "Ctrl+Shift+Z"}
missing = required - set(shortcuts_by_action.keys())
assert not missing, f"atalhos faltantes: {missing}"
check(f"1.3 todos atalhos essenciais: {sorted(required)}")

# Itens renomeados em posicoes corretas — coletar via action.text() direto, evita refs perdidas
menu_items_by_title: dict[str, list[str]] = {}
for menu_act in win.menuBar().actions():
    submenu = menu_act.menu()
    if submenu is None:
        continue
    menu_items_by_title[menu_act.text()] = [
        a.text() for a in submenu.actions() if a.text() and not a.isSeparator()
    ]

assert "Limpar transcricao gerada..." in menu_items_by_title["Editar"], menu_items_by_title["Editar"]
assert "Enviar para Lixeira..." in menu_items_by_title["Editar"]
check("1.4 Editar contem 'Limpar transcricao gerada...' e 'Enviar para Lixeira...'")

assert "Abrir pasta Resultados" in menu_items_by_title["Arquivo"], menu_items_by_title["Arquivo"]
check("1.5 Arquivo contem 'Abrir pasta Resultados'")

assert "Fluxo de trabalho" in menu_items_by_title["Ajuda"], menu_items_by_title["Ajuda"]
check("1.6 Ajuda contem 'Fluxo de trabalho'")

# ==== FASE 2 ====
header("FASE 2 - SAVE/EXPORT UX")

assert win.delete_transcription_action.text() == "Limpar transcricao gerada..."
assert win.trash_selected_action.text() == "Enviar para Lixeira..."
check("2.1 Actions renomeadas")

assert saved_status_message() == "Todas as alteracoes foram salvas"
assert "Ultimo salvamento:" in saved_status_tooltip()
check(f"2.2 saved_status: '{saved_status_message()}' + timestamp tooltip")

win.set_save_state(saved_status_message())
assert win.save_status_label.toolTip().startswith("Ultimo salvamento:")
win.set_save_state("Salvando...")
assert win.save_status_label.toolTip() == ""
check("2.3 set_save_state: tooltip condicional OK")

# ExportDialog 4 cenarios
d1 = ExportDialog(has_open=True, open_title="Teste", n_selected=0, n_total=3)
assert d1.selected_scope() == "current" and d1.scope_row.isVisible() is False
d2 = ExportDialog(has_open=False, n_selected=2, n_total=3)
assert d2.selected_scope() == "selected"
d3 = ExportDialog(has_open=False, n_selected=0, n_total=5)
assert d3.selected_scope() == "all" and d3._ok_btn.isEnabled()
d4 = ExportDialog(has_open=False, n_selected=0, n_total=25)
assert d4.large_confirm is not None and not d4._ok_btn.isEnabled()
d4.large_confirm.setChecked(True); app.processEvents()
assert d4._ok_btn.isEnabled()
check("2.4 ExportDialog 4 cenarios (current/selected/all<20/all>=20)")

# ExportResultDialog tem 4 botoes + lista
fake_files = []
for name in ["A01.reviewed.docx", "A01.reviewed.md", "A02.reviewed.docx"]:
    p = tmp / name
    p.write_bytes(b"x" * 200)
    fake_files.append(p)
rd = ExportResultDialog(exported_paths=fake_files, skipped_ids=["B01"], results_folder=tmp)
assert rd.list.count() == 3
button_texts = sorted({b.text() for b in rd.findChildren(QPushButton)})
assert "Abrir pasta" in button_texts
assert "Copiar caminho" in button_texts
assert "Fechar" in button_texts
if sys.platform == "win32":
    assert "Mostrar no Explorer" in button_texts
check(f"2.5 ExportResultDialog: {rd.list.count()} items + botoes {button_texts}")

# ==== FASE 3 ====
header("FASE 3 - RESULTADOS/")

assert win.context.config.get("use_resultados_dir") is True
check("3.1 config.use_resultados_dir default=True")

folder = win._results_folder_for_user()
assert folder == paths.review_dir / "final"
check(f"3.2 _results_folder_for_user pre-export aponta para '{folder.name}/'")

# Simular export via mock: MD + DOCX + NVivo (nvivo e tecnico)
final_md = paths.review_dir / "final" / "md" / "A01.reviewed.md"
final_md.parent.mkdir(parents=True, exist_ok=True)
final_md.write_text("# Entrevista A01\nv1", encoding="utf-8")
final_docx = paths.review_dir / "final" / "docx" / "A01.reviewed.docx"
final_docx.parent.mkdir(parents=True, exist_ok=True)
final_docx.write_bytes(b"docx " * 500)
nvivo_tsv = paths.review_dir / "final" / "nvivo" / "A01.reviewed_nvivo.tsv"
nvivo_tsv.parent.mkdir(parents=True, exist_ok=True)
nvivo_tsv.write_text("col1\tcol2\n", encoding="utf-8")

with patch("transcribe_pipeline.app_service.export_review_outputs",
           return_value=[final_md, final_docx, nvivo_tsv]):
    app_service.export_review(win.context, "A01", formats=["md", "docx", "nvivo"])

res = tmp / "Resultados"
assert res.exists()
assert (res / "A01.reviewed.md").exists()
assert (res / "A01.reviewed.docx").exists()
assert not (res / "A01.reviewed_nvivo.tsv").exists(), "nvivo NAO deve espelhar"
assert (res / "LEIA-ME.txt").exists()
check("3.3 Resultados/ criado: MD+DOCX+LEIA-ME, NVivo filtrado")

assert (res / "A01.reviewed.md").read_text(encoding="utf-8") == final_md.read_text(encoding="utf-8")
check("3.4 Conteudo espelhado binariamente identico")

# _results_folder_for_user agora aponta para Resultados/
folder2 = win._results_folder_for_user()
assert folder2 == res
check("3.5 _results_folder_for_user pos-export aponta para 'Resultados/'")

# Re-export atualiza o mirror
final_md.write_text("# Entrevista A01\nVERSAO NOVA v2", encoding="utf-8")
with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=[final_md]):
    app_service.export_review(win.context, "A01", formats=["md"])
assert (res / "A01.reviewed.md").read_text(encoding="utf-8") == "# Entrevista A01\nVERSAO NOVA v2"
check("3.6 Re-export atualiza o mirror")

# LEIA-ME preserva edicao
(res / "LEIA-ME.txt").write_text("EDIT USUARIO", encoding="utf-8")
with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=[final_md]):
    app_service.export_review(win.context, "A01", formats=["md"])
assert (res / "LEIA-ME.txt").read_text(encoding="utf-8") == "EDIT USUARIO"
check("3.7 LEIA-ME preserva edicao do usuario")

# Flag off nao toca Resultados em projeto novo
tmp2 = Path(tempfile.mkdtemp())
paths2 = make_paths({**config, "project_root": str(tmp2)}, base_dir=tmp2)
ensure_directories(paths2)
md2 = paths2.review_dir / "final" / "md" / "X.reviewed.md"
md2.parent.mkdir(parents=True)
md2.write_text("x", encoding="utf-8")
from transcribe_pipeline.app_service import ProjectContext
ctx2 = ProjectContext(
    config_path=paths2.config_dir / "run_config.yaml",
    config={**win.context.config, "use_resultados_dir": False, "project_root": str(tmp2)},
    paths=paths2,
    rows=[],
    project={},
    metadata={},
    jobs={},
)
with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=[md2]):
    app_service.export_review(ctx2, "X", formats=["md"])
assert not (tmp2 / "Resultados").exists()
check("3.8 use_resultados_dir=False impede criacao")

# ==== REGRESSAO ====
header("REGRESSAO F0 + A + B + C + diarizacao + dark")

# Dark theme
pal = app.palette()
assert pal.color(QPalette.ColorRole.Window).lightness() < 80
check("R.1 Dark theme (Window lightness < 80)")

# Logger em dev
import logging
from transcribe_pipeline.review_studio_qt import _logger
assert len(_logger.handlers) == 1
assert isinstance(_logger.handlers[0], logging.StreamHandler)
check("R.2 Logger dev mode (StreamHandler)")

# Iteracao A
from transcribe_pipeline.review_studio_qt import _compute_effective_target_ids
assert _compute_effective_target_ids(["a","b"], checked={"a"}, visually_selected=set()) == ["a"]
check("R.3 effective_target_ids (Iteracao A)")

# Iteracao B
from transcribe_pipeline.project_store import _reorder_move, _merge_interview_order
assert _reorder_move(["a","b","c"], "b", -1) == ["b","a","c"]
assert _merge_interview_order(["a","b"], ["a","b","c"]) == ["a","b","c"]
check("R.4 reorder + merge (Iteracao B)")

# Iteracao C
from transcribe_pipeline.project_store import _find_collisions, _build_undo_entry
assert _find_collisions([]) == []
e = _build_undo_entry(trash_id="x", interview_ids=["a"], csv_mtimes={}, snapshots={}, moved_files=[])
assert e["status"] == "complete"
check("R.5 trash logic (Iteracao C)")

# close_open_file existe (fix critico)
assert hasattr(win, "close_open_file")
assert not hasattr(win, "close_current_review")
check("R.6 close_open_file existe, close_current_review removido")

# Helpers de cor
from transcribe_pipeline.review_studio_qt import _style_ok, _style_warn, _style_err, _style_muted
for fn in (_style_ok, _style_warn, _style_err, _style_muted):
    assert "color: #" in fn()
check("R.7 Style helpers (F0)")

print()
print("=" * 60)
print("TODOS OS TESTES F1+F2+F3+REGRESSAO PASSARAM")
print("=" * 60)
