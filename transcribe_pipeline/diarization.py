from __future__ import annotations

from pathlib import Path
from typing import Any
import os

from .config import Paths
from .manifest import selected_rows
from . import model_manager, runtime
from .utils import append_jsonl, now_utc, write_json


def run_pyannote_diarization(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    failures = 0
    token_env = str(config["model_download_token_env"])
    try:
        model_name = model_manager.validate_local_diarization_model(config.get("diarize_model"))
    except ValueError as exc:
        print(str(exc))
        return len(selected_rows(rows, ids)) or 1
    rows_to_run = selected_rows(rows, ids)

    if dry_run:
        for row in rows_to_run:
            audio_path = diarization_audio_path(paths, row)
            print(f"pyannote {audio_path} --model {model_name} --offline {speaker_config_summary(config)}")
        return 0

    runtime.apply_secure_hf_environment(offline=True, token_env=token_env)
    if config.get("pyannote_metrics_enabled") is not None:
        os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        print(f"Missing pyannote dependencies: {exc}")
        return len(rows_to_run) or 1

    effective_device, fell_back = runtime.resolve_device(config.get("asr_device"))
    if fell_back:
        print("[Transcritorio] CUDA indisponivel. Usando CPU para diarizacao.")
    device = torch.device(effective_device)
    try:
        checkpoint = model_name if Path(model_name).exists() else model_manager.local_pyannote_checkpoint()
        pipeline = Pipeline.from_pretrained(checkpoint, token=None, cache_dir=str(runtime.model_cache_dir())).to(device)
    except Exception as exc:  # noqa: BLE001 - provide an actionable standalone error.
        print(f"Could not load local pyannote model: {exc}")
        return len(rows_to_run) or 1

    for row in rows_to_run:
        interview_id = row["interview_id"]
        audio_path = diarization_audio_path(paths, row)
        if not audio_path.exists():
            failures += 1
            log_job(paths, interview_id, "error", model_name, audio_path, "audio file missing")
            continue

        try:
            output = pipeline(str(audio_path), **speaker_kwargs(config))
            regular = getattr(output, "speaker_diarization", output)
            exclusive = getattr(output, "exclusive_speaker_diarization", None)
            write_annotation_outputs(paths, interview_id, "regular", regular, model_name, audio_path)
            if exclusive is not None:
                write_annotation_outputs(paths, interview_id, "exclusive", exclusive, model_name, audio_path)
            status = "ok" if exclusive is not None else "ok_no_exclusive"
            log_job(paths, interview_id, status, model_name, audio_path, "")
        except Exception as exc:  # noqa: BLE001 - preserve batch progress and log the failed file.
            failures += 1
            log_job(paths, interview_id, "error", model_name, audio_path, str(exc)[-2000:])
    return failures


def diarization_audio_path(paths: Paths, row: dict[str, str]) -> Path:
    wav_path = row.get("wav_path", "")
    return paths.project_root / wav_path if wav_path else paths.project_root / row["source_path"]


def speaker_kwargs(config: dict) -> dict[str, int]:
    num_speakers = config.get("diarization_num_speakers")
    if num_speakers is not None:
        return {"num_speakers": int(num_speakers)}
    result: dict[str, int] = {}
    if config.get("min_speakers") is not None:
        result["min_speakers"] = int(config["min_speakers"])
    if config.get("max_speakers") is not None:
        result["max_speakers"] = int(config["max_speakers"])
    return result


def speaker_config_summary(config: dict) -> str:
    kwargs = speaker_kwargs(config)
    return " ".join(f"--{key} {value}" for key, value in kwargs.items())


def write_annotation_outputs(paths: Paths, interview_id: str, kind: str, annotation, model_name: str, audio_path: Path) -> None:
    payload = {
        "interview_id": interview_id,
        "kind": kind,
        "diarization_model": model_name,
        "audio_path": str(audio_path),
        "created_at": now_utc(),
        "segments": annotation_to_segments(annotation),
    }
    write_json(paths.diarization_dir / "json" / f"{interview_id}.{kind}.json", payload)
    with (paths.diarization_dir / "rttm" / f"{interview_id}.{kind}.rttm").open("w", encoding="utf-8") as handle:
        annotation.write_rttm(handle)


def annotation_to_segments(annotation) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for segment, _track, speaker in annotation.itertracks(yield_label=True):
        segments.append({"start": float(segment.start), "end": float(segment.end), "speaker": str(speaker)})
    return segments


def log_job(paths: Paths, interview_id: str, status: str, model_name: str, audio_path: Path, message: str) -> None:
    append_jsonl(
        paths.manifest_dir / "jobs.jsonl",
        {
            "interview_id": interview_id,
            "stage": "diarize",
            "status": status,
            "started_at": now_utc(),
            "model": model_name,
            "audio_path": str(audio_path),
            "message": message,
        },
    )
