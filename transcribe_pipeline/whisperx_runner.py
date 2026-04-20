from __future__ import annotations

import os
import re
import time
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from . import runtime
from . import model_manager
from .model_manager import validate_local_diarization_model
from .utils import append_jsonl, now_utc, run_command_stream


ProgressCallback = Callable[[dict[str, Any]], None]


def run_whisperx(
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
    cache_only = bool(config.get("asr_model_cache_only", True))
    model_cache_dir = str(config.get("model_cache_dir") or runtime.model_cache_dir())
    runtime.apply_secure_hf_environment(offline=cache_only, token_env=token_env)
    output_dir = asr_output_dir(paths, config)
    output_dir.mkdir(parents=True, exist_ok=True)

    for row in selected_rows(rows, ids):
        if should_cancel is not None and should_cancel():
            failures += 1
            break
        wav = paths.project_root / row["wav_path"]
        if config.get("diarize", True):
            validate_local_diarization_model(config.get("diarize_model"))
        device, fell_back = runtime.resolve_device(config.get("asr_device"))
        if fell_back:
            print(f"[Transcritorio] CUDA indisponivel. Usando CPU para transcrever {row['interview_id']}.")
        effective_model = model_manager.resolve_asr_model(str(config["asr_model"]))
        command = [
            runtime.resolve_executable("whisperx"),
            str(wav),
            "--model",
            effective_model,
            "--model_dir",
            model_cache_dir,
            "--device",
            device,
            "--compute_type",
            str(config["asr_compute_type"]),
            "--batch_size",
            str(config["asr_batch_size"]),
            "--output_format",
            "all",
            "--output_dir",
            str(output_dir),
        ]
        if cache_only:
            command.extend(["--model_cache_only", "True"])
        if config.get("asr_language"):
            command.extend(["--language", str(config["asr_language"])])
        add_optional_arg(command, "--beam_size", config.get("asr_beam_size"))
        add_optional_arg(command, "--initial_prompt", config.get("asr_initial_prompt"))
        add_optional_arg(command, "--hotwords", config.get("asr_hotwords"))
        add_optional_arg(command, "--vad_method", config.get("asr_vad_method"))
        add_optional_arg(command, "--vad_onset", config.get("asr_vad_onset"))
        add_optional_arg(command, "--vad_offset", config.get("asr_vad_offset"))
        add_optional_arg(command, "--chunk_size", config.get("asr_chunk_size"))
        add_optional_arg(command, "--align_model", config.get("asr_align_model"))
        if config.get("diarize", True):
            command.append("--diarize")
            add_optional_arg(command, "--min_speakers", config.get("min_speakers"))
            add_optional_arg(command, "--max_speakers", config.get("max_speakers"))
            add_optional_arg(command, "--diarize_model", config.get("diarize_model"))

        redacted = list(command)
        if dry_run:
            print(" ".join(redacted))
            continue

        if config.get("pyannote_metrics_enabled") is not None:
            os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

        tracker = WhisperXProgressTracker(row["interview_id"], progress_callback)
        tracker.emit({"event": "asr_progress", "progress": 1, "message": "Carregando modelo de IA na GPU..."})
        result = run_command_stream(command, cwd=paths.project_root, on_output=tracker.feed, should_cancel=should_cancel)
        cancelled = should_cancel is not None and should_cancel()
        tracker.emit({"event": "asr_done", "progress": 100 if result.returncode == 0 else tracker.last_percent, "message": "WhisperX finalizado."})
        status = "ok" if result.returncode == 0 else "cancelled" if cancelled else "error"
        failures += 0 if result.returncode == 0 else 1
        append_jsonl(
            paths.manifest_dir / "jobs.jsonl",
            {
                "interview_id": row["interview_id"],
                "stage": "transcribe",
                "status": status,
                "started_at": now_utc(),
                "model": config["asr_model"],
                "compute_type": config["asr_compute_type"],
                "batch_size": config["asr_batch_size"],
                "variant": config.get("asr_variant") or "",
                "output_dir": str(output_dir),
                "command": redacted,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
        )
    return failures


class WhisperXProgressTracker:
    def __init__(self, interview_id: str, callback: ProgressCallback | None) -> None:
        self.interview_id = interview_id
        self.callback = callback
        self.tail = ""
        self.last_percent = 1
        self.last_message_at = 0.0
        self._creep_start = time.monotonic()

    def feed(self, chunk: str) -> None:
        self.tail = (self.tail + chunk)[-4000:]
        percent = parse_progress_percent(self.tail)
        if percent is not None and percent != self.last_percent:
            self.last_percent = max(percent, self.last_percent)
            self._creep_start = time.monotonic()
            self.emit({"event": "asr_progress", "progress": self.last_percent, "message": self.current_message()})
            return

        now = time.monotonic()
        if now - self.last_message_at >= 2.0:
            # Creep: advance 1% per 4s when no tqdm progress is detected.
            # Covers model loading (~30s), VAD (~10s), and alignment (~60s)
            # where WhisperX emits no percentage. Cap at 90 to leave room
            # for real tqdm values (which arrive at 90-100%).
            creep_elapsed = now - self._creep_start
            if self.last_percent < 90 and creep_elapsed >= 2.0:
                self.last_percent = min(90, self.last_percent + 1)
                self._creep_start = now
            message = self.current_message() or "Carregando modelo de IA na GPU..."
            self.last_message_at = now
            self.emit({"event": "asr_progress", "progress": self.last_percent, "message": message})

    def current_message(self) -> str:
        text = self.tail.replace("\r", "\n")
        lines = [clean_output_line(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        return lines[-1] if lines else ""

    def emit(self, payload: dict[str, Any]) -> None:
        if self.callback is None:
            return
        payload = dict(payload)
        payload.setdefault("file_id", self.interview_id)
        self.callback(payload)


def parse_progress_percent(text: str) -> int | None:
    matches = re.findall(r"(?<!\d)(\d{1,3})\s*%", text)
    if not matches:
        return None
    value = max(0, min(100, int(matches[-1])))
    return value


def clean_output_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def add_optional_arg(command: list[str], flag: str, value) -> None:
    if value is None or value == "":
        return
    command.extend([flag, str(value)])


def asr_output_dir(paths: Paths, config: dict):
    variant = config.get("asr_variant")
    if not variant:
        return paths.asr_dir
    safe_variant = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(variant)).strip("._")
    if not safe_variant:
        raise ValueError("Invalid empty ASR variant name after sanitization.")
    return paths.asr_variants_dir / safe_variant
