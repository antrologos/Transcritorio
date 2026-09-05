"""Toy: separar as vozes JUNTO com a transcrição do mesmo arquivo — 2026-09-05.

As duas etapas são independentes (as duas só leem o WAV; quem junta é o
render), e a separação já rodava em subprocesso — o que a serializava era o
passo esperar por ela. Medido num notebook de 4 núcleos: sobrepor corta 10,8%
do relógio do lote, com a saída idêntica.

Este teste fixa as garantias que tornam a sobreposição segura, e que não são
óbvias no código:

- a thread NÃO escreve em jobs.json (só guarda o último percentual). Sem isso
  haveria dois escritores num arquivo que é lido-modificado-escrito sem rename
  atômico (regra do Dropbox), e atualizações se perderiam;
- uma falha na separação é RELANÇADA no passo de espera, para o `optional=True`
  do `job_step` continuar valendo: falha vira "falantes pendentes", nunca falha
  do lote;
- um arquivo cuja transcrição falhou pula o passo de espera, e a thread dele
  não pode ficar órfã;
- pedir adiantamento duas vezes para o mesmo arquivo não abre duas threads.

Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.mkdtemp(prefix="toy_diar_ahead_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp) / "appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import ReviewStudioWindow
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_service  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp) / "proj.transcricao"
ctx = app_service.create_project(root, "ahead")
write_manifest([{"interview_id": "E1", "selected": "true", "source_path": "midia/E1.m4a",
                 "source_ext": ".m4a", "wav_path": "Transcricoes/01_audio_wav16k_mono/E1.wav",
                 "status": "pending", "duration_sec": "60"}],
               ctx.paths.manifest_dir / "manifest.csv")
win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()

# --- sem adiantamento, o passo faz o trabalho na hora ------------------------
assert win._diar_ahead_take("E1") is None, "sem adiantamento, take devolve None"
print("PASS: sem adiantamento o passo segue o caminho de sempre")

# --- adiantar: o trabalho roda em thread e o take espera ----------------------
chamadas: list[tuple[str, object]] = []
thread_do_trabalho: list[str] = []


def trabalho_lento(interview_id, progress_callback=None, should_cancel=None):
    thread_do_trabalho.append(threading.current_thread().name)
    for pct in (10, 60):
        if progress_callback is not None:
            progress_callback({"event": "diarize_progress", "progress": pct})
        time.sleep(0.15)
    chamadas.append((interview_id, progress_callback))
    return app_service.JobResult("diarize", 0)


win._diarize_then_channels_now = trabalho_lento   # type: ignore[method-assign]

assert win._diar_ahead_start("E1") is True
assert win._diar_ahead_start("E1") is False, "duas chamadas nao podem abrir duas threads"
eventos: list[dict] = []
resultado = win._diar_ahead_take("E1", eventos.append)
assert resultado is not None and resultado.failures == 0
assert chamadas and chamadas[0][0] == "E1"
assert thread_do_trabalho and thread_do_trabalho[0] != threading.main_thread().name, \
    "o trabalho tem de correr fora da thread do passo"
assert eventos and eventos[-1]["progress"] == 100, eventos[-1]
print("PASS: adiantar roda em thread e o passo de espera recolhe")

# --- a thread NAO recebe o callback do passo (um escritor so em jobs.json) ---
assert chamadas[0][1] is not eventos.append, "a thread nao pode escrever pelo passo"
print("PASS: a thread nao escreve em jobs.json")

# --- take so serve uma vez ---------------------------------------------------
assert win._diar_ahead_take("E1") is None, "o registro e consumido"
print("PASS: o adiantamento e consumido uma vez so")

# --- falha na separacao e relancada no passo de espera -----------------------
def trabalho_quebrado(interview_id, progress_callback=None, should_cancel=None):
    raise RuntimeError("pyannote caiu")


win._diarize_then_channels_now = trabalho_quebrado   # type: ignore[method-assign]
win._diar_ahead_start("E1")
try:
    win._diar_ahead_take("E1")
except RuntimeError as exc:
    assert "pyannote caiu" in str(exc), exc
else:
    raise AssertionError("a falha tem de ser relancada — e o optional=True do job_step "
                         "que a transforma em 'falantes pendentes'")
print("PASS: falha relancada, para o optional=True continuar valendo")

# --- thread orfa (transcricao falhou, passo de espera pulado) ----------------
parar = threading.Event()


def trabalho_demorado(interview_id, progress_callback=None, should_cancel=None):
    parar.wait(10.0)
    return app_service.JobResult("diarize", 0)


win._diarize_then_channels_now = trabalho_demorado   # type: ignore[method-assign]
win._diar_ahead_start("E1")
orfa = win._diar_ahead["E1"]["thread"]
assert orfa.is_alive()
parar.set()                       # em producao: o servidor morto faz run() voltar
win._diar_ahead_join_all()
assert getattr(win, "_diar_ahead_stop", None) is None, "o freio de mao e reposto"
assert not orfa.is_alive(), "nenhuma thread de separacao sobrevive ao fim do lote"
assert win._diar_ahead == {}, "o registro e limpo"
win._diar_ahead_join_all()        # idempotente (roda no fim, na falha e ao fechar)
print("PASS: thread orfa e recolhida ao fim do lote")

# --- _diarize_then_channels usa o adiantado em vez de refazer ----------------
feito = []
win._diarize_then_channels_now = (   # type: ignore[method-assign]
    lambda iid, progress_callback=None, should_cancel=None: (
        feito.append(iid) or app_service.JobResult("diarize", 0)))
win._diar_ahead_start("E1")
win._diarize_then_channels("E1")
assert feito == ["E1"], f"o trabalho aconteceu UMA vez, na thread: {feito}"
print("PASS: o passo aproveita o adiantado em vez de separar de novo")

# --- o freio de mao chega ao trabalho ----------------------------------------
# Sem ele, uma thread órfã cujo servidor morreu ao fim do lote cairia no
# fallback e abriria um pyannote NOVO depois de tudo acabar.
visto: list[bool] = []
solta = threading.Event()


def trabalho_que_pergunta(interview_id, progress_callback=None, should_cancel=None):
    solta.wait(5.0)
    visto.append(bool(should_cancel and should_cancel()))
    return app_service.JobResult("diarize", 0)


win._diarize_then_channels_now = trabalho_que_pergunta   # type: ignore[method-assign]
win._diar_ahead_start("E1", lambda: False)   # o lote NAO foi cancelado
win._diar_ahead_stop.set()                   # ...mas o lote acabou
solta.set()
win._diar_ahead["E1"]["thread"].join(timeout=5.0)
assert visto == [True], ("o fim do lote tem de chegar ao trabalho mesmo sem "
                         f"cancelamento do usuario: {visto}")
win._diar_ahead_join_all()
print("PASS: o fim do lote freia a thread, mesmo sem cancelamento")

print("PASS: toy_diar_ahead")
