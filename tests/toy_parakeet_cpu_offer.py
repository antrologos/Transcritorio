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
    from transcribe_pipeline.review_studio_qt import (
        engine_offer_due,
        languages_outside_pt,
        parakeet_cpu_offer_due,
    )
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

# 2026-09-02: "automático" (nenhum idioma declarado) NAO bloqueia mais —
# beta tester com arquivo em Automático nunca via a oferta e o guard do
# motor barrava o lote. Vazios tambem sao ignorados.
assert languages_outside_pt(["pt"]) == set()
assert languages_outside_pt(["automático"]) == set()
assert languages_outside_pt(["pt", "automático", ""]) == set()
assert languages_outside_pt(["pt", "es"]) == {"es"}
assert languages_outside_pt(["en"]) == {"en"}
assert parakeet_cpu_offer_due("cpu", None, ["automático"], False, platform="win32") is True
assert parakeet_cpu_offer_due("cpu", None, ["pt", "automático"], False, platform="win32") is True

# Faixa da lista (migracao visivel): sem GPU + Whisper + nao recusada + ocioso
assert engine_offer_due("cpu", None, "win32", False, False) is True
assert engine_offer_due("cpu", "whisper", "linux", False, False) is True
assert engine_offer_due("cuda", None, "win32", False, False) is False
assert engine_offer_due("cpu", "parakeet_onnx", "win32", False, False) is False
assert engine_offer_due("cpu", None, "darwin", False, False) is False
assert engine_offer_due("cpu", None, "win32", True, False) is False
assert engine_offer_due("cpu", None, "win32", False, True) is False
print("PASS: languages_outside_pt + engine_offer_due")

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
