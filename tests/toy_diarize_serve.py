"""Toy: servidor de diarizacao por lote (B1, 2026-09-02).

Um lote de N arquivos abria N processos `diarize`, cada um pagando ~35 s
(Python + import torch/pyannote + carga do modelo). Agora `transcritorio-cli
diarize-serve` carrega uma vez e atende pedidos JSON pelo stdin; a GUI fala
com ele por `diarize_client.DiarizeServer`.

Parte 1: cmd_diarize_serve em processo, com carga e execucao FALSAS
(sem torch/pyannote): READY apos a carga, PROGRESS + DONE por pedido, id
fora do manifesto = falha, excecao num pedido nao derruba o servidor, lixo
no stdin e ignorado, quit encerra, modelo que nao carrega = DONE error + rc 1.
Parte 2: DiarizeServer contra um servidor falso (script stdlib) — pedido
normal, falha, servidor que nao carrega, crash no meio, cancelamento e
comando inexistente.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = Path(tempfile.mkdtemp(prefix="toy_diarize_serve_"))
os.environ["TRANSCRITORIO_HOME"] = str(_tmp / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(_tmp / "models")

try:
    import numpy  # noqa: F401 - cli importa diarization, que importa numpy
except ImportError as exc:  # pragma: no cover - CI minimo
    print(f"SKIP: numpy ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import cli  # noqa: E402
from transcribe_pipeline.diarize_client import DiarizeServer  # noqa: E402
from transcribe_pipeline.utils import (  # noqa: E402
    DONE_JSON_PREFIX,
    PROGRESS_JSON_PREFIX,
    READY_JSON_PREFIX,
    parse_prefixed_json_line,
)

# ---------------------------------------------------------------- parte 1
rows = [
    {"interview_id": "A01", "selected": "true", "wav_path": "a.wav", "source_path": "a.m4a"},
    {"interview_id": "B02", "selected": "true", "wav_path": "b.wav", "source_path": "b.m4a"},
]
calls: list[tuple[str, ...]] = []


def fake_load(config, progress_callback=None):
    progress_callback({"event": "diarize_progress", "progress": 2, "message": "Carregando o modelo…"})
    return SimpleNamespace(pipeline=object(), model_name="fake-model", device="cpu")


def fake_rows(loaded, rows_, config, paths, ids=None, progress_callback=None, should_cancel=None):
    calls.append(tuple(ids or ()))
    if ids == ["B02"]:
        raise RuntimeError("boom no B02")
    progress_callback({"event": "diarize_progress", "progress": 55, "message": f"Separando falantes de {ids[0]} — 50%"})
    return 0


cli.load_context = lambda args: ({"model_download_token_env": "HF_TOKEN"}, SimpleNamespace(project_root=_tmp))
cli.load_diarization_pipeline = fake_load
cli.diarize_rows = fake_rows
cli.load_manifest_or_exit = lambda paths: rows
cli.per_file_configs = lambda config, paths, rows_, ids: [
    (r["interview_id"], config) for r in rows_ if r["interview_id"] in (ids or [])]

pedidos = "\n".join([
    json.dumps({"ids": ["A01"]}),
    "isto nao e json",
    json.dumps({"ids": ["ZZ9"]}),      # fora do manifesto: falha, sem chamar o pipeline
    json.dumps({"ids": ["B02"]}),      # excecao: falha, servidor segue
    "",
    json.dumps({"quit": True}),
    json.dumps({"ids": ["A01"]}),      # depois do quit: nunca roda
]) + "\n"
_stdin = sys.stdin
sys.stdin = io.StringIO(pedidos)
saida = io.StringIO()
with contextlib.redirect_stdout(saida):
    rc = cli.cmd_diarize_serve(SimpleNamespace(project=str(_tmp), config=None))
sys.stdin = _stdin
linhas = saida.getvalue().splitlines()
assert rc == 0, rc
prontos = [l for l in linhas if l.startswith(READY_JSON_PREFIX)]
assert len(prontos) == 1, linhas
pronto = parse_prefixed_json_line(prontos[0], READY_JSON_PREFIX)
assert pronto == {"device": "cpu", "model": "fake-model"}, pronto
assert any(l.startswith(PROGRESS_JSON_PREFIX) for l in linhas[: linhas.index(prontos[0])]), "progresso da carga antes do READY"
feitos = [parse_prefixed_json_line(l, DONE_JSON_PREFIX) for l in linhas if l.startswith(DONE_JSON_PREFIX)]
assert [(d["ids"], d["failures"]) for d in feitos] == [(["A01"], 0), (["ZZ9"], 1), (["B02"], 1)], feitos
assert calls == [("A01",), ("B02",)], calls
progressos = [parse_prefixed_json_line(l, PROGRESS_JSON_PREFIX) for l in linhas if l.startswith(PROGRESS_JSON_PREFIX)]
assert any("A01" in str(p.get("message")) for p in progressos), progressos
print("PASS: cmd_diarize_serve — READY, PROGRESS/DONE por pedido, falha isolada, quit")

cli.load_diarization_pipeline = lambda config, progress_callback=None: None
sys.stdin = io.StringIO(json.dumps({"ids": ["A01"]}) + "\n")
saida = io.StringIO()
with contextlib.redirect_stdout(saida):
    rc = cli.cmd_diarize_serve(SimpleNamespace(project=str(_tmp), config=None))
sys.stdin = _stdin
feitos = [parse_prefixed_json_line(l, DONE_JSON_PREFIX) for l in saida.getvalue().splitlines() if l.startswith(DONE_JSON_PREFIX)]
assert rc == 1 and len(feitos) == 1 and "error" in feitos[0], (rc, feitos)
print("PASS: cmd_diarize_serve — modelo que nao carrega = DONE error + rc 1")

# ---------------------------------------------------------------- parte 2
FAKE = _tmp / "fake_serve.py"
FAKE.write_text(textwrap.dedent('''
    import json, sys, time
    mode = sys.argv[1]
    def out(prefix, payload):
        print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)
    if mode == "noload":
        out("@DONE ", {"error": "sem modelo"}); sys.exit(1)
    out("@PROGRESS ", {"event": "diarize_progress", "progress": 2, "message": "Carregando o modelo…"})
    print("log humano qualquer — ignorado", flush=True)
    out("@READY ", {"device": "cpu", "model": "fake"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("quit"):
            break
        ids = req["ids"]
        if mode == "crash" and ids == ["B"]:
            sys.exit(3)
        if mode == "desalinhado":
            # Responde por um arquivo que NAO foi pedido: e o que acontece
            # quando uma resposta antiga sobra na fila compartilhada.
            out("@DONE ", {"ids": ["OUTRO"], "failures": 0})
            out("@DONE ", {"ids": ids, "failures": 0})
            continue
        if mode == "slow":
            for i in range(60):
                out("@PROGRESS ", {"event": "diarize_progress", "progress": i, "message": f"{ids[0]} passo {i}"})
                time.sleep(0.1)
        out("@PROGRESS ", {"event": "diarize_progress", "progress": 50, "message": f"Separando {ids[0]}"})
        out("@DONE ", {"ids": ids, "failures": 1 if ids == ["B"] else 0})
    sys.exit(0)
'''), encoding="utf-8")


def _server(mode: str) -> DiarizeServer:
    return DiarizeServer(_tmp, command=[sys.executable, "-B", str(FAKE), mode])


# normal: 2 pedidos, progresso da carga E do pedido chegam ao callback
srv = _server("normal")
assert srv.start() and srv.alive()
vistos: list[dict] = []
assert srv.run(["A"], on_progress=vistos.append) == 0
assert any("Carregando" in str(p.get("message")) for p in vistos), vistos
assert any(p.get("progress") == 50 for p in vistos), vistos
assert srv.run(["B"], on_progress=vistos.append) == 1
assert srv.served == 2
srv.stop()
assert not srv.alive() and srv._process is not None and srv._process.poll() == 0, srv._process.poll()
print("PASS: DiarizeServer — pedidos, progresso, falha por arquivo, quit limpo")

# modelo nao carrega: run devolve None (fallback) e o cliente fica morto
srv = _server("noload")
assert srv.start()
assert srv.run(["A"]) is None
assert not srv.alive() and "não carregou" in srv.failure_reason, srv.failure_reason
srv.stop()
print("PASS: DiarizeServer — modelo nao carrega = None (fallback)")

# crash no meio do 2o pedido: None, morto; o 1o pedido ja tinha valido
srv = _server("crash")
assert srv.start()
assert srv.run(["A"]) == 0
assert srv.run(["B"]) is None
assert not srv.alive() and "encerrou" in srv.failure_reason, srv.failure_reason
srv.stop()
print("PASS: DiarizeServer — crash no meio = None (fallback)")

# cancelamento: mata o servidor e devolve falha (semantica do subprocesso unico)
srv = _server("slow")
assert srv.start()
contagem = {"n": 0}


def _conta(detail: dict) -> None:
    contagem["n"] += 1


t0 = time.monotonic()
assert srv.run(["A"], on_progress=_conta, should_cancel=lambda: contagem["n"] >= 3) == 1
assert time.monotonic() - t0 < 8, "cancelar demorou demais"
assert not srv.alive() and srv.failure_reason == "cancelado"
srv.stop()
print("PASS: DiarizeServer — cancelar mata o servidor rapido")

# comando inexistente: start() False, sem excecao
assert not DiarizeServer(_tmp, command=[str(_tmp / "nao_existe.exe")]).start()
print("PASS: DiarizeServer — comando inexistente = start False")

# --- resposta de OUTRO pedido nao pode ser aceita (2026-09-05) --------------
# O protocolo atende um pedido por vez e a fila de linhas e compartilhada. Sem
# conferir os ids, um @DONE atrasado daria "pronto" a um arquivo que o servidor
# ainda nem comecou — e o render sairia sem falantes, em silencio.
srv = _server("desalinhado")
assert srv.start() and srv.wait_ready()
assert srv.run(["A"]) == 0, "tem de esperar o @DONE de A, ignorando o de OUTRO"
srv.stop()
print("PASS: DiarizeServer — @DONE de outro pedido e ignorado")

# --- @DONE {"error": ...} nunca vira sucesso --------------------------------
# `failures` ausente viraria 0 pelo .get(): o arquivo seria marcado como
# separado sem nada ter rodado.
FAKE_ERRO = _tmp / "fake_erro.py"
FAKE_ERRO.write_text(textwrap.dedent('''
    import json, sys
    def out(prefix, payload):
        print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)
    out("@READY ", {"device": "cpu", "model": "fake"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("quit"):
            break
        out("@DONE ", {"error": "pyannote caiu no meio"})
    sys.exit(0)
'''), encoding="utf-8")
srv = DiarizeServer(_tmp, command=[sys.executable, "-B", str(FAKE_ERRO)])
assert srv.start() and srv.wait_ready()
assert srv.run(["A"]) is None, "erro do servidor tem de virar fallback, nunca 0 falhas"
assert "pyannote caiu" in srv.failure_reason, srv.failure_reason
srv.stop()
print("PASS: DiarizeServer — @DONE com erro nao vira sucesso")

print("PASS: toy_diarize_serve")
