"""Analise de canais de audio (fase 4, nucleo): microfones separados.

Quando os canais da fonte carregam microfones distintos (lapelas,
gravador 2-mic), a dominancia de energia por canal e um sinal de
atribuicao de falante muito mais forte que qualquer pos-processamento.
Este estagio roda ENTRE a diarizacao e o render: le os WAVs por canal
({id}.ch{n}.wav, extraidos no preparo), decide se os canais sao
informativos (vs estereo-ambiente, que segue o fluxo atual) e, se sim,
produz segmentos {start, end, speaker} no contrato do render
(diarization_source "channels"), com rotulos SPEAKER_NN casados aos do
pyannote por centroides de embedding — nomeacao de vozes, ancoras e
speakers_map continuam coerentes. Fail-soft em tudo: sem canais, sem
pyannote ou com torch indisponivel, degrada sem derrubar o lote.

numpy so dentro das funcoes (o CI minimo roda o toy com skip); torch
so na fusao de rotulos, mesmo padrao do boundary_check.
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from .utils import append_jsonl, now_utc, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

CHANNELS_VERSION = 1
MAX_CHANNELS = 8
WINDOW_SECONDS = 0.5
ACTIVITY_THRESHOLD_RATIO = 0.15   # janela ativa: RMS >= 15% do pico global
ENVELOPE_CORR_MAX = 0.98          # correlacao >= isto = estereo-ambiente
DOMINANT_FRACTION_MIN = 0.20      # fracao minima de janelas dominadas
MERGE_GAP_SECONDS = 0.4
MIN_SEGMENT_SECONDS = 0.3
EMBED_WINDOW_SECONDS = 2.0        # janela de embedding por segmento dominado
EMBED_MIN_SEGMENT_SECONDS = 1.0   # so segmentos com corpo dao centroide
EMBED_SEGMENTS_PER_CHANNEL = 6


def channels_json_path(paths: Paths, interview_id: str) -> Path:
    return paths.diarization_dir / "json" / f"{interview_id}.channels.json"


def channel_wav_paths(paths: Paths, row: dict[str, str]) -> list[Path]:
    """{id}.ch{n}.wav existentes, em ordem; [] se nao ha pelo menos 2."""
    wav_path = row.get("wav_path") or ""
    if not wav_path:
        return []
    wav = paths.project_root / wav_path
    found: list[Path] = []
    for index in range(MAX_CHANNELS):
        candidate = wav.with_name(f"{wav.stem}.ch{index}.wav")
        if not candidate.exists():
            break
        found.append(candidate)
    return found if len(found) >= 2 else []


def rms_envelopes(channel_paths: list[Path], window_seconds: float = WINDOW_SECONDS) -> list[list[float]]:
    """Envelope RMS por janela, por canal, truncado ao menor canal."""
    import numpy as np

    envelopes: list[list[float]] = []
    for path in channel_paths:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            n_channels = handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels)[:, 0]
        window = max(1, int(rate * window_seconds))
        n_windows = len(samples) // window
        if n_windows == 0:
            envelopes.append([])
            continue
        trimmed = samples[: n_windows * window].reshape(n_windows, window)
        envelopes.append(np.sqrt((trimmed ** 2).mean(axis=1)).tolist())
    if not envelopes:
        return []
    shortest = min(len(env) for env in envelopes)
    return [env[:shortest] for env in envelopes]


def analyze_envelopes(
    envelopes: list[list[float]], dominance_threshold: float = 0.65,
) -> tuple[str, float, float, list[int | None]]:
    """(decisao, correlacao, fracao_dominante, rotulo_por_janela).

    decisao: "informative" (canais carregam microfones distintos) ou
    "ambience" (estereo-ambiente/duvida -> fluxo atual). rotulo[w] =
    indice do canal dominante, ou None (janela inativa ou ambigua).
    """
    import numpy as np

    matrix = np.array(envelopes, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] < 2 or matrix.shape[1] == 0:
        return "ambience", 1.0, 0.0, []
    peak = float(matrix.max())
    if peak <= 0:
        return "ambience", 1.0, 0.0, []
    active = matrix.max(axis=0) >= (ACTIVITY_THRESHOLD_RATIO * peak)
    correlations: list[float] = []
    for left in range(matrix.shape[0]):
        for right in range(left + 1, matrix.shape[0]):
            if matrix[left].std() > 0 and matrix[right].std() > 0:
                correlations.append(float(np.corrcoef(matrix[left], matrix[right])[0, 1]))
    correlation = max(correlations) if correlations else 1.0
    totals = np.maximum(matrix.sum(axis=0), 1e-9)
    dominance = matrix.max(axis=0) / totals
    labels: list[int | None] = []
    active_windows = 0
    dominant_windows = 0
    for index in range(matrix.shape[1]):
        if not active[index]:
            labels.append(None)
            continue
        active_windows += 1
        if dominance[index] >= dominance_threshold:
            dominant_windows += 1
            labels.append(int(matrix[:, index].argmax()))
        else:
            labels.append(None)
    dominant_fraction = (dominant_windows / active_windows) if active_windows else 0.0
    informative = correlation < ENVELOPE_CORR_MAX and dominant_fraction >= DOMINANT_FRACTION_MIN
    return ("informative" if informative else "ambience", correlation, dominant_fraction, labels)


def segments_from_labels(
    labels: list[int | None],
    window_seconds: float = WINDOW_SECONDS,
    merge_gap: float = MERGE_GAP_SECONDS,
    min_segment: float = MIN_SEGMENT_SECONDS,
) -> list[dict[str, Any]]:
    """[{start, end, channel}] de janelas rotuladas consecutivas (pura)."""
    runs: list[list[float]] = []
    current: list[float] | None = None
    for index, label in enumerate(labels):
        if label is None:
            continue
        if current is not None and current[0] == label and (index - current[2]) * window_seconds <= merge_gap:
            current[2] = index + 1
        else:
            if current is not None:
                runs.append(current)
            current = [label, index, index + 1]
    if current is not None:
        runs.append(current)
    segments: list[dict[str, Any]] = []
    for channel, first, last in runs:
        start = first * window_seconds
        end = last * window_seconds
        if end - start >= min_segment:
            segments.append({"start": round(start, 3), "end": round(end, 3), "channel": int(channel)})
    return segments


def mean_embedding(vectors: list[list[float]]) -> list[float] | None:
    """Centroide L2-normalizado (pura, stdlib); None se vazio/inconsistente."""
    if not vectors:
        return None
    dim = len(vectors[0])
    accumulator = [0.0] * dim
    for vector in vectors:
        if len(vector) != dim:
            return None
        for index, value in enumerate(vector):
            accumulator[index] += float(value)
    mean = [value / len(vectors) for value in accumulator]
    norm = math.sqrt(sum(value * value for value in mean))
    if not math.isfinite(norm) or norm <= 0:
        return None
    return [value / norm for value in mean]


def _speaker_index(label: str) -> int | None:
    match = re.match(r"^SPEAKER_(\d+)$", str(label))
    return int(match.group(1)) if match else None


def channel_speaker_map(
    n_channels: int,
    channel_centroids: dict[int, list[float]],
    speaker_centroids: dict[str, list[float]],
) -> dict[int, str]:
    """{canal: SPEAKER_NN} — 1:1 guloso por melhor cosseno global.

    Canais sem par (ou sem centroide, ou sem pyannote) ganham rotulos
    novos apos o maior indice existente; sem pyannote nenhum, os
    rotulos seguem a ordem do canal (mapeamento humano e posicional).
    """
    from .voice_recognition import cosine_similarity

    mapping: dict[int, str] = {}
    used: set[str] = set()
    pairs: list[tuple[float, int, str]] = []
    for channel, channel_vector in channel_centroids.items():
        for speaker, speaker_vector in speaker_centroids.items():
            pairs.append((cosine_similarity(channel_vector, speaker_vector), channel, speaker))
    for _score, channel, speaker in sorted(pairs, key=lambda item: (-item[0], item[1], item[2])):
        if channel in mapping or speaker in used:
            continue
        mapping[channel] = speaker
        used.add(speaker)
    known = [index for index in (_speaker_index(s) for s in speaker_centroids) if index is not None]
    next_index = (max(known) + 1) if known else 0
    for channel in range(n_channels):
        if channel in mapping:
            continue
        mapping[channel] = f"SPEAKER_{next_index:02d}"
        next_index += 1
    return mapping


def _channel_centroids(
    mono_wav: Path, segments: list[dict[str, Any]],
) -> dict[int, list[float]]:
    """Centroide de voz por canal, embedando o WAV MONO nos trechos em
    que o canal domina (torch/pyannote; excecoes viram {} no chamador)."""
    from .boundary_check import embed_window, load_embedder
    from .diarization import _load_wav_as_tensor

    audio = _load_wav_as_tensor(mono_wav)
    embedder = load_embedder()
    by_channel: dict[int, list[dict[str, Any]]] = {}
    for segment in segments:
        by_channel.setdefault(int(segment["channel"]), []).append(segment)
    centroids: dict[int, list[float]] = {}
    for channel, channel_segments in by_channel.items():
        bodies = [s for s in channel_segments
                  if s["end"] - s["start"] >= EMBED_MIN_SEGMENT_SECONDS]
        bodies.sort(key=lambda s: s["start"] - s["end"])  # mais longos primeiro
        vectors: list[list[float]] = []
        for segment in bodies[:EMBED_SEGMENTS_PER_CHANNEL]:
            vector = embed_window(
                embedder, audio["waveform"], audio["sample_rate"],
                segment["start"], min(segment["start"] + EMBED_WINDOW_SECONDS, segment["end"]))
            if vector is not None:
                vectors.append(vector)
        centroid = mean_embedding(vectors)
        if centroid is not None:
            centroids[channel] = centroid
    return centroids


def run_channel_analysis(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Analisa canais das entrevistas selecionadas; retorna falhas."""
    if not bool(config.get("channels_analysis", True)):
        return 0
    dominance_threshold = float(config.get("channels_dominance_threshold") or 0.65)
    failures = 0
    targets = selected_rows(rows, ids)
    for position, row in enumerate(targets):
        if should_cancel is not None and should_cancel():
            break
        interview_id = row["interview_id"]
        channel_paths = channel_wav_paths(paths, row)
        if not channel_paths:
            continue  # mono ou sem extracao: nada a fazer, em silencio
        if progress_callback is not None:
            progress_callback({
                "event": "channels_progress",
                "progress": int(100 * position / max(1, len(targets))),
                "message": f"Medindo a energia dos {len(channel_paths)} canais de {interview_id}...",
            })
        try:
            envelopes = rms_envelopes(channel_paths)
            decision, correlation, dominant_fraction, labels = analyze_envelopes(
                envelopes, dominance_threshold=dominance_threshold)
            payload: dict[str, Any] = {
                "interview_id": interview_id,
                "created_at": now_utc(),
                "version": CHANNELS_VERSION,
                "n_channels": len(channel_paths),
                "decision": decision,
                "envelope_correlation": round(float(correlation), 4),
                "dominant_fraction": round(float(dominant_fraction), 4),
                "channel_speaker_map": {},
                "segments": [],
            }
            if decision == "informative":
                raw_segments = segments_from_labels(labels)
                from .voice_recognition import load_speaker_embeddings
                speaker_centroids = load_speaker_embeddings(paths, interview_id)
                centroids: dict[int, list[float]] = {}
                if speaker_centroids:
                    if progress_callback is not None:
                        progress_callback({
                            "event": "channels_progress",
                            "progress": int(100 * (position + 0.5) / max(1, len(targets))),
                            "message": f"Casando os canais de {interview_id} com as vozes do pyannote...",
                        })
                    try:
                        centroids = _channel_centroids(
                            paths.project_root / row["wav_path"], raw_segments)
                    except Exception as exc:  # noqa: BLE001 - fusao e opcional
                        print(f"Aviso: fusao de rotulos indisponivel para {interview_id}: {exc}")
                mapping = channel_speaker_map(len(channel_paths), centroids, speaker_centroids)
                payload["channel_speaker_map"] = {str(k): v for k, v in mapping.items()}
                payload["segments"] = [
                    {"start": s["start"], "end": s["end"], "speaker": mapping[int(s["channel"])]}
                    for s in raw_segments
                ]
            target = channels_json_path(paths, interview_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, payload)
            _log(paths, interview_id, "ok",
                 f"decision={decision} corr={payload['envelope_correlation']} "
                 f"dom={payload['dominant_fraction']} segments={len(payload['segments'])}")
        except Exception as exc:  # noqa: BLE001 - um arquivo nao derruba o lote
            failures += 1
            _log(paths, interview_id, "error", str(exc)[:500])
            print(f"Falha na analise de canais de {interview_id}: {exc}")
    if progress_callback is not None:
        progress_callback({"event": "channels_progress", "progress": 100,
                           "message": "Analise de canais concluida."})
    return failures


def _log(paths: Paths, interview_id: str, status: str, message: str) -> None:
    append_jsonl(
        paths.manifest_dir / "jobs.jsonl",
        {
            "interview_id": interview_id,
            "stage": "channels",
            "status": status,
            "started_at": now_utc(),
            "message": message,
        },
    )
