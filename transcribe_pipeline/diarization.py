from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable
import numpy as np
import os
import threading
import time
import wave as wave_mod

from .config import Paths
from .manifest import selected_rows
from . import model_manager, runtime
from .utils import append_jsonl, now_utc, write_json

ProgressCallback = Callable[[dict[str, Any]], None]


def _load_wav_as_tensor(audio_path: Path):
    """Load a WAV file as a torch tensor dict, bypassing torchcodec.

    Returns {"waveform": (1, T) float32 tensor, "sample_rate": int}.
    This avoids the torchcodec/FFmpeg DLL dependency that causes
    NameError: 'AudioDecoder' on systems without FFmpeg DLLs registered.
    """
    import torch

    with wave_mod.open(str(audio_path), "r") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels)[:, 0]

    waveform = torch.from_numpy(samples).unsqueeze(0)  # (1, T)
    return {"waveform": waveform, "sample_rate": sample_rate}


def run_pyannote_diarization(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
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

    def emit(event: str, progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "progress": progress, "message": message})

    def _ts() -> str:
        return time.strftime("%H:%M:%S")

    emit("diarize_progress", 0, "Carregando modelo de identificacao de falantes...")
    print(f"[{_ts()}] [diarize] Inicio da diarizacao", flush=True)

    runtime.apply_secure_hf_environment(offline=True, token_env=token_env)
    if config.get("pyannote_metrics_enabled") is not None:
        os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

    print(f"[{_ts()}] [diarize] Importando torch/pyannote...", flush=True)
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
    print(f"[{_ts()}] [diarize] Device: {effective_device}. Carregando pipeline...", flush=True)
    try:
        checkpoint = model_name if Path(model_name).exists() else model_manager.local_pyannote_checkpoint()
        pipeline = Pipeline.from_pretrained(checkpoint, token=None, cache_dir=str(runtime.model_cache_dir())).to(device)
    except Exception as exc:  # noqa: BLE001 - provide an actionable standalone error.
        print(f"Could not load local pyannote model: {exc}")
        return len(rows_to_run) or 1

    # Apply custom hyperparameters if configured
    custom_params: dict = {}
    clustering_threshold = config.get("diarization_clustering_threshold")
    min_duration_off = config.get("diarization_min_duration_off")
    if clustering_threshold is not None:
        custom_params["clustering"] = {"threshold": float(clustering_threshold), "Fa": 0.07, "Fb": 0.8}
    if min_duration_off is not None:
        custom_params["segmentation"] = {"min_duration_off": float(min_duration_off)}
    if custom_params:
        pipeline.instantiate(custom_params)
        print(f"[{_ts()}] [diarize] Hiperparametros customizados: {custom_params}", flush=True)

    print(f"[{_ts()}] [diarize] Pipeline carregado.", flush=True)
    emit("diarize_progress", 20, "Modelo carregado.")
    total = len(rows_to_run)

    for idx, row in enumerate(rows_to_run):
        if should_cancel is not None and should_cancel():
            failures += total - idx
            break
        interview_id = row["interview_id"]
        audio_path = diarization_audio_path(paths, row)
        if not audio_path.exists():
            failures += 1
            log_job(paths, interview_id, "error", model_name, audio_path, "audio file missing")
            continue

        file_start_pct = 20 + int(70 * idx / max(1, total))
        file_end_pct = 20 + int(70 * (idx + 1) / max(1, total))
        emit("diarize_progress", file_start_pct, f"Processando {interview_id}...")

        try:
            print(f"[{_ts()}] [diarize] Carregando audio {interview_id}...", flush=True)
            audio_tensor = _load_wav_as_tensor(audio_path)
            print(f"[{_ts()}] [diarize] Audio carregado. Rodando pipeline (pode levar alguns minutos)...", flush=True)

            # Heartbeat: emit progress every 5s so GUI doesn't appear frozen
            heartbeat_stop = threading.Event()
            t0 = time.monotonic()

            def _heartbeat() -> None:
                # Exponential curve calibrated from benchmark data:
                # GPU diarize ~1.7s per minute of audio (tau=120 covers
                # 63% at 2min, 86% at 4min, 95% at 6min).
                tau = 120
                pct_range = file_end_pct - file_start_pct - 3
                while not heartbeat_stop.wait(5):
                    elapsed = int(time.monotonic() - t0)
                    mins, secs = divmod(elapsed, 60)
                    time_str = f"{mins}min {secs:02d}s" if mins else f"{secs}s"
                    frac = 1 - math.exp(-elapsed / tau)
                    pct = file_start_pct + max(1, int(pct_range * min(0.95, frac)))
                    emit("diarize_progress", pct, f"Processando {interview_id}... ({time_str})")
                    print(f"[{_ts()}] [diarize] heartbeat: {time_str} processando...", flush=True)

            heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
            heartbeat_thread.start()
            try:
                output = pipeline(audio_tensor, **speaker_kwargs(config))
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=2)

            elapsed = int(time.monotonic() - t0)
            print(f"[{_ts()}] [diarize] Pipeline concluido em {elapsed}s.", flush=True)
            regular = getattr(output, "speaker_diarization", output)
            exclusive = getattr(output, "exclusive_speaker_diarization", None)
            emit("diarize_progress", file_end_pct - 2, f"Gravando resultados de {interview_id}...")
            write_annotation_outputs(paths, interview_id, "regular", regular, model_name, audio_path)
            if exclusive is not None:
                write_annotation_outputs(paths, interview_id, "exclusive", exclusive, model_name, audio_path)
            status = "ok" if exclusive is not None else "ok_no_exclusive"
            log_job(paths, interview_id, status, model_name, audio_path, "")
            print(f"[{_ts()}] [diarize] {interview_id} concluido: {status}", flush=True)
        except Exception as exc:  # noqa: BLE001 - preserve batch progress and log the failed file.
            failures += 1
            print(f"[{_ts()}] [diarize] ERRO em {interview_id}: {exc}", flush=True)
            log_job(paths, interview_id, "error", model_name, audio_path, str(exc)[-2000:])

    emit("diarize_progress", 100, "Identificacao de falantes concluida.")
    print(f"[{_ts()}] [diarize] Diarizacao finalizada. Falhas: {failures}", flush=True)
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
