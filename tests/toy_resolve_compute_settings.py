"""Toy test para runtime.resolve_compute_settings() e resolve_device('auto').

Motivacao (migracao v0.2, Fase 1.1): o default cuda/float16/batch8 chegava
intacto ao caminho CPU — CTranslate2 converte float16 para float32 em CPU
(~2x RAM, muito mais lento), travando maquinas sem GPU. A resolucao 'auto'
escolhe valores seguros por dispositivo e a coercao impede a combinacao
perigosa mesmo em configs antigas explicitas.

Sem dependencias pesadas: detect_device e forcado via runtime._detected_device.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import runtime
from transcribe_pipeline.runtime import resolve_compute_settings

# ---- resolve_compute_settings ----

# auto em CUDA -> perfil rapido
assert resolve_compute_settings("cuda", "auto", "auto") == ("float16", 8)
assert resolve_compute_settings("cuda", None, None) == ("float16", 8)

# auto em CPU -> perfil seguro
assert resolve_compute_settings("cpu", "auto", "auto") == ("int8", 2)
assert resolve_compute_settings("cpu", None, None) == ("int8", 2)

# COERCAO: float16 explicito em CPU vira int8 (CT2 converteria p/ float32)
assert resolve_compute_settings("cpu", "float16", 8) == ("int8", 4)
assert resolve_compute_settings("cpu", "int8_float16", 2) == ("int8", 2)

# Escolhas explicitas validas em CPU sao respeitadas
assert resolve_compute_settings("cpu", "float32", 1) == ("float32", 1)
assert resolve_compute_settings("cpu", "int8_float32", 2) == ("int8_float32", 2)
assert resolve_compute_settings("cpu", "int8", 4) == ("int8", 4)

# Batch em CPU e limitado a 4; em CUDA passa intacto
assert resolve_compute_settings("cpu", "int8", 32)[1] == 4
assert resolve_compute_settings("cuda", "float16", 32) == ("float16", 32)

# Valores invalidos de batch caem no default do dispositivo
assert resolve_compute_settings("cuda", "float16", "n/a")[1] == 8
assert resolve_compute_settings("cpu", "int8", "")[1] == 2
assert resolve_compute_settings("cpu", "int8", 0)[1] == 2

# CUDA respeita compute_type explicito
assert resolve_compute_settings("cuda", "int8_float16", 4) == ("int8_float16", 4)

print("PASS: resolve_compute_settings (13 cenarios)")

# ---- resolve_device('auto') ----

_saved = runtime._detected_device
try:
    runtime._detected_device = "cpu"
    # auto em maquina CPU: cair em CPU e o esperado, NAO um fallback (sem warning)
    assert runtime.resolve_device("auto") == ("cpu", False)
    assert runtime.resolve_device(None) == ("cpu", False)
    # cuda explicito em maquina CPU: fallback verdadeiro (warning legitimo)
    assert runtime.resolve_device("cuda") == ("cpu", True)
    # cpu explicito: sempre cpu
    assert runtime.resolve_device("cpu") == ("cpu", False)

    runtime._detected_device = "cuda"
    assert runtime.resolve_device("auto") == ("cuda", False)
    assert runtime.resolve_device("cuda") == ("cuda", False)
    assert runtime.resolve_device("cpu") == ("cpu", False)
finally:
    runtime._detected_device = _saved

print("PASS: resolve_device auto/explicito (7 cenarios)")
print("PASS: toy_resolve_compute_settings")
