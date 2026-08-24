"""Toy test para o protocolo @PROGRESS (diarizacao em subprocesso, v0.2).

Cobre: utils.parse_progress_json_line (parser puro), runtime.cli_command
(launcher dev-mode) e cmd_diarize --progress-json (linhas estruturadas na
stdout com o mesmo contrato de eventos do progress_callback in-process).

Dependencia condicional: cli.py importa diarization.py, que importa numpy no
topo. Se numpy nao estiver disponivel (CI minimo), a parte do CLI e PULADA —
o parser e o launcher continuam testados.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.utils import PROGRESS_JSON_PREFIX, parse_progress_json_line

# ---- parser puro ----
assert parse_progress_json_line('@PROGRESS {"event": "diarize_progress", "progress": 42, "message": "x"}') == {
    "event": "diarize_progress", "progress": 42, "message": "x",
}
assert parse_progress_json_line("  @PROGRESS {\"progress\": 1}  ") == {"progress": 1}
assert parse_progress_json_line("[12:00:01] [diarize] Carregando audio...") is None
assert parse_progress_json_line("") is None
assert parse_progress_json_line("@PROGRESS nao-e-json") is None
assert parse_progress_json_line('@PROGRESS [1, 2]') is None  # nao-dict
print("PASS: parse_progress_json_line (6 cenarios)")

# ---- launcher dev-mode ----
from transcribe_pipeline.runtime import cli_command

cmd = cli_command("--project", "X", "diarize", "--ids", "A01", "--progress-json")
assert cmd[0] == sys.executable, cmd
assert cmd[1:4] == ["-B", "-m", "transcribe_pipeline"], "dev-mode usa -B -m (sem __pycache__ no Dropbox)"
assert cmd[-1] == "--progress-json", cmd
print("PASS: cli_command dev-mode")

# ---- cmd_diarize --progress-json (skip se numpy ausente) ----
try:
    from transcribe_pipeline import cli
except ModuleNotFoundError as exc:
    print(f"SKIP: parte do CLI pulada (dependencia ausente: {exc.name})")
    print("PASS: toy_progress_json")
    raise SystemExit(0)

_events = [
    {"event": "diarize_progress", "progress": 20, "message": "Modelo carregado"},
    {"event": "diarize_progress", "progress": 90, "message": "Gravando"},
]


def _fake_run(rows, config, paths, ids=None, dry_run=False, progress_callback=None, **kw):
    for ev in _events:
        if progress_callback is not None:
            progress_callback(ev)
    print("linha humana qualquer no meio do output")
    return 0


# cmd_diarize resolve metadados per-arquivo desde o fix D1.1 (2026-08-23):
# paths precisa de um project_root real (sem metadados.csv -> config intacto)
# e o manifest precisa conter a entrevista selecionada para o runner rodar.
_tmp = tempfile.TemporaryDirectory()
_paths = types.SimpleNamespace(project_root=Path(_tmp.name))
_rows = [{"interview_id": "A01", "selected": "true", "wav_path": "a.wav", "source_path": "a.mp3"}]

cli.run_pyannote_diarization = _fake_run
cli.load_context = lambda args: ({}, _paths)
cli.load_manifest_or_exit = lambda paths: _rows

args = types.SimpleNamespace(
    ids=["A01"], dry_run=False, progress_json=True,
    diarization_num_speakers=None, diarize_model=None,
    min_speakers=None, max_speakers=None, project=None, config=None,
)
buf = io.StringIO()
with redirect_stdout(buf):
    rc = cli.cmd_diarize(args)
assert rc == 0
lines = buf.getvalue().splitlines()
parsed = [parse_progress_json_line(l) for l in lines]
got = [p for p in parsed if p is not None]
assert got == _events, got
assert any(p is None for p in parsed), "linhas humanas devem coexistir sem quebrar o parser"
print("PASS: cmd_diarize --progress-json emite eventos parseaveis")

# sem a flag: nenhuma linha @PROGRESS
args.progress_json = False
buf2 = io.StringIO()
with redirect_stdout(buf2):
    cli.cmd_diarize(args)
assert PROGRESS_JSON_PREFIX not in buf2.getvalue()
print("PASS: sem --progress-json nao ha linhas @PROGRESS")

_tmp.cleanup()
print("PASS: toy_progress_json")
