from __future__ import annotations

from pathlib import Path

from .config import Paths
from .manifest import selected_rows
from .runtime import resolve_executable
from .utils import append_jsonl, now_utc, run_command


def prepare_audio(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    failures = 0
    for row in selected_rows(rows, ids):
        source = paths.project_root / row["source_path"]
        wav = paths.project_root / row["wav_path"]
        wav.parent.mkdir(parents=True, exist_ok=True)
        command = [
            resolve_executable("ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-y" if force else "-n",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            str(config["wav_channels"]),
            "-ar",
            str(config["wav_sample_rate"]),
            "-c:a",
            "pcm_s16le",
            str(wav),
        ]

        if dry_run:
            print(" ".join(command))
            continue
        if wav.exists() and not force:
            _log(paths, row, "prepare-audio", "skipped", command, "wav already exists")
            continue

        result = run_command(command, cwd=paths.project_root)
        status = "ok" if result.returncode == 0 else "error"
        failures += 0 if result.returncode == 0 else 1
        _log(paths, row, "prepare-audio", status, command, result.stderr[-2000:])
    return failures


def probe_duration(path: Path) -> float | None:
    result = run_command(
        [
            resolve_executable("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _log(paths: Paths, row: dict[str, str], stage: str, status: str, command: list[str], message: str) -> None:
    append_jsonl(
        paths.manifest_dir / "jobs.jsonl",
        {
            "interview_id": row["interview_id"],
            "stage": stage,
            "status": status,
            "started_at": now_utc(),
            "command": command,
            "message": message,
        },
    )
