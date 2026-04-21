"""Runner for mlx-whisper (Apple Silicon Metal acceleration).

Used as an alternative to the whisperx CLI path when running on macOS with
Apple Silicon (MPS). Produces output files compatible with the downstream
pipeline (render.py reads the same {interview_id}.json shape).

Not available on Windows/Linux (the `mlx` framework only builds on macOS ARM).
is_available() returns False there, and run_whisperx() falls back to its
normal CPU path.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
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

    failures = 0
    output_json_dir = paths.asr_dir / "json"
    output_srt_dir = paths.asr_dir / "srt"
    output_vtt_dir = paths.asr_dir / "vtt"
    output_txt_dir = paths.asr_dir / "txt"
    output_json_dir.mkdir(parents=True, exist_ok=True)
    output_srt_dir.mkdir(parents=True, exist_ok=True)
    output_vtt_dir.mkdir(parents=True, exist_ok=True)
    output_txt_dir.mkdir(parents=True, exist_ok=True)

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
        write_json(output_json_dir / f"{interview_id}.json", json_payload)

        _write_srt(output_srt_dir / f"{interview_id}.srt", json_payload["segments"])
        _write_vtt(output_vtt_dir / f"{interview_id}.vtt", json_payload["segments"])
        _write_txt(output_txt_dir / f"{interview_id}.txt", json_payload["segments"])

        _emit(progress_callback, interview_id,
              {"event": "asr_done", "progress": 100,
               "message": f"MLX Whisper concluido em {elapsed:.1f}s"})
        _log_job(paths, interview_id, mlx_repo, config, "ok", elapsed_s=elapsed)

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
    elapsed_s: float | None = None,
    error: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "interview_id": interview_id,
        "stage": "transcribe",
        "status": status,
        "started_at": now_utc(),
        "model": config.get("asr_model", ""),
        "resolved_model": model,
        "backend": "mlx-whisper",
        "language": config.get("asr_language", ""),
        "variant": config.get("asr_variant") or "",
    }
    if elapsed_s is not None:
        entry["elapsed_s"] = round(elapsed_s, 2)
    if error is not None:
        entry["error"] = error
    append_jsonl(paths.manifest_dir / "jobs.jsonl", entry)
