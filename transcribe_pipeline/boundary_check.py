"""Verificacao acustica pos-render: fronteiras suspeitas e sobreposicao.

Duas checagens sobre a transcricao canonica, depois do render:

1. Fronteiras: para cada par de turnos adjacentes com falantes distintos,
   embedda uma janela curta de audio de cada lado da fronteira (modelo de
   embedding do proprio pipeline de diarizacao, ja no cache local) e compara
   por cosseno. Similaridade alta = mesma voz dos dois lados = a divisao
   provavelmente esta errada -> flag "duvida" no turno da esquerda.
   Validado em benchmark (2026-08-25, 64 fronteiras com gabarito):
   AUC 0,961, recall 91%, falso alarme 9% com janela de 2s.

2. Sobreposicao: onde a diarizacao regular detectou >=2 falantes
   simultaneos (e a exclusiva escolheu um), os turnos afetados ganham o
   flag "sobreposicao".

As funcoes puras deste modulo nao dependem de torch/pyannote e sao
testaveis isoladamente (tests/toy_boundary_check.py). O modelo so e
carregado dentro de run_boundary_check.

IMPORTANTE: este modulo escreve apenas no canonical e nos derivados
(md/tsv/docx); nunca toca review.json nem grava em review["edits"] --
uma lista de edits nao-vazia desligaria refresh_unedited_reviews.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable

import numpy as np

from .config import Paths
from .manifest import selected_rows
from .utils import append_jsonl, now_utc, read_json, write_json
from .voice_recognition import cosine_similarity

ProgressCallback = Callable[[dict[str, Any]], None]

# Marcadores fixos usados para idempotencia (re-rodar nao duplica notas).
BOUNDARY_NOTE_MARKER = "parecem iguais"
OVERLAP_NOTE = "Ha vozes sobrepostas neste trecho."

# Janela mais curta que isso nao rende embedding confiavel.
MIN_WINDOW_SECONDS = 0.7
# Sobreposicao mais curta que isso nao vira flag (ruido de collar).
MIN_OVERLAP_SECONDS = 0.3
# Fracao minima do turno coberta por sobreposicao para virar flag.
# Auditoria 2026-08-25: por mera interseccao, turnos longos com backchannel
# curto ("uhum") inundavam de flags (117 turnos num grupo focal); por fracao,
# so a interjeicao realmente engolida pela fala do outro e marcada.
OVERLAP_FRACTION = 0.5


def candidate_boundaries(turns: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Pares de turnos adjacentes com falantes distintos: (indice_esq, a, b)."""
    pairs = []
    for i in range(len(turns) - 1):
        a, b = turns[i], turns[i + 1]
        if str(a.get("speaker")) != str(b.get("speaker")):
            pairs.append((i, a, b))
    return pairs


