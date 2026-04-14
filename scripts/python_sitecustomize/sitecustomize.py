from __future__ import annotations

import os
from pathlib import Path


def _candidate_ffmpeg_bins() -> list[Path]:
    local_appdata = os.environ.get("LOCALAPPDATA")
    candidates: list[Path] = []
    runtime_dir = os.environ.get("TRANSCRITORIO_RUNTIME_DIR")
    if runtime_dir:
        runtime_root = Path(runtime_dir)
        candidates.extend(
            [
                runtime_root / "bin",
                runtime_root / "ffmpeg" / "bin",
                runtime_root / "vendor" / "ffmpeg" / "bin",
            ]
        )
    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root / "runtime" / "bin",
            repo_root / "runtime" / "ffmpeg" / "bin",
            repo_root / "runtime" / "vendor" / "ffmpeg" / "bin",
        ]
    )
    if not local_appdata:
        return [path for path in candidates if (path / "ffmpeg.exe").exists()]
    packages = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
    patterns = [
        "BtbN.FFmpeg.GPL.Shared.7.1_*/*shared-7.1/bin",
        "BtbN.FFmpeg.LGPL.Shared.7.1_*/*shared-7.1/bin",
        "Gyan.FFmpeg.Shared_*/*shared/bin",
    ]
    for pattern in patterns:
        candidates.extend(path for path in packages.glob(pattern) if (path / "ffmpeg.exe").exists())
    return candidates


for ffmpeg_bin in _candidate_ffmpeg_bins():
    try:
        os.add_dll_directory(str(ffmpeg_bin))
    except (AttributeError, FileNotFoundError, OSError):
        pass
    os.environ["PATH"] = f"{ffmpeg_bin}{os.pathsep}{os.environ.get('PATH', '')}"
