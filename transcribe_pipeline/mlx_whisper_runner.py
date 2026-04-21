"""Runner for mlx-whisper (Apple Silicon Metal acceleration).

Used as an alternative to the whisperx CLI path when running on macOS with
Apple Silicon (MPS). Produces output files compatible with the downstream
pipeline (render.py reads the same {interview_id}.json shape).

Not available on Windows/Linux (the `mlx` framework only builds on macOS ARM).
is_available() returns False there, and run_whisperx() falls back to its
normal CPU path.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from . import runtime
from .model_manager import validate_local_diarization_model
from .utils import append_jsonl, format_timestamp, now_utc, write_json


ProgressCallback = Callable[[dict[str, Any]], None]


MLX_MODEL_MAP: dict[str, str] = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def is_available() -> bool:
    """True if mlx_whisper imports on the current interpreter."""
    try:
        import mlx_whisper  # noqa: F401
    except Exception:
        return False
    return True


def resolve_mlx_model(asr_model: str) -> str:
    """Map a standard whisper model name to the MLX HF repo.

    Pass-through if the name already looks like an HF repo (contains "/")
    or an existing local path.
    """
    key = (asr_model or "").strip()
    if not key:
        return MLX_MODEL_MAP["large-v3"]
    if key in MLX_MODEL_MAP:
        return MLX_MODEL_MAP[key]
    if "/" in key:
        return key
    if Path(key).exists():
        return key
    return f"mlx-community/whisper-{key}-mlx"


def run_mlx_whisper(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Transcribe rows using mlx-whisper. Returns failure count.

    Output shape mirrors what the whisperx CLI writes to 02_asr_raw/json/:
    a per-interview JSON with top-level `segments` and (if word_timestamps
    is enabled) per-segment `words`. The rest of the pipeline (render.py,
    diarization.py) is unchanged.
    """
    if not is_available():
        raise RuntimeError(
            "mlx-whisper is not installed. Install with 'pip install mlx-whisper' "
            "on macOS Apple Silicon, or use the WhisperX CPU path."
        )

    import mlx_whisper  # type: ignore[import-not-found]

    # Apply same HF environment hygiene that whisperx_runner does: offline flag,
    # token redaction, HF_TOKEN cleanup. Without this, the HF token can leak
    # across the pyannote/HF cache paths and the model_cache_dir override is
    # ignored.
    token_env = str(config.get("model_download_token_env") or "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN")
    cache_only = bool(config.get("asr_model_cache_only", True))
    runtime.apply_secure_hf_environment(offline=cache_only, token_env=token_env)

    # Fail fast when diarization is enabled but the model is missing — matches
    # whisperx_runner behavior (otherwise we'd spend 5+ min transcribing before
    # the diarize step discovers the missing model).
    if config.get("diarize", True):
        validate_local_diarization_model(config.get("diarize_model"))

    # pyannote metrics env var — set only when explicitly configured so MLX
    # and whisperx paths behave identically for downstream logs.
    if config.get("pyannote_metrics_enabled") is not None:
        os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

    # Output dir respects asr_variant: baseline -> paths.asr_dir; variant ->
    # paths.asr_variants_dir/<name>. Imported lazily to avoid a circular import
    # between whisperx_runner and mlx_whisper_runner.
    from .whisperx_runner import asr_output_dir
    output_dir = asr_output_dir(paths, config)
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    mlx_repo = resolve_mlx_model(str(config.get("asr_model", "")))
    language = config.get("asr_language") or None
    word_timestamps = bool(config.get("asr_word_timestamps", True))

    for row in selected_rows(rows, ids):
        if should_cancel is not None and should_cancel():
            failures += 1
            break

        interview_id = row["interview_id"]
        wav = paths.project_root / row["wav_path"]
        if not wav.exists():
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"WAV ausente: {wav.name}"})
            _log_job(paths, interview_id, mlx_repo, config, "error",
                     error=f"wav not found: {wav}")
            failures += 1
            continue

        if dry_run:
            print(f"[mlx-whisper] would transcribe {wav} with {mlx_repo}")
            continue

        _emit(progress_callback, interview_id,
              {"event": "asr_progress", "progress": 1,
               "message": f"Carregando modelo MLX {mlx_repo}..."})

        started = time.monotonic()
        try:
            result = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=mlx_repo,
                language=language,
                word_timestamps=word_timestamps,
                verbose=False,
            )
        except Exception as exc:
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"mlx-whisper falhou: {type(exc).__name__}"})
            _log_job(paths, interview_id, mlx_repo, config, "error", error=str(exc))
            failures += 1
            continue

        elapsed = time.monotonic() - started

        json_payload = _normalize_mlx_result(result)
        # Output layout matches whisperx CLI (--output_format all): JSON, SRT,
        # VTT, TXT and TSV all written directly into output_dir, so
        # render.find_whisperx_json(paths, id) finds the baseline file and
        # asr_variant routing works identically.
        write_json(output_dir / f"{interview_id}.json", json_payload)
        _write_srt(output_dir / f"{interview_id}.srt", json_payload["segments"])
        _write_vtt(output_dir / f"{interview_id}.vtt", json_payload["segments"])
        _write_txt(output_dir / f"{interview_id}.txt", json_payload["segments"])
        _write_tsv(output_dir / f"{interview_id}.tsv", json_payload["segments"])

        _emit(progress_callback, interview_id,
              {"event": "asr_done", "progress": 100,
               "message": f"MLX Whisper concluido em {elapsed:.1f}s"})
        _log_job(paths, interview_id, mlx_repo, config, "ok",
                 output_dir=str(output_dir), elapsed_s=elapsed)

    return failures


