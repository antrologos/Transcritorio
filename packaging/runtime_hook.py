"""PyInstaller runtime hook for Transcritorio.

Runs before application code in the frozen bundle.
Registers FFmpeg DLLs, sets environment variables, and configures
the runtime directory so that resolve_executable() finds bundled
executables (whisperx, ffmpeg, ffprobe).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

if getattr(sys, "frozen", False):
    # In onedir mode _MEIPASS == exe parent; in onefile it is a temp dir.
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

    # Let runtime.py resolve_executable() find bundled binaries.
    os.environ.setdefault("TRANSCRITORIO_RUNTIME_DIR", str(bundle_dir))

    # Register FFmpeg DLLs for torchcodec and PySide6 multimedia.
    for candidate in (
        bundle_dir / "vendor" / "ffmpeg" / "bin",
        bundle_dir / "ffmpeg" / "bin",
        bundle_dir,
    ):
        if (candidate / "ffmpeg.exe").exists() or (candidate / "ffmpeg").exists():
            try:
                os.add_dll_directory(str(candidate))
            except (AttributeError, OSError):
                pass
            path = os.environ.get("PATH", "")
            if str(candidate) not in path:
                os.environ["PATH"] = f"{candidate}{os.pathsep}{path}"
            break

    # Lazy CUDA loading for faster startup.
    os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
