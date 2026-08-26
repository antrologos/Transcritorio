from __future__ import annotations

from pathlib import Path

from .config import Paths
from .manifest import selected_rows
from .runtime import resolve_executable
from .utils import append_jsonl, now_utc, run_command

MAX_SPLIT_CHANNELS = 8


def channel_wav_commands(
    ffmpeg: str,
    source: Path,
    wav: Path,
    n_channels: int,
    sample_rate: int,
    force: bool,
) -> list[tuple[Path, list[str]]]:
    """Comandos de extracao por canal (puro, testavel sem ffmpeg).

    Fase 4: alem do WAV mono (inalterado), cada canal da fonte vira
    {id}.ch{n}.wav mono 16k — insumo da analise de canais (channels.py).
    """
    commands: list[tuple[Path, list[str]]] = []
    for index in range(min(int(n_channels), MAX_SPLIT_CHANNELS)):
        target = wav.with_name(f"{wav.stem}.ch{index}.wav")
        commands.append((target, [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-y" if force else "-n",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"pan=mono|c0=c{index}",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(target),
        ]))
    return commands


def _source_channels(row: dict[str, str]) -> int:
    try:
        return int(str(row.get("source_audio_channels") or "").strip() or 0)
    except ValueError:
        return 0


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
        elif wav.exists() and not force:
            _log(paths, row, "prepare-audio", "skipped", command, "wav already exists")
        else:
            result = run_command(command, cwd=paths.project_root)
            status = "ok" if result.returncode == 0 else "error"
            failures += 0 if result.returncode == 0 else 1
            _log(paths, row, "prepare-audio", status, command, result.stderr[-2000:])

        # Fase 4: fontes com >= 2 canais ganham tambem um WAV por canal
        # ({id}.ch{n}.wav) — insumo da analise de canais. Mono: nada muda.
        n_channels = _source_channels(row)
        if n_channels >= 2 and bool(config.get("wav_split_channels", True)):
            for target, channel_command in channel_wav_commands(
                    resolve_executable("ffmpeg"), source, wav, n_channels,
                    int(config["wav_sample_rate"]), force):
                if dry_run:
                    print(" ".join(channel_command))
                    continue
                if target.exists() and not force:
                    _log(paths, row, "prepare-channels", "skipped", channel_command, "channel wav already exists")
                    continue
                result = run_command(channel_command, cwd=paths.project_root)
                status = "ok" if result.returncode == 0 else "error"
                failures += 0 if result.returncode == 0 else 1
                _log(paths, row, "prepare-channels", status, channel_command, result.stderr[-2000:])
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
