"""Toy test: parametro env opcional de utils.run_command_stream."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.utils import run_command_stream, secure_subprocess_env

PY = [sys.executable, "-c", "import os; print(os.environ.get('TOY_X', '<ausente>'))"]

# --- sem env: comportamento antigo (ambiente do processo, sanitizado) ---
os.environ["TOY_X"] = "herdado"
try:
    r = run_command_stream(PY)
    assert r.returncode == 0
    assert "herdado" in r.stdout, r.stdout
finally:
    del os.environ["TOY_X"]
print("PASS: default herda o ambiente sanitizado")

# --- com env: o filho ve EXATAMENTE o que foi passado ---
custom = secure_subprocess_env()
custom["TOY_X"] = "customizado"
r = run_command_stream(PY, env=custom)
assert r.returncode == 0
assert "customizado" in r.stdout, r.stdout

sem_var = secure_subprocess_env()
sem_var.pop("TOY_X", None)
r = run_command_stream(PY, env=sem_var)
assert "<ausente>" in r.stdout, r.stdout
print("PASS: env explicito substitui o ambiente do filho")

# --- env explicito nao pode reintroduzir segredos por acidente:
# secure_subprocess_env continua sendo a base recomendada ---
os.environ["HF_TOKEN"] = "segredo"
try:
    base = secure_subprocess_env()
    assert "HF_TOKEN" not in base
finally:
    del os.environ["HF_TOKEN"]
print("PASS: base recomendada continua sem segredos")

print("PASS: toy_run_command_env")
