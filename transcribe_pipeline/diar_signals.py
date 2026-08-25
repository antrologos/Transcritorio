"""Captura de sinais intermediarios da diarizacao (hook do pyannote).

O pipeline community-1 calcula e descarta artefatos frame-level uteis:
a contagem instantanea de falantes (revela silencios e sobreposicoes
segundo o proprio modelo) e os embeddings por chunk (permitem medir a
margem de confianca da atribuicao de voz de cada trecho — o equivalente
pratico do soft_clusters do VBx, sem acessar API privada).

Este modulo captura esses artefatos via o parametro hook do pipeline e
persiste apenas DERIVADOS compactos em
03_diarization/signals/{id}.signals.json — nunca as matrizes brutas
(os posteriors crus seriam ~100 MB por hora de audio).

Consumidor: boundary_check (flags de sobreposicao e de atribuicao
incerta). Falha aqui nunca derruba a diarizacao: as chamadas em
diarization.py sao protegidas por try/except, mesmo padrao dos
embeddings de falante.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from .config import Paths
from .utils import now_utc, write_json

MIN_REGION_SECONDS = 0.3
SIGNALS_VERSION = 1


def signals_path(paths: Paths, interview_id: str):
    return paths.diarization_dir / "signals" / f"{interview_id}.signals.json"


class SignalCollector:
    """Guarda o ULTIMO artefato de cada step relevante do hook pyannote.

    Chamadas parciais de progresso (completed < total) e artefatos None
    sao ignorados; o pipeline chama o hook varias vezes por step.
    """

    STEPS = ("segmentation", "speaker_counting", "embeddings")

    def __init__(self) -> None:
        self.artifacts: dict[str, Any] = {}

    def hook(
        self,
        step_name: str,
        step_artifact: Any = None,
        *,
        file: Any = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        if step_artifact is None or step_name not in self.STEPS:
            return
        if completed is not None and total is not None and completed < total:
            return
        self.artifacts[step_name] = step_artifact


def regions_from_counts(
    counts: Any,
    frame_start: float,
    frame_step: float,
    predicate: Callable[[float], bool],
    min_duration: float = MIN_REGION_SECONDS,
) -> list[tuple[float, float]]:
    """Intervalos onde predicate(count) vale, por run-length sobre os frames."""
    regions: list[tuple[float, float]] = []
    run_start: int | None = None
    n = len(counts)
    for i in range(n + 1):
        ok = i < n and bool(predicate(float(counts[i])))
        if ok and run_start is None:
            run_start = i
        elif not ok and run_start is not None:
            t0 = frame_start + run_start * frame_step
            t1 = frame_start + i * frame_step
            if t1 - t0 >= min_duration:
                regions.append((round(t0, 3), round(t1, 3)))
            run_start = None
    return regions


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return matrix / np.where(norms > 0, norms, np.nan)


def segment_margins(
    chunk_embeddings: np.ndarray,
    chunk_starts: np.ndarray,
    chunk_duration: float,
    centroids: np.ndarray,
    labels: list[str],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Margem de confianca da atribuicao de voz por segmento exclusive.

    Para um segmento do falante S: entre os chunks que cobrem o centro do
    segmento, escolhe em cada chunk o falante LOCAL cujo embedding e mais
    parecido com o centroide de S (proxy do canal local correto); a margem
    do chunk e cos(., S) - max cos(., outros centroides). O segmento recebe
    a MEDIANA das margens. Margem alta = voz claramente daquele falante;
    margem baixa/negativa = trecho ambiguo entre vozes.
    """
    if len(labels) < 2:
        return []  # com um falante nao ha "segundo colocado"
    embeddings = _normalize_rows(np.asarray(chunk_embeddings, dtype=np.float64))
    centroid_matrix = _normalize_rows(np.asarray(centroids, dtype=np.float64))
    label_index = {label: i for i, label in enumerate(labels)}
    results: list[dict[str, Any]] = []
    for segment in segments:
        speaker = str(segment.get("speaker"))
        s_idx = label_index.get(speaker)
        if s_idx is None or not np.all(np.isfinite(centroid_matrix[s_idx])):
            continue
        mid = (float(segment["start"]) + float(segment["end"])) / 2.0
        covering = np.nonzero(
            (chunk_starts <= mid) & (mid <= chunk_starts + chunk_duration)
        )[0]
        margins: list[float] = []
        for c in covering:
            sims = embeddings[c] @ centroid_matrix.T  # (local, spk)
            own = sims[:, s_idx]
            if not np.any(np.isfinite(own)):
                continue
            local = int(np.nanargmax(own))
            others = np.delete(sims[local], s_idx)
            others = others[np.isfinite(others)]
            if not math.isfinite(float(own[local])) or others.size == 0:
                continue
            margins.append(float(own[local]) - float(np.max(others)))
        if margins:
            results.append(
                {
                    "start": round(float(segment["start"]), 3),
                    "end": round(float(segment["end"]), 3),
                    "speaker": speaker,
                    "margin": round(float(np.median(margins)), 4),
                }
            )
    return results


