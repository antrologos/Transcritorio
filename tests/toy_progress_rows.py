"""Toy: progresso por arquivo na lista + um so percentual na barra (2026-09-02).

Relato: durante o lote a coluna Transcricao ficava em "Processando 0%" para
todos (a lista so era redesenhada no fim), e a barra de baixo mostrava um
segundo percentual (o interno do motor) alem do total do lote. Decisao do
usuario: DOIS niveis — lista = cada arquivo (etapa + avanco), barra = lote
("Arquivo k de n · id · atividade · ~M min restantes"), sem percentual
interno no texto. Puras + janela com projeto fake (SKIP sem PySide6).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_progress_rows_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import (
        COL_FORMATO,
        COL_TRANSCRICAO,
        ReviewStudioWindow,
        batch_label,
        eta_text,
        row_progress_text,
        strip_percent,
        strip_step_prefix,
    )
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

# --- puras ---
assert row_progress_text({"status": "Na fila", "progress": 0}) == "Na fila"
assert row_progress_text({"status": "Rodando", "stage": "transcrever", "progress": 46}) == "Transcrevendo 46%"
assert row_progress_text({"status": "Rodando", "stage": "identificar falantes", "progress": 12}) == "Separando falantes 12%"
assert row_progress_text({"status": "Rodando", "stage": "preparar audio", "progress": 3}) == "Preparando áudio 3%"
assert row_progress_text({"status": "Rodando", "stage": "montar transcricao", "progress": 90}) == "Montando transcrição 90%"
assert row_progress_text({"status": "Rodando", "progress": 42}) == "Processando 42%"        # sem etapa: generico
assert row_progress_text({"status": "Rodando", "stage": "transcrever", "progress": "x"}) == "Transcrevendo 0%"
assert row_progress_text({"status": "Rodando", "stage": "transcrever", "progress": 250}) == "Transcrevendo 100%"
assert row_progress_text({"status": "Concluído", "progress": 100}) is None
assert row_progress_text({"status": "Falha"}) is None
assert row_progress_text({}) is None
print("PASS: row_progress_text")

assert strip_percent("Transcrevendo com Parakeet (46%)...") == "Transcrevendo com Parakeet..."
assert strip_percent("Separando falantes — 30% (3 min decorridos, ~5 min restantes)") == "Separando falantes (3 min decorridos, ~5 min restantes)"
assert strip_percent("Carregando o modelo Parakeet pt-BR...") == "Carregando o modelo Parakeet pt-BR..."
assert strip_percent("Baixando 45% de 2,5 GB") == "Baixando de 2,5 GB"
assert strip_step_prefix("2/5 F03R_0729: transcrevendo com o TAGARELA...") == "transcrevendo com o TAGARELA..."
assert strip_step_prefix("Carregando o modelo...") == "Carregando o modelo..."
assert eta_text(30) == "menos de 2 min restantes"
assert eta_text(12 * 60) == "~12 min restantes"
assert eta_text(3900) == "~1 h 05 min restantes"
assert batch_label(2, 5, "F03R_0729", "2/5 F03R_0729: transcrevendo com o TAGARELA...", None) == \
    "Arquivo 2 de 5 · F03R_0729 · transcrevendo com o TAGARELA..."
assert batch_label(2, 5, "F03R_0729", "Transcrevendo com Parakeet (46%)...", 12 * 60) == \
    "Arquivo 2 de 5 · F03R_0729 · Transcrevendo com Parakeet... · ~12 min restantes"
assert "%" not in batch_label(1, 1, "X", "Separando falantes — 30%", 100)
print("PASS: strip_percent / strip_step_prefix / eta_text / batch_label")

# --- janela: celulas da lista acompanham o jobs.json sem reconstruir a tabela ---
from transcribe_pipeline import app_service, project_store  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp_home) / "proj.transcricao"
ctx = app_service.create_project(root, "progresso")
rows = [
    {"interview_id": iid, "selected": "true", "source_path": f"midia/{iid}.m4a", "source_ext": ".m4a",
     "wav_path": f"Transcricoes/01_audio_wav16k_mono/{iid}.wav", "status": "pending", "duration_sec": "60"}
    for iid in ("E1", "E2")
]
write_manifest(rows, ctx.paths.manifest_dir / "manifest.csv")
win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()


def cell(iid: str, col: int) -> str:
    for r in range(win.interview_table.rowCount()):
        it = win.interview_table.item(r, 1)
        if it and str(it.data(0x0100) or "") == iid:  # Qt.ItemDataRole.UserRole
            return win.interview_table.item(r, col).text()
    raise AssertionError(f"linha {iid} nao encontrada")


assert cell("E1", COL_TRANSCRICAO) == "Não transcrita"
# fila + andamento gravados no jobs.json pela thread do lote
project_store.update_job(ctx.paths, "E1", {"status": "Rodando", "stage": "transcrever", "progress": 37})
project_store.update_job(ctx.paths, "E2", {"status": "Na fila", "stage": "aguardando", "progress": 0})
win._refresh_running_rows(force=True)
assert cell("E1", COL_TRANSCRICAO) == "Transcrevendo 37%", cell("E1", COL_TRANSCRICAO)
assert cell("E2", COL_TRANSCRICAO) == "Na fila"
# aceleracao: chamada logo em seguida sem force nao le de novo
project_store.update_job(ctx.paths, "E1", {"status": "Rodando", "stage": "identificar falantes", "progress": 60})
win._refresh_running_rows()
assert cell("E1", COL_TRANSCRICAO) == "Transcrevendo 37%"
win._refresh_running_rows(force=True)
assert cell("E1", COL_TRANSCRICAO) == "Separando falantes 60%"
# arquivo terminou: a linha e recalculada (sem review: "Não transcrita"), Formato tambem
formato_antes = cell("E1", COL_FORMATO)
project_store.update_job(ctx.paths, "E1", {"status": "Concluído", "stage": "", "progress": 100})
win._refresh_running_rows(force=True)
assert cell("E1", COL_TRANSCRICAO) == "Não transcrita", cell("E1", COL_TRANSCRICAO)
assert cell("E1", COL_FORMATO) == formato_antes
assert cell("E2", COL_TRANSCRICAO) == "Na fila"
# a barra de progresso continua sendo a do lote (on_worker_progress tambem dispara o refresh)
project_store.update_job(ctx.paths, "E2", {"status": "Rodando", "stage": "preparar audio", "progress": 5})
win._rows_refresh_at = 0.0
win.on_worker_progress("Arquivo 2 de 2 · E2 · convertendo o áudio...", 55)
assert win.progress_bar.value() == 55 and cell("E2", COL_TRANSCRICAO) == "Preparando áudio 5%"
print("PASS: lista acompanha o jobs.json durante o lote")

print("PASS: toy_progress_rows")
