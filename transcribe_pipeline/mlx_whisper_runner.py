"""Runner for mlx-whisper (Apple Silicon Metal acceleration).

Used as an alternative to the whisperx CLI path when running on macOS with
Apple Silicon (MPS). Produces output files compatible with the downstream
pipeline (render.py reads the same {interview_id}.json shape).

Not available on Windows/Linux (the `mlx` framework only builds on macOS ARM).
is_available() returns False there, and run_whisperx() falls back to its
normal CPU path.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from . import runtime
from .model_manager import validate_local_diarization_model
from .utils import append_jsonl, format_timestamp, now_utc, sanitize_message, write_json


# Same safe pattern asr_output_dir() uses for variant names. interview_id
# comes from manifest.csv which is user-editable; enforce safety before
# using it as a path component.
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


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

# Revisions pinadas (SHA) — colhidas via HF API em 2026-04-22.
# Mesma motivacao de model_manager.ASR_VARIANTS: reprodutibilidade +
# defesa contra redirect chains e atualizacoes nao-auditadas.
MLX_MODEL_REVISIONS: dict[str, str] = {
    "mlx-community/whisper-tiny-mlx": "6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
    "mlx-community/whisper-base-mlx": "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
    "mlx-community/whisper-small-mlx": "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
    "mlx-community/whisper-medium-mlx": "7fc08c4eac4c316526498f147dfdee6f6303f975",
    "mlx-community/whisper-large-v3-mlx": "49e6aa286ad60c14352c404340ded53710378a11",
    "mlx-community/whisper-large-v2-mlx": "cce86229e2765266197fef869ce9f7e2550067ab",
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
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


def ensure_mlx_model_local(mlx_repo: str) -> str:
    """Resolve the pinned revision of *mlx_repo* to a local path, best-effort.

    Returns the local snapshot dir when pre-download succeeds (so
    mlx_whisper.transcribe picks up the audited SHA). On any failure
    (no network, mocked env in tests, revision map missing), returns
    *mlx_repo* unchanged so mlx_whisper handles the download itself —
    losing the pin but not blocking the transcription. This is
    progressive enhancement: the pin is a defense, not a requirement.
    """
    if "/" not in mlx_repo or Path(mlx_repo).exists():
        return mlx_repo
    revision = MLX_MODEL_REVISIONS.get(mlx_repo)
    if not revision:
        return mlx_repo
    try:
        from huggingface_hub import snapshot_download
        local_path = snapshot_download(
            repo_id=mlx_repo,
            revision=revision,
            repo_type="model",
            cache_dir=str(runtime.model_cache_dir()),
        )
        return str(local_path)
    except Exception:
        return mlx_repo


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
            "mlx-whisper nao esta instalado. Instale com 'pip install mlx-whisper' "
            "em macOS Apple Silicon, ou use o caminho CPU via WhisperX."
        )

    import mlx_whisper  # type: ignore[import-not-found]

    # Apply same HF environment hygiene that whisperx_runner does: offline flag,
    # token redaction, HF_TOKEN cleanup. Without this, the HF token can leak
    # across the pyannote/HF cache paths and the model_cache_dir override is
    # ignored.
    token_env = str(config.get("model_download_token_env") or "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN")
    cache_only = bool(config.get("asr_model_cache_only", True))
    runtime.apply_secure_hf_environment(offline=cache_only, token_env=token_env)

    # Respect config.model_cache_dir override: apply_secure_hf_environment points
    # HF_HOME at runtime.model_cache_dir() (app default); if the project config
    # overrides that, re-point HF_HOME/HF_HUB_CACHE here so mlx-whisper, which
    # uses HuggingFace's env-var-driven cache location, picks up the override.
    custom_cache = config.get("model_cache_dir")
    if custom_cache:
        cache_path = Path(str(custom_cache))
        os.environ["HF_HUB_CACHE"] = str(cache_path)
        os.environ["HF_HOME"] = str(cache_path.parent if cache_path.parent != cache_path else cache_path)

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
    # Pre-resolve pinned revision to local path so mlx_whisper picks up
    # the audited SHA, not main. Best-effort: falls back to repo_id if
    # pre-download fails (no network, mocked test env, etc.).
    mlx_repo = ensure_mlx_model_local(mlx_repo)
    language = config.get("asr_language") or None
    word_timestamps = bool(config.get("asr_word_timestamps", True))

    for row in selected_rows(rows, ids):
        if should_cancel is not None and should_cancel():
            failures += 1
            break

        raw_id = str(row.get("interview_id", "") or "")
        # Defuse path traversal / shell chars / empty: accept only safe chars,
        # matching the pattern asr_output_dir() uses for variants.
        safe_id = _SAFE_ID_RE.sub("_", raw_id).strip("._")
        if not safe_id:
            _emit(progress_callback, raw_id or "<sem_id>",
                  {"event": "asr_error", "progress": 0,
                   "message": "Linha sem interview_id valido; pulando."})
            _log_job(paths, raw_id or "<invalid>", mlx_repo, config, "error",
                     error="interview_id vazio ou invalido apos sanitizacao")
            failures += 1
            continue
        interview_id = safe_id
        wav = paths.project_root / row["wav_path"]
        if not wav.exists():
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"WAV ausente: {wav.name}"})
            _log_job(paths, interview_id, mlx_repo, config, "error",
                     error=f"WAV nao encontrado: {wav}")
            failures += 1
            continue

        if dry_run:
            print(f"[mlx-whisper] would transcribe {wav} with {mlx_repo}")
            continue

        _emit(progress_callback, interview_id,
              {"event": "asr_progress", "progress": 1,
               "message": f"Carregando modelo MLX {mlx_repo}..."})

        # mlx_whisper.transcribe() is synchronous and emits nothing while it
        # runs. Without a creep we would freeze at 1% for minutes, then jump
        # to 100% — the user would assume the app hung. Spawn a daemon thread
        # that advances progress 1% every 3s up to 89 while the call runs.
        # Cap at 89 so the real 100 only fires after successful completion.
        creep_stop = threading.Event()
        creep_state = {"percent": 1}

        def _creep() -> None:
            while not creep_stop.wait(3.0):
                if creep_state["percent"] < 89:
                    creep_state["percent"] += 1
                    _emit(progress_callback, interview_id, {
                        "event": "asr_progress",
                        "progress": creep_state["percent"],
                        "message": f"Transcrevendo com MLX ({creep_state['percent']}%)...",
                    })

        creep_thread = threading.Thread(target=_creep, daemon=True, name=f"mlx-creep-{interview_id}")
        creep_thread.start()

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
            # Include the exception message, not just the type name, so the
            # user can act on it (model not found / OOM / corrupted file / ...).
            # Truncated to keep the UI dialog readable. Sanitize to redact
            # HF tokens that upstream error strings can include.
            detail = sanitize_message(str(exc).strip() or type(exc).__name__)
            if len(detail) > 240:
                detail = detail[:240].rstrip() + "..."
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"mlx-whisper falhou ({type(exc).__name__}): {detail}"})
            _log_job(paths, interview_id, mlx_repo, config, "error",
                     error=sanitize_message(str(exc)))
            failures += 1
            continue
        finally:
            # Stop the creep thread whether transcribe raised or returned.
            creep_stop.set()
            creep_thread.join(timeout=1.0)

        # transcribe() may return None or an unexpected type in edge cases
        # (corrupt model, internal MLX errors). Treat as a failure without
        # crashing the batch.
        if not isinstance(result, dict):
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"mlx-whisper retornou {type(result).__name__}, esperado dict."})
            _log_job(paths, interview_id, mlx_repo, config, "error",
                     error=f"transcribe returned non-dict ({type(result).__name__})")
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

    Defensive against adversarial/corrupt inputs from the transcribe call:
    - result=None / non-dict -> empty output (raised by caller earlier)
    - NaN/Inf timestamps -> segment dropped
    - start > end -> swapped (preserve what we can)
    - empty text -> segment dropped (renders nothing meaningful anyway)
    """
    if not isinstance(result, dict):
        # Caller should have raised, but be defensive here too.
        return {"language": "", "segments": [], "text": ""}

    out_segments: list[dict[str, Any]] = []
    for raw in result.get("segments", []) or []:
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw.get("start", 0) or 0)
            end = float(raw.get("end", start) if raw.get("end") is not None else start)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        if start > end:
            start, end = end, start
        text = str(raw.get("text", "") or "")
        if not text.strip():
            continue
        seg: dict[str, Any] = dict(raw)
        seg["start"] = start
        seg["end"] = end
        seg["text"] = text
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
                if not (math.isfinite(ws) and math.isfinite(we)):
                    continue
                if ws > we:
                    ws, we = we, ws
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