def speaker_stats(margins: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Media/desvio/n das margens por falante (qualidade da voz no arquivo)."""
    by_speaker: dict[str, list[float]] = {}
    for item in margins:
        by_speaker.setdefault(str(item["speaker"]), []).append(float(item["margin"]))
    stats = {}
    for speaker, values in by_speaker.items():
        arr = np.asarray(values, dtype=np.float64)
        stats[speaker] = {
            "margin_mean": round(float(arr.mean()), 4),
            "margin_std": round(float(arr.std()), 4),
            "segments": int(arr.size),
        }
    return stats


def persist_signals(
    paths: Paths,
    interview_id: str,
    collector: SignalCollector,
    output: Any,
    model_name: str,
) -> None:
    """Deriva e grava o signals.json a partir dos artefatos capturados.

    Usa as annotations PRE pos-processamento do output (mesma decisao dos
    embeddings de falante): os tempos servem de indice acustico e o
    boundary_check mapeia por interseccao com os turnos finais.
    """
    payload: dict[str, Any] = {
        "interview_id": interview_id,
        "diarization_model": model_name,
        "created_at": now_utc(),
        "version": SIGNALS_VERSION,
        "silences": [],
        "overlaps": [],
        "segment_margins": [],
        "speaker_stats": {},
    }

    counting = collector.artifacts.get("speaker_counting")
    data = getattr(counting, "data", None)
    window = getattr(counting, "sliding_window", None)
    if data is not None and window is not None:
        counts = np.asarray(data).reshape(len(data), -1)[:, 0]
        start, step = float(window.start), float(window.step)
        payload["silences"] = regions_from_counts(counts, start, step, lambda v: v <= 0)
        payload["overlaps"] = regions_from_counts(counts, start, step, lambda v: v >= 2)

    embeddings = collector.artifacts.get("embeddings")
    segmentation = collector.artifacts.get("segmentation")
    centroids = getattr(output, "speaker_embeddings", None)
    annotation = getattr(output, "speaker_diarization", None)
    exclusive = getattr(output, "exclusive_speaker_diarization", None)
    chunk_window = getattr(segmentation, "sliding_window", None)
    if (
        embeddings is not None
        and centroids is not None
        and annotation is not None
        and exclusive is not None
        and chunk_window is not None
    ):
        labels = [str(label) for label in annotation.labels()]
        chunks = np.asarray(embeddings, dtype=np.float64)
        chunk_starts = np.asarray(
            [float(chunk_window.start) + i * float(chunk_window.step) for i in range(chunks.shape[0])]
        )
        segments = [
            {"start": float(segment.start), "end": float(segment.end), "speaker": str(label)}
            for segment, _track, label in exclusive.itertracks(yield_label=True)
        ]
        margins = segment_margins(
            chunks, chunk_starts, float(chunk_window.duration),
            np.asarray(centroids, dtype=np.float64), labels, segments,
        )
        payload["segment_margins"] = margins
        payload["speaker_stats"] = speaker_stats(margins)

    target = signals_path(paths, interview_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)
