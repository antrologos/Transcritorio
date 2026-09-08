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

# --- NUNCA duas threads ao mesmo tempo (revisão adversarial, 2026-09-05) -----
# O achado: a transcrição de F1 falha -> o skip-and-continue pula o passo que
# recolhe a thread de F1 -> F1 segue separando -> F2 abre a SEGUNDA thread. As
# duas falam com um DiarizeServer que atende um pedido por vez e cuja fila de
# linhas é compartilhada: o @DONE de F1 seria consumido pela thread de F2, que
# declararia F2 pronto antes de o servidor começar — e o render sairia sem
# falantes, em silêncio.
vivas: list[str] = []
segura = threading.Event()


def trabalho_que_fica(interview_id, progress_callback=None, should_cancel=None):
    vivas.append(interview_id)
    while not segura.is_set() and not (should_cancel and should_cancel()):
        time.sleep(0.02)
    vivas.remove(interview_id)
    return app_service.JobResult("diarize", 0)


win._diarize_then_channels_now = trabalho_que_fica   # type: ignore[method-assign]
win._diar_ahead_start("F1")
_pump = time.monotonic()
while not vivas and time.monotonic() - _pump < 5:
    time.sleep(0.02)
assert vivas == ["F1"], vivas
segura.set()                       # F1 pode terminar quando for recolhida
win._diar_ahead_start("F2")        # tem de recolher F1 ANTES de abrir F2
assert list(win._diar_ahead) == ["F2"], f"so pode existir uma: {list(win._diar_ahead)}"
assert len(vivas) <= 1, f"duas threads vivas ao mesmo tempo: {vivas}"
win._diar_ahead_join_all()
print("PASS: nunca duas threads adiantadas ao mesmo tempo")

# --- transcrição que falha desiste da separação daquele arquivo -------------
# Sem isto, o arquivo sem transcrição continuaria sendo separado por minutos e
# gravaria um exclusive.json que antes nunca existiria.
segura.clear()
win._diarize_then_channels_now = trabalho_que_fica   # type: ignore[method-assign]
win._transcrever_agora = (   # type: ignore[method-assign]
    lambda *a, **k: app_service.JobResult("transcribe", 1))
win._prepare_failed = set()
resultado = win._transcribe_prepared("F3", "parakeet-pt", None, None, diarize_ahead=True)
assert resultado.failures == 1
assert "F3" not in win._diar_ahead, "a separação de um arquivo sem transcrição é abandonada"
assert vivas == [], f"a thread do arquivo que falhou continuou viva: {vivas}"
print("PASS: transcrição falhou -> a separação daquele arquivo é abandonada")


def _explode(*a, **k):
    raise RuntimeError("motor caiu")


win._transcrever_agora = _explode   # type: ignore[method-assign]
try:
    win._transcribe_prepared("F4", "parakeet-pt", None, None, diarize_ahead=True)
except RuntimeError:
    pass
assert "F4" not in win._diar_ahead and vivas == [], vivas
print("PASS: exceção na transcrição também abandona a separação")

print("PASS: toy_diar_ahead")
