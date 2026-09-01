"""Toy: decisao pura da oferta TAGARELA-em-CPU (parakeet_cpu_offer_due).

Gate da mudanca 2026-09-01 (TAGARELA padrao em maquinas sem GPU): quem
JA esta instalado com Whisper e vai transcrever em CPU recebe UMA oferta
de troca — nunca em GPU, nunca quando o lote tem idioma fora do pt,
nunca depois de recusa definitiva, e nunca quando o motor ja e o
TAGARELA.

Depende de PySide6 (importa review_studio_qt); o CI ja o instala.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import parakeet_cpu_offer_due
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

# Caso alvo: Whisper em CPU, lote em pt, sem recusa -> oferece
assert parakeet_cpu_offer_due("cpu", None, ["pt"], False, platform="win32") is True
assert parakeet_cpu_offer_due("cpu", None, ["pt"], False, platform="linux") is True
# Sem idioma declarado = default pt do projeto -> oferece
assert parakeet_cpu_offer_due("cpu", None, [], False, platform="win32") is True
assert parakeet_cpu_offer_due("cpu", None, ["pt", ""], False, platform="win32") is True

# Nunca em GPU (o Whisper turbo e o melhor motor la)
assert parakeet_cpu_offer_due("cuda", None, ["pt"], False, platform="win32") is False
# Mac: o Whisper tem a rota Metal/MLX mesmo com device coeragido p/ cpu
assert parakeet_cpu_offer_due("cpu", None, ["pt"], False, platform="darwin") is False
assert parakeet_cpu_offer_due("mps", None, ["pt"], False, platform="win32") is False

# Motor ja e o TAGARELA -> nada a oferecer
assert parakeet_cpu_offer_due("cpu", "parakeet_onnx", ["pt"], False, platform="win32") is False

# Lote com idioma fora do pt -> o TAGARELA nao serve
assert parakeet_cpu_offer_due("cpu", None, ["pt", "es"], False, platform="win32") is False
assert parakeet_cpu_offer_due("cpu", None, ["en"], False, platform="win32") is False

# Recusa definitiva persiste
assert parakeet_cpu_offer_due("cpu", None, ["pt"], True, platform="win32") is False

print("PASS: toy_parakeet_cpu_offer (11 casos)")