def _normalize_mlx_result(result: dict[str, Any]) -> dict[str, Any]:
    """Coerce mlx-whisper output into the shape render.py expects.

    Keys preserved: language, text. Segments get ensured start/end/text and
    optional words list with word/start/end fields. Extra mlx fields
    (avg_logprob, no_speech_prob, etc.) are passed through.
    """
    out_segments: list[dict[str, Any]] = []
    for raw in result.get("segments", []) or []:
        if not isinstance(raw, dict):
            continue
        seg: dict[str, Any] = dict(raw)
        seg["start"] = float(raw.get("start", 0) or 0)
        seg["end"] = float(raw.get("end", seg["start"]) or seg["start"])
        seg["text"] = str(raw.get("text", "") or "")
        raw_words = raw.get("words")
        if isinstance(raw_words, list):
            norm_words = []
            for w in raw_words:
                if not isinstance(w, dict):
                    continue
                word_text = str(w.get("word") or w.get("text") or "").strip()
                if not word_text:
                    continue
                try:
                    ws = float(w.get("start"))
                    we = float(w.get("end"))
                except (TypeError, ValueError):
                    continue
                nw = dict(w)
                nw["word"] = word_text
                nw["start"] = ws
                nw["end"] = we
                norm_words.append(nw)
            seg["words"] = norm_words
        out_segments.append(seg)

    return {
        "language": str(result.get("language", "") or ""),
        "segments": out_segments,
        "text": str(result.get("text", "") or ""),
    }


def _write_srt(path: Path, segments: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg['start'])} --> {_srt_ts(seg['end'])}")
        lines.append(str(seg.get("text", "")).strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_vtt(path: Path, segments: list[dict[str, Any]]) -> None:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_ts(seg['start'])} --> {_vtt_ts(seg['end'])}")
        lines.append(str(seg.get("text", "")).strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_txt(path: Path, segments: list[dict[str, Any]]) -> None:
    text = " ".join(str(s.get("text", "")).strip() for s in segments if s.get("text"))
    path.write_text(text + "\n", encoding="utf-8")


def _write_tsv(path: Path, segments: list[dict[str, Any]]) -> None:
    """Tab-separated format produced by whisperx CLI: start\\tend\\ttext (ms)."""
    lines = ["start\tend\ttext"]
    for seg in segments:
        try:
            start_ms = int(round(float(seg.get("start", 0) or 0) * 1000))
            end_ms = int(round(float(seg.get("end", 0) or 0) * 1000))
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text", "")).strip().replace("\t", " ").replace("\n", " ")
        lines.append(f"{start_ms}\t{end_ms}\t{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _srt_ts(seconds: float) -> str:
    # SRT requires HH:MM:SS,mmm (comma decimal).
    stamp = format_timestamp(float(seconds), millis=True)
    return stamp.replace(".", ",")


def _vtt_ts(seconds: float) -> str:
    # WebVTT requires HH:MM:SS.mmm (dot decimal).
    return format_timestamp(float(seconds), millis=True)


def _emit(callback: ProgressCallback | None, interview_id: str,
          payload: dict[str, Any]) -> None:
    if callback is None:
        return
    data = dict(payload)
    data.setdefault("file_id", interview_id)
    callback(data)


def _log_job(
    paths: Paths,
    interview_id: str,
    model: str,
    config: dict,
    status: str,
    *,
    output_dir: str | None = None,
    elapsed_s: float | None = None,
    error: str | None = None,
) -> None:
    # Schema mirrors whisperx_runner.jobs.jsonl entries (same consumers)
    # plus backend="mlx-whisper" to differentiate the producer.
    entry: dict[str, Any] = {
        "interview_id": interview_id,
        "stage": "transcribe",
        "status": status,
        "started_at": now_utc(),
        "model": config.get("asr_model", ""),
        "resolved_model": model,
        "backend": "mlx-whisper",
        "language": config.get("asr_language", ""),
        "compute_type": config.get("asr_compute_type", ""),
        "batch_size": config.get("asr_batch_size", ""),
        "variant": config.get("asr_variant") or "",
    }
    if output_dir is not None:
        entry["output_dir"] = output_dir
    if elapsed_s is not None:
        entry["elapsed_s"] = round(elapsed_s, 2)
    if error is not None:
        entry["error"] = error
    append_jsonl(paths.manifest_dir / "jobs.jsonl", entry)
