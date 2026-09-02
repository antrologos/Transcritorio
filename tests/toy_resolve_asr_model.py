"""Toy: resolve_asr_model nunca troca de MOTOR em silencio.

Revisao 2026-09-02: com o TAGARELA como padrao de fabrica, um Whisper
configurado mas nao baixado caia em "parakeet-pt" e o repo ONNX ia parar
no whisperx CLI. Regra: o substituto e do mesmo motor do configurado (o
padrao primeiro, depois a ordem do catalogo); sem candidato, devolve o
configurado (o runner acusa a falta). Sem rede: cache simulado.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import model_manager as mm  # noqa: E402

_orig_snapshot = mm.cached_snapshot_path
_orig_weights = mm._snapshot_has_weights


def _instalar(*chaves: str) -> None:
    repos = {mm.ASR_VARIANTS[k]["repo"] for k in chaves}
    mm.cached_snapshot_path = lambda repo, cache_dir=None, revision=None: Path("x") if repo in repos else None  # type: ignore[assignment]
    mm._snapshot_has_weights = lambda path: True  # type: ignore[assignment]


try:
    assert mm.DEFAULT_ASR_VARIANT == "parakeet-pt"
    from transcribe_pipeline.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["asr_model"] == mm.DEFAULT_ASR_VARIANT, "config.py e model_manager divergem"

    # 1) configurado instalado -> ele mesmo
    _instalar("small", "parakeet-pt")
    assert mm.resolve_asr_model("small") == "small"
    assert mm.resolve_asr_model("parakeet-pt") == "parakeet-pt"

    # 2) Whisper ausente com {small, parakeet-pt} -> outro Whisper, nunca o TAGARELA
    assert mm.resolve_asr_model("large-v3-turbo") == "small"
    _instalar("medium", "small", "parakeet-pt")
    assert mm.resolve_asr_model("large-v3-turbo") == "medium"   # ordem do catalogo

    # 3) TAGARELA ausente com so Whisper -> devolve o configurado (runner acusa)
    _instalar("small")
    assert mm.resolve_asr_model("parakeet-pt") == "parakeet-pt"

    # 4) nada instalado -> configurado
    _instalar()
    assert mm.resolve_asr_model("large-v3-turbo") == "large-v3-turbo"

    # 5) modelo desconhecido (caminho customizado) passa direto
    assert mm.resolve_asr_model("/meu/modelo") == "/meu/modelo"
finally:
    mm.cached_snapshot_path = _orig_snapshot  # type: ignore[assignment]
    mm._snapshot_has_weights = _orig_weights  # type: ignore[assignment]

print("PASS: toy_resolve_asr_model")
