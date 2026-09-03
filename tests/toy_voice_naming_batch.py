"""Toy: vozes por identificar ao fim do lote (C, 2026-09-02).

Num lote de 5 o dialogo "De quem é esta voz?" so aparecia para a
transcricao ABERTA; as outras 4 ficavam mudas ate serem abertas. Agora a
lista ganha a faixa "N entrevistas com vozes por identificar" com
"Identificar agora…" (uma por vez, em sequencia; cancelar interrompe) e
"Depois". Pura + janela offscreen com dialogo monkeypatchado.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_voice_batch_")
os.environ["TRANSCRITORIO_APP_DATA"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import ReviewStudioWindow, voice_naming_pending
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)


def _turns(*speakers: str) -> list[dict]:
    return [{"speaker": s, "text": "…", "start": i, "end": i + 1} for i, s in enumerate(speakers)]


TURNS = {
    "E1": _turns("SPEAKER_00", "SPEAKER_01"),   # 2 vozes: pendente
    "E2": _turns("SPEAKER_00", "SPEAKER_01"),   # confirmada nos metadados
    "E3": _turns("SPEAKER_00"),                 # voz unica: nada a perguntar
    "E4": _turns("SPEAKER_00", "SPEAKER_01"),   # pendente
}


def _load(iid: str) -> list[dict]:
    if iid == "E5":
        raise OSError("review corrompida")
    return TURNS[iid]


# --- pura ---
meta = {"E2": {"speakers_confirmed": "true"}, "E4": {"speakers_confirmed": "false"}}
assert voice_naming_pending(["E1", "E2", "E3", "E4", "E5"], {}, meta, _load) == ["E1", "E4"]
assert voice_naming_pending(["E4", "E1"], {}, meta, _load) == ["E4", "E1"], "ordem do lote"
assert voice_naming_pending(["E1", "E4"], {"voice_naming_prompt": False}, meta, _load) == []
assert voice_naming_pending([], {}, meta, _load) == []
print("PASS: voice_naming_pending")

# --- janela ---
from transcribe_pipeline import app_service  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp_home) / "proj.transcricao"
ctx = app_service.create_project(root, "lote")
write_manifest([
    {"interview_id": iid, "selected": "true", "source_path": f"midia/{iid}.m4a",
     "source_ext": ".m4a", "wav_path": f"Transcricoes/01_audio_wav16k_mono/{iid}.wav",
     "status": "pending", "duration_sec": "60"}
    for iid in ("E1", "E2", "E4")
], ctx.paths.manifest_dir / "manifest.csv")
ctx = app_service.load_project(config_path=ctx.config_path)
app_service.update_file_metadata(ctx, ["E2"], {"speakers_confirmed": "true"})
win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()
win._turns_for_voice_check = _load  # type: ignore[method-assign]

assert not win.voice_batch_banner.isVisibleTo(win)
win._voice_batch_ids = ["E1", "E2", "E4"]
win._update_voice_batch_banner()
assert win.voice_batch_banner.isVisibleTo(win)
assert win.voice_batch_label.text().startswith("🎙 2 entrevistas com vozes por identificar"), win.voice_batch_label.text()
assert win._voice_batch_ids == ["E1", "E4"]
print("PASS: faixa com a contagem certa (confirmada fica de fora)")

# durante um lote a faixa some
win.worker = SimpleNamespace(isRunning=lambda: True)
win._sync_busy_hints(True)
assert not win.voice_batch_banner.isVisibleTo(win)
win.worker = None
win._update_voice_batch_banner()
assert win.voice_batch_banner.isVisibleTo(win)
print("PASS: faixa some durante o lote e volta depois")

# sequencia: abre cada pendente e pergunta; cancelar interrompe
abertas: list[str] = []
perguntadas: list[str] = []
respostas = {"E1": True, "E4": False}


def _open(iid: str) -> None:
    abertas.append(iid)
    win.current_interview_id = iid
    win.review = {"edits": []}
    win.turns = TURNS[iid]


def _dialogo() -> bool:
    iid = win.current_interview_id
    perguntadas.append(iid)
    if respostas[iid]:
        win.context = app_service.update_file_metadata(win.context, [iid], {"speakers_confirmed": "true"})
        return True
    return False


win.open_review = _open  # type: ignore[method-assign]
win.open_voice_naming_dialog = _dialogo  # type: ignore[method-assign]
win._on_voice_batch_identify()
assert abertas == ["E1", "E4"] and perguntadas == ["E1", "E4"], (abertas, perguntadas)
assert win.voice_batch_banner.isVisibleTo(win) and win._voice_batch_ids == ["E4"]
assert "1 entrevista com vozes" in win.voice_batch_label.text(), win.voice_batch_label.text()
print("PASS: sequencia confirma E1, cancela em E4 e a faixa segue com E4")

respostas["E4"] = True
win._on_voice_batch_identify()
assert perguntadas == ["E1", "E4", "E4"]
assert not win.voice_batch_banner.isVisibleTo(win) and win._voice_batch_ids == []
print("PASS: ultima confirmada esconde a faixa")

# "Depois" esconde sem confirmar nada
win._voice_batch_ids = ["E1"]
win.context = app_service.update_file_metadata(win.context, ["E1"], {"speakers_confirmed": ""})
win._update_voice_batch_banner()
assert win.voice_batch_banner.isVisibleTo(win)
win._on_voice_batch_later()
assert not win.voice_batch_banner.isVisibleTo(win) and win._voice_batch_ids == []
print("PASS: Depois esconde a faixa")

print("PASS: toy_voice_naming_batch")
