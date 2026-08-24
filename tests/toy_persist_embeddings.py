"""Toy test para diarization._persist_speaker_embeddings (plano X1a, item 11).

Mapeamento embeddings[s] <-> labels()[s] confirmado empiricamente no
community-1 (pyannote 4.0). Aqui o output e mockado: valida o mapeamento, o
descarte de vetores nao-finitos e que nada e gravado sem dados.

Importa diarization (numpy no topo); skip condicional no CI minimo.
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.diarization import _persist_speaker_embeddings
    from transcribe_pipeline.voice_recognition import embeddings_path, load_speaker_embeddings
    from transcribe_pipeline.config import ensure_directories, load_config, make_paths
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


class FakeAnnotation:
    def __init__(self, labels: list[str]) -> None:
        self._labels = labels

    def labels(self) -> list[str]:
        return list(self._labels)


class FakeOutput:
    def __init__(self, labels: list[str], embeddings: list[list[float]]) -> None:
        self.speaker_diarization = FakeAnnotation(labels)
        self.speaker_embeddings = embeddings


with tempfile.TemporaryDirectory() as tmp:
    config = load_config(None)
    config["project_root"] = "."
    paths = make_paths(config, base_dir=Path(tmp))
    ensure_directories(paths)

    # Mapeamento por indice + descarte do vetor NaN (falante sem fala util)
    output = FakeOutput(
        ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
        [[1.0, 0.0], [float("nan"), 1.0], [0.0, 1.0]],
    )
    _persist_speaker_embeddings(paths, "T01", output, "pyannote/community-1")
    saved = load_speaker_embeddings(paths, "T01")
    assert saved == {"SPEAKER_00": [1.0, 0.0], "SPEAKER_02": [0.0, 1.0]}, saved
    assert all(math.isfinite(v) for vec in saved.values() for v in vec)
    print("PASS: mapeamento por indice + NaN descartado")

    # Menos embeddings que labels: nao estoura, grava o que da
    output = FakeOutput(["SPEAKER_00", "SPEAKER_01"], [[0.5, 0.5]])
    _persist_speaker_embeddings(paths, "T02", output, "m")
    assert set(load_speaker_embeddings(paths, "T02")) == {"SPEAKER_00"}
    print("PASS: embeddings a menos nao estoura")

    # Sem nada util: arquivo nem e criado
    output = FakeOutput(["SPEAKER_00"], [[float("inf"), 1.0]])
    _persist_speaker_embeddings(paths, "T03", output, "m")
    assert not embeddings_path(paths, "T03").exists()
    # Output sem os atributos (versao futura do pyannote): no-op silencioso
    _persist_speaker_embeddings(paths, "T04", object(), "m")
    assert not embeddings_path(paths, "T04").exists()
    print("PASS: sem dados uteis / sem atributos -> no-op")

print()
print("PASS: toy_persist_embeddings")