def boundary_windows(
    a: dict[str, Any],
    b: dict[str, Any],
    window: float,
    min_window: float = MIN_WINDOW_SECONDS,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Janelas [antes, depois] da fronteira, presas ao interior de cada turno.

    Retorna None quando algum lado fica curto demais para embedding.
    """
    a_start, a_end = float(a["start"]), float(a["end"])
    b_start, b_end = float(b["start"]), float(b["end"])
    left = (max(a_start, a_end - window), a_end)
    right = (b_start, min(b_end, b_start + window))
    if left[1] - left[0] < min_window or right[1] - right[0] < min_window:
        return None
    return left, right


def overlap_intervals(
    segments: list[dict[str, Any]],
    min_duration: float = MIN_OVERLAP_SECONDS,
) -> list[tuple[float, float]]:
    """Intervalos onde >=2 falantes estao ativos ao mesmo tempo (varredura)."""
    events: list[tuple[float, int]] = []
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        if end > start:
            events.append((start, 1))
            events.append((end, -1))
    events.sort()
    intervals: list[tuple[float, float]] = []
    active = 0
    overlap_start: float | None = None
    for t, delta in events:
        active += delta
        if active >= 2 and overlap_start is None:
            overlap_start = t
        elif active < 2 and overlap_start is not None:
            if t - overlap_start >= min_duration:
                intervals.append((overlap_start, t))
            overlap_start = None
    return intervals


def turns_overlapping_intervals(
    turns: list[dict[str, Any]],
    intervals: list[tuple[float, float]],
    min_fraction: float = OVERLAP_FRACTION,
) -> list[int]:
    """Indices dos turnos cobertos por sobreposicao em >= min_fraction da duracao."""
    hits = []
    for i, turn in enumerate(turns):
        t_start, t_end = float(turn["start"]), float(turn["end"])
        duration = t_end - t_start
        if duration <= 0:
            continue
        covered = sum(
            max(0.0, min(t_end, o_end) - max(t_start, o_start))
            for o_start, o_end in intervals
        )
        if covered / duration >= min_fraction:
            hits.append(i)
    return hits


def flag_turn(turn: dict[str, Any], flag: str, note: str, marker: str) -> bool:
    """Adiciona flag+nota ao turno; idempotente via marcador na nota."""
    notes = str(turn.get("notes") or "")
    if marker in notes:
        return False
    flags = [str(f) for f in (turn.get("flags") or [])]
    if flag not in flags:
        flags.append(flag)
    turn["flags"] = flags
    turn["notes"] = f"{notes} | {note}".strip(" |") if notes else note
    return True


def boundary_note(similarity: float) -> str:
    return (
        f"A voz deste bloco e a do seguinte {BOUNDARY_NOTE_MARKER} "
        f"(semelhanca {similarity:.0%}) - a divisao entre eles pode estar errada."
    )


def load_embedder():
    """Carrega o modelo de embedding do pipeline de diarizacao local."""
    from . import model_manager, runtime

    runtime.apply_secure_hf_environment(offline=True)
    import torch
    from pyannote.audio import Pipeline

    checkpoint = model_manager.local_pyannote_checkpoint()
    pipeline = Pipeline.from_pretrained(
        checkpoint, token=None, cache_dir=str(runtime.model_cache_dir())
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pipeline.to(device)
    return pipeline._embedding


def embed_window(embedder, waveform, sample_rate: int, t0: float, t1: float) -> list[float] | None:
    """Embedding da janela [t0, t1] do waveform (1, T); None se invalido."""
    i0 = max(0, int(t0 * sample_rate))
    i1 = min(int(waveform.shape[-1]), int(t1 * sample_rate))
    if i1 - i0 < int(0.5 * sample_rate):
        return None
    chunk = waveform[..., i0:i1].reshape(1, 1, -1)
    vector = np.asarray(embedder(chunk), dtype=np.float64).reshape(-1)
    values = [float(v) for v in vector]
    if not values or not all(math.isfinite(v) for v in values):
        return None
    return values


def _diarization_segments(paths: Paths, interview_id: str, kind: str) -> list[dict[str, Any]] | None:
    path = paths.diarization_dir / "json" / f"{interview_id}.{kind}.json"
    if not path.exists():
        return None
    payload = read_json(path)
    segments = payload.get("segments")
    return segments if isinstance(segments, list) else None


def _excerpt(text: str, words: int, tail: bool) -> str:
    parts = str(text).split()
    chosen = parts[-words:] if tail else parts[:words]
    return " ".join(chosen)


def run_boundary_check(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    report: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Roda as duas checagens; retorna numero de falhas.

    dry_run: so imprime o que seria feito, sem carregar modelo.
    report: roda de verdade e imprime os achados, mas NAO grava nada
    (modo de auditoria/calibracao do limiar).
    """
    if not config.get("boundary_check", True):
        print("boundary_check desativado na config; nada a fazer.")
        return 0

    threshold = float(config.get("boundary_check_threshold") or 0.40)
    window = float(config.get("boundary_check_window") or 2.0)
    rows_to_run = selected_rows(rows, ids)

    if dry_run:
        for row in rows_to_run:
            print(
                f"boundary-check {row['interview_id']} "
                f"--threshold {threshold} --window {window}"
            )
        return 0

    def emit(event: str, progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "progress": progress, "message": message})

    from .diarization import _load_wav_as_tensor, diarization_audio_path
    from .render import write_docx_if_available, write_markdown, write_nvivo_tsv

    failures = 0
    embedder = None
    total = len(rows_to_run)
    for index, row in enumerate(rows_to_run):
        if should_cancel is not None and should_cancel():
            print("Cancelado.")
            break
        interview_id = row["interview_id"]
        base_progress = int(100 * index / max(1, total))
        emit("boundary_progress", base_progress, f"Verificando fronteiras: {interview_id}")
        started = time.time()
        try:
            canonical_path = paths.canonical_dir / "json" / f"{interview_id}.canonical.json"
            if not canonical_path.exists():
                print(f"{interview_id}: ainda sem transcricao montada; nada a verificar.")
                continue
            exclusive = _diarization_segments(paths, interview_id, "exclusive")
            if exclusive is None:
                print(f"{interview_id}: sem diarizacao exclusive; nada a verificar.")
                continue
            canonical = read_json(canonical_path)
            turns = canonical.get("turns") or []

            changed = 0
            suspects = 0
            skipped_short = 0

            pairs = candidate_boundaries(turns)
            if pairs:
                if embedder is None:
                    emit("boundary_progress", base_progress, "Carregando modelo de vozes...")
                    embedder = load_embedder()
                audio = _load_wav_as_tensor(diarization_audio_path(paths, row))
                waveform, sample_rate = audio["waveform"], audio["sample_rate"]
                for i, a, b in pairs:
                    windows = boundary_windows(a, b, window)
                    if windows is None:
                        skipped_short += 1
                        continue
                    (l0, l1), (r0, r1) = windows
                    left = embed_window(embedder, waveform, sample_rate, l0, l1)
                    right = embed_window(embedder, waveform, sample_rate, r0, r1)
                    if left is None or right is None:
                        skipped_short += 1
                        continue
                    similarity = cosine_similarity(left, right)
                    if similarity < threshold:
                        continue
                    suspects += 1
                    if report:
                        gap = float(b["start"]) - float(a["end"])
                        print(
                            f"[fronteira] {interview_id} t={float(a['end']):.1f}s "
                            f"sim={similarity:.2f} gap={gap:.2f}s "
                            f"{a.get('speaker')}->{b.get('speaker')} | "
                            f"...{_excerpt(a.get('text', ''), 8, tail=True)} || "
                            f"{_excerpt(b.get('text', ''), 8, tail=False)}..."
                        )
                    else:
                        changed += int(
                            flag_turn(a, "duvida", boundary_note(similarity), BOUNDARY_NOTE_MARKER)
                        )

            regular = _diarization_segments(paths, interview_id, "regular")
            overlap_hits: list[int] = []
            if regular:
                overlap_hits = turns_overlapping_intervals(turns, overlap_intervals(regular))
                for i in overlap_hits:
                    if report:
                        turn = turns[i]
                        print(
                            f"[sobreposicao] {interview_id} "
                            f"{float(turn['start']):.1f}-{float(turn['end']):.1f}s "
                            f"{turn.get('speaker')}"
                        )
                    else:
                        changed += int(
                            flag_turn(turns[i], "sobreposicao", OVERLAP_NOTE, OVERLAP_NOTE)
                        )

            if changed and not report:
                write_json(canonical_path, canonical)
                write_markdown(paths.review_dir / "md" / f"{interview_id}.md", canonical)
                write_nvivo_tsv(paths.asr_dir / "tsv" / f"{interview_id}_nvivo.tsv", canonical)
                write_docx_if_available(paths.review_dir / "docx" / f"{interview_id}.docx", canonical)

            summary = (
                f"{interview_id}: {len(pairs)} fronteiras analisadas, "
                f"{suspects} suspeitas, {len(overlap_hits)} turnos com sobreposicao, "
                f"{skipped_short} curtas demais, {changed} turnos atualizados."
            )
            print(summary)
            append_jsonl(
                paths.manifest_dir / "jobs.jsonl",
                {
                    "at": now_utc(),
                    "stage": "boundary_check",
                    "interview_id": interview_id,
                    "boundaries": len(pairs),
                    "suspects": suspects,
                    "overlap_turns": len(overlap_hits),
                    "updated_turns": changed,
                    "seconds": round(time.time() - started, 1),
                },
            )
        except Exception as exc:  # noqa: BLE001 - passo opcional: falha vira log, nao crash.
            print(f"Falha ao verificar {interview_id}: {exc}")
            failures += 1
    emit("boundary_progress", 100, "Verificacao de fronteiras concluida.")
    return failures
