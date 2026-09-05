"""Toy: preparo dos audios em paralelo (B2, 2026-09-02) + montagem do lote.

Com 2+ arquivos o preparo (ffmpeg) vira UM passo do lote, 2 conversoes por
vez; quem falha entra em _prepare_failed e o passo de transcricao daquele
arquivo levanta o erro (skip-and-continue). Verifica tambem que a lista de
pesos continua alinhada com a de passos (desalinhar quebra o progresso a
partir do 2o arquivo) e que o servidor de separacao e a faixa de vozes
sao acionados pelo lote.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_prepare_batch_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline import review_studio_qt as rsq
    from transcribe_pipeline.review_studio_qt import ReviewStudioWindow
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_service, parakeet_runner, project_store, runtime  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp_home) / "proj.transcricao"
ctx = app_service.create_project(root, "lote")
IDS = ["E1", "E2", "E3"]
write_manifest([
    {"interview_id": iid, "selected": "true", "source_path": f"midia/{iid}.m4a",
     "source_ext": ".m4a", "wav_path": f"Transcricoes/01_audio_wav16k_mono/{iid}.wav",
     "status": "pending", "duration_sec": "60"}
    for iid in IDS
], ctx.paths.manifest_dir / "manifest.csv")
win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()

# --- _prepare_batch: paralelo, falha isolada, jobs.json, progresso ---
DELAY = 0.4


def fake_prepare(context, ids=None, force=False):
    time.sleep(DELAY)
    return app_service.JobResult("prepare-audio", 1 if ids == ["E2"] else 0)


app_service.prepare_interviews = fake_prepare
eventos: list[dict] = []
t0 = time.monotonic()
result = win._prepare_batch(IDS, 0, 10, eventos.append, lambda: False)
elapsed = time.monotonic() - t0
assert result.failures == 0, "falha de um arquivo nunca derruba o passo do lote"
assert win._prepare_failed == {"E2"}, win._prepare_failed
assert elapsed < DELAY * 3 - 0.1, f"nao paralelizou: {elapsed:.2f}s"
assert eventos[0]["progress"] == 0 and eventos[-1]["progress"] == 100, eventos
assert eventos[-1]["message"] == "Preparando os áudios (3 de 3)..." and eventos[-1]["event"] == "prepare_progress"
jobs = project_store.read_json(project_store.jobs_path(win.context.paths)) or {}
assert jobs["E1"]["progress"] == 10 and jobs["E3"]["progress"] == 10 and jobs["E1"]["stage"] == "preparar audio", jobs
assert jobs["E2"]["progress"] == 0, jobs["E2"]
print("PASS: _prepare_batch — 2 por vez, falha isolada, jobs.json e progresso")

# --- _transcribe_prepared: recusa quem falhou; chama o ASR para os demais ---
chamados: list[str] = []
app_service.transcribe_interviews = lambda context, ids=None, overrides=None, progress_callback=None, should_cancel=None: (
    chamados.extend(ids or []) or app_service.JobResult("transcribe", 0))
try:
    win._transcribe_prepared("E2", "parakeet-pt")
    raise AssertionError("E2 deveria ter sido recusado")
except RuntimeError as exc:
    assert "ffmpeg" in str(exc), exc
assert win._transcribe_prepared("E1", "parakeet-pt").failures == 0 and chamados == ["E1"]
print("PASS: _transcribe_prepared — recusa o preparo falho, transcreve o resto")

# --- cancelamento no meio do preparo ---
feitos = {"n": 0}


def _conta(detail: dict) -> None:
    if detail["progress"] > 0:
        feitos["n"] += 1


win._prepare_batch(IDS, 0, 10, _conta, lambda: feitos["n"] >= 1)
assert feitos["n"] < 3, "cancelar deveria parar antes do fim"
print("PASS: _prepare_batch — cancelar interrompe")

# --- montagem do lote: passos x pesos alinhados; servidor + faixa acionados ---
capturado: dict = {}
servidor: list[int] = []
win.start_worker = lambda label, steps, weights=None: capturado.update(label=label, steps=steps, weights=weights)  # type: ignore[method-assign]
win._start_diarize_server = (  # type: ignore[method-assign]
    lambda n, sobrepor=False: servidor.append((n, sobrepor)))
win.ensure_models_ready = lambda *a, **k: True  # type: ignore[method-assign]
win._maybe_offer_parakeet_cpu = lambda *a, **k: False  # type: ignore[method-assign]
win._maybe_offer_parakeet_gpu = lambda *a, **k: None  # type: ignore[method-assign]
app_service.diarize_effective = lambda config: (True, "")
rsq.ids_without_speaker_setup = lambda metadata, ids: []
runtime.resolve_device = lambda device=None: ("cuda", False)
parakeet_runner.planned_device = lambda device: "cuda"

win.run_full_transcription_job(ids=list(IDS))
steps, weights = capturado["steps"], capturado["weights"]
assert len(steps) == len(weights), (len(steps), len(weights))
assert steps[0][0] == "Preparando 3 áudios..." and steps[0][2] is True and len(steps[0]) == 3, steps[0]
# 1 passo de lote + 6 por arquivo (transcrever, separar, montar, conferir, recriar, verificar)
assert len(steps) == 1 + 6 * 3, len(steps)
assert "convertendo" not in " ".join(str(s[0]) for s in steps[1:])
w5 = rsq._pipeline_weights("parakeet-pt", "cuda")
assert weights[0] == max(1, w5[0] * 3), (weights[0], w5)
assert servidor == [(3, False)] and win._voice_batch_ids == IDS, servidor
print("PASS: lote de 3 — passo unico de preparo, pesos alinhados, servidor e faixa acionados")

capturado.clear()
servidor.clear()
win.run_full_transcription_job(ids=["E1"])
steps, weights = capturado["steps"], capturado["weights"]
assert len(steps) == len(weights) == 7, (len(steps), len(weights))
assert "convertendo" in steps[0][0], steps[0][0]
assert servidor == [(1, False)], servidor
print("PASS: lote de 1 — rota antiga (preparo por arquivo)")

# --- sobreposicao (2026-09-05): maquina de CPU com nucleos e memoria ---------
# A separacao passa a comecar JUNTO com a transcricao do mesmo arquivo. A lista
# de passos NAO muda de forma (o passo de separacao vira espera, nao some), e o
# servidor passa a ser aberto tambem com UM arquivo — que e quem mais ganha.
capturado.clear()
servidor.clear()
runtime.resolve_device = lambda device=None: ("cpu", False)
parakeet_runner.planned_device = lambda device: "cpu"
rsq_caps = sys.modules["transcribe_pipeline.capabilities"]
rsq_caps.hardware_snapshot = lambda: rsq_caps.Hardware(   # type: ignore[assignment]
    has_gpu=False, vram_gb=None, ram_gb=16.0, cores=4, free_disk_gb=100.0)

# diarize_now=True pula a janela pre-lote: em CPU ela e MODAL e travaria o toy.
win.run_full_transcription_job(ids=["E1"], diarize_now=True)
steps, weights = capturado["steps"], capturado["weights"]
assert len(steps) == len(weights) == 7, (len(steps), len(weights))
assert servidor == [(1, True)], f"1 arquivo tambem abre o servidor ao sobrepor: {servidor}"
assert any("separando as vozes" in str(s[0]) for s in steps), \
    "o passo de separacao continua existindo — ele passa a ESPERAR, nao some"
print("PASS: sobreposicao — mesma forma de lote, servidor tambem com 1 arquivo")

# --- e a maquina apertada continua em serie, com motivo ----------------------
for _hw, _motivo in (
    (rsq_caps.Hardware(has_gpu=False, vram_gb=None, ram_gb=16.0, cores=2, free_disk_gb=100.0), "núcleos"),
    (rsq_caps.Hardware(has_gpu=False, vram_gb=None, ram_gb=4.0, cores=8, free_disk_gb=100.0), "memória"),
):
    ok, motivo = rsq_caps.should_overlap(_hw, "cpu")
    assert ok is False and _motivo in motivo, (ok, motivo)
assert rsq_caps.should_overlap(
    rsq_caps.Hardware(has_gpu=True, vram_gb=8.0, ram_gb=16.0, cores=8, free_disk_gb=100.0),
    "cuda")[0] is False
print("PASS: recusa de sobrepor vem com motivo")

print("PASS: toy_prepare_batch")
