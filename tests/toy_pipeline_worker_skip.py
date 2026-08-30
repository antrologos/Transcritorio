"""Toy test: skip-and-continue no lote (SL-C5). Offscreen, sincrono.

Bug original: os steps de N arquivos eram achatados numa lista unica e
a PRIMEIRA falha nao-opcional abortava o lote inteiro — os arquivos
seguintes nem comecavam (pendencia antiga "skip-and-continue").

Regra nova: falha num step COM grupo (interview_id) pula os steps
restantes DAQUELE arquivo e segue para o proximo; steps SEM grupo
(jobs de passo unico) mantem o aborto antigo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import PipelineWorker

app = QApplication.instance() or QApplication([])


def _roda(steps, weights=None):
    worker = PipelineWorker("Teste", steps, weights=weights)
    oks, errs, msgs = [], [], []
    worker.finished_ok.connect(oks.append)
    worker.failed.connect(errs.append)
    worker.progress.connect(lambda m, p: msgs.append((m, p)))
    worker.run()  # sincrono de proposito (QThread.run e um metodo comum)
    return oks, errs, msgs


executado: list[str] = []


def _ok(nome):
    def f():
        executado.append(nome)
    return f


def _boom(nome):
    def f():
        executado.append(nome)
        raise RuntimeError("explodiu")
    return f


# --- falha no meio do arquivo A: resto de A pulado, B roda inteiro ---
executado.clear()
steps = [
    ("A passo1", _ok("A1"), False, "A"),
    ("A passo2", _boom("A2"), False, "A"),
    ("A passo3", _ok("A3"), False, "A"),
    ("B passo1", _ok("B1"), False, "B"),
    ("B passo2", _ok("B2"), False, "B"),
]
oks, errs, _msgs = _roda(steps)
assert executado == ["A1", "A2", "B1", "B2"], executado  # A3 pulado
assert not errs, errs                                    # falha parcial nao e vermelho
assert oks and "1 arquivo(s) com falha" in oks[0], oks
assert "Fila de tarefas" in oks[0], oks

# --- todos os arquivos falham: ai sim o lote termina em vermelho ---
executado.clear()
steps = [
    ("A passo1", _boom("A1"), False, "A"),
    ("A passo2", _ok("A2"), False, "A"),
    ("B passo1", _boom("B1"), False, "B"),
]
oks, errs, _msgs = _roda(steps)
assert executado == ["A1", "B1"], executado
assert not oks and errs and "2 arquivo(s) com falha" in errs[0], (oks, errs)
# o ERRO REAL nao pode se perder no resumo (lote de 1 arquivo era o pior
# caso: o dialogo vermelho dizia so "concluido com 1 falha")
assert "explodiu" in errs[0], errs

# --- step SEM grupo falhando: aborta como sempre (jobs de passo unico) ---
executado.clear()
steps = [
    ("passo1", _boom("S1"), False),
    ("passo2", _ok("S2"), False),
]
oks, errs, _msgs = _roda(steps)
assert executado == ["S1"], executado
assert errs and not oks

# --- sem falhas: mensagem de sempre ---
executado.clear()
oks, errs, _msgs = _roda([("A passo1", _ok("A1"), False, "A")])
assert oks == ["Teste concluido."] and not errs, (oks, errs)

# --- a barra nao congela: pesos dos steps pulados sao somados ---
executado.clear()
steps = [
    ("A passo1", _boom("A1"), False, "A"),
    ("A passo2", _ok("A2"), False, "A"),
    ("B passo1", _ok("B1"), False, "B"),
]
oks, errs, msgs = _roda(steps, weights=[10, 80, 10])
percentuais = [p for _m, p in msgs]
assert max(percentuais) == 100, percentuais  # chegou ao fim mesmo pulando 80%

print("PASS: toy_pipeline_worker_skip")
