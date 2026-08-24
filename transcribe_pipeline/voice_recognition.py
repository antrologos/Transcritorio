"""Reconhecimento local de vozes recorrentes (plano D2.5+X1a, 2026-08-23).

Ancoras = (nome, interview_id, vetor) gravadas quando o usuario confirma nomes
no dialogo "De quem e esta voz?". So NOMES RECORRENTES (confirmados em >=2
arquivos distintos) sao candidatos a reconhecimento — num desenho de
entrevistas cada entrevistado aparece num unico arquivo; a voz recorrente e a
do entrevistador. Comparacao por cosseno sobre vetores L2-normalizados.

Tudo local, dentro da pasta do projeto. Stdlib pura (sem numpy/torch) para
rodar em qualquer ambiente, inclusive o CI minimo.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .config import Paths
from .utils import now_utc, read_json, write_json

ANCHORS_FILENAME = "voice_anchors.json"


def anchors_path(paths: Paths) -> Path:
    from .project_store import INTERNAL_PROJECT_DIR

    return paths.output_root / INTERNAL_PROJECT_DIR / ANCHORS_FILENAME


def embeddings_path(paths: Paths, interview_id: str) -> Path:
    return paths.diarization_dir / "embeddings" / f"{interview_id}.embeddings.json"


def write_speaker_embeddings(paths: Paths, interview_id: str, embeddings: dict[str, list[float]], model_name: str) -> None:
    path = embeddings_path(paths, interview_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "interview_id": interview_id,
            "diarization_model": model_name,
            "created_at": now_utc(),
            "dim": len(next(iter(embeddings.values()), [])),
            "embeddings": embeddings,
        },
    )


def load_speaker_embeddings(paths: Paths, interview_id: str) -> dict[str, list[float]]:
    """{SPEAKER_NN: vetor} gravado na diarizacao; {} quando ausente/corrompido."""
    path = embeddings_path(paths, interview_id)
    if not path.exists():
        return {}
    try:
        payload = read_json(path)
        result: dict[str, list[float]] = {}
        for speaker, vector in (payload.get("embeddings") or {}).items():
            if isinstance(vector, list) and vector:
                result[str(speaker)] = [float(value) for value in vector]
        return result
    except Exception:
        return {}


def load_anchors(paths: Paths) -> list[dict[str, Any]]:
    path = anchors_path(paths)
    if not path.exists():
        return []
    try:
        payload = read_json(path)
        anchors = payload.get("anchors")
        if not isinstance(anchors, list):
            return []
        return [anchor for anchor in anchors if isinstance(anchor, dict)]
    except Exception:
        return []


def save_anchors(paths: Paths, anchors: list[dict[str, Any]]) -> None:
    path = anchors_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"version": 1, "updated_at": now_utc(), "anchors": anchors})


def add_anchor(anchors: list[dict[str, Any]], name: str, interview_id: str, vector: list[float]) -> list[dict[str, Any]]:
    """Adiciona/substitui a ancora (nome, arquivo). Retorna lista nova."""
    clean = " ".join(str(name).split())
    if not clean or not vector:
        return list(anchors)
    result = [
        anchor for anchor in anchors
        if not (_same_name(anchor.get("name"), clean) and str(anchor.get("interview_id") or "") == interview_id)
    ]
    result.append({
        "name": clean,
        "interview_id": interview_id,
        "vector": [float(value) for value in vector],
        "created_at": now_utc(),
    })
    return result


def _same_name(value: Any, reference: str) -> bool:
    return " ".join(str(value or "").split()).casefold() == reference.casefold()


def recurring_names(anchors: list[dict[str, Any]]) -> list[str]:
    """Nomes confirmados em >=2 arquivos distintos (candidatos a reconhecimento)."""
    files_by_name: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for anchor in anchors:
        name = " ".join(str(anchor.get("name") or "").split())
        interview_id = str(anchor.get("interview_id") or "")
        if not name or not interview_id:
            continue
        key = name.casefold()
        files_by_name.setdefault(key, set()).add(interview_id)
        display.setdefault(key, name)
    return [display[key] for key, files in files_by_name.items() if len(files) >= 2]


def _normalized(vector: list[float]) -> list[float] | None:
    if not vector or not all(math.isfinite(value) for value in vector):
        return None
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return None
    return [value / norm for value in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    normalized_a = _normalized(a)
    normalized_b = _normalized(b)
    if normalized_a is None or normalized_b is None:
        return 0.0
    return sum(x * y for x, y in zip(normalized_a, normalized_b))


def match_voices(
    embeddings: dict[str, list[float]],
    anchors: list[dict[str, Any]],
    threshold: float,
) -> dict[str, tuple[str, float]]:
    """{SPEAKER_NN: (nome, score)} das vozes reconhecidas acima do limiar.

    Candidatos: apenas nomes recorrentes; score = melhor cosseno contra as
    ancoras daquele nome. Nunca decide sozinho — o chamador apenas PREENCHE a
    sugestao; a confirmacao e sempre do usuario.
    """
    candidates = {name.casefold(): name for name in recurring_names(anchors)}
    if not candidates or not embeddings:
        return {}
    result: dict[str, tuple[str, float]] = {}
    for speaker, vector in embeddings.items():
        best_name: str | None = None
        best_score = 0.0
        for anchor in anchors:
            key = " ".join(str(anchor.get("name") or "").split()).casefold()
            if key not in candidates:
                continue
            anchor_vector = anchor.get("vector")
            if not isinstance(anchor_vector, list):
                continue
            score = cosine_similarity(vector, [float(value) for value in anchor_vector])
            if score > best_score:
                best_score = score
                best_name = candidates[key]
        if best_name is not None and best_score >= float(threshold):
            result[speaker] = (best_name, best_score)
    return result