def _safe_subtitle_text(raw_text: Any) -> str:
    """Sanitize a segment text for SRT/VTT output:
    - replace '-->' (collides with timecode separator)
    - collapse \\r/\\n into spaces (multi-line text would fragment cue)
    - strip.
    """
    s = str(raw_text or "")
    # Hyphen-minus + hyphen-minus + greater-than is the SRT/VTT cue separator.
    # An attacker-controlled or accidental "-->" inside the text line would be
    # read by parsers as a new timecode line.
    s = s.replace("-->", "->")
    s = s.replace("\r", " ").replace("\n", " ")
    return s.strip()


def _write_srt(path: Path, segments: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg['start'])} --> {_srt_ts(seg['end'])}")
        lines.append(_safe_subtitle_text(seg.get("text", "")))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_vtt(path: Path, segments: list[dict[str, Any]]) -> None:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_ts(seg['start'])} --> {_vtt_ts(seg['end'])}")
        lines.append(_safe_subtitle_text(seg.get("text", "")))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_txt(path: Path, segments: list[dict[str, Any]]) -> None:
    text = " ".join(str(s.get("text", "")).strip() for s in segments if s.get("text"))
    path.write_text(text + "\n", encoding="utf-8")


def _write_tsv(path: Path, segments: list[dict[str, Any]]) -> None:
    """Tab-separated format produced by whisperx CLI: start\\tend\\ttext (ms).

    Text is flattened: tabs and newlines become spaces so a 3-column TSV
    stays 3 columns per row even if the ASR text happened to contain them.
    """
    lines = ["start\tend\ttext"]
    for seg in segments:
        try:
            start_ms = int(round(float(seg.get("start", 0) or 0) * 1000))
            end_ms = int(round(float(seg.get("end", 0) or 0) * 1000))
        except (TypeError, ValueError):
            continue
        text = (str(seg.get("text", "")).strip()
                .replace("\t", " ").replace("\r", " ").replace("\n", " "))
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
    """Best-effort emit: a rude callback (disconnected Qt signal, user bug)
    must not abort the batch or crash the creep thread."""
    if callback is None:
        return
    data = dict(payload)
    data.setdefault("file_id", interview_id)
    try:
        callback(data)
    except Exception:
        # Intentionally swallow: progress is cosmetic, transcription is not.
        pass


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
