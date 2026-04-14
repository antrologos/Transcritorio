# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Transcritorio.

Produces a single output directory (onedir) containing:
  Transcritorio.exe      – PySide6 GUI (no console window)
  transcritorio-cli.exe  – CLI with subcommands (manifest, transcribe, models …)
  whisperx.exe           – WhisperX ASR subprocess called by whisperx_runner.py

Run with:
  pyinstaller --distpath dist --workpath build --clean packaging/transcritorio.spec
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(SPECPATH).resolve().parent          # d:/Dropbox/Transcritorio
PACKAGE_DIR = REPO_ROOT / "transcribe_pipeline"
ASSETS_DIR = REPO_ROOT / "assets"
PACKAGING_DIR = REPO_ROOT / "packaging"
HOOKS_DIR = PACKAGING_DIR / "hooks"
VENDOR_FFMPEG_BIN = PACKAGING_DIR / "vendor" / "ffmpeg" / "bin"

# ---------------------------------------------------------------------------
# Hidden imports  (lazy-imported modules PyInstaller cannot detect)
# ---------------------------------------------------------------------------
hidden_imports = [
    # huggingface_hub – lazy-imported in model_manager.py
    "huggingface_hub",
    "huggingface_hub.snapshot_download",
    # PySide6 multimedia (used by review_studio_qt.py)
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    # scipy / soundfile / librosa – used by whisperx / pyannote
    "scipy",
    "scipy.signal",
    "soundfile",
    "librosa",
    # sklearn – used by pyannote
    "sklearn",
    "sklearn.cluster",
    "sklearn.utils",
    # Other pyannote dependencies
    "asteroid_filterbanks",
    "speechbrain",
]

# collect_submodules for heavy packages whose internal structure is complex
for pkg in ("torch", "torchaudio", "torchvision"):
    hidden_imports += collect_submodules(pkg)

# transformers: only collect the core + models actually used by whisperx/pyannote
# (collecting ALL of transformers adds ~600 unused model modules and ~3 GB)
hidden_imports += [
    "transformers",
    "transformers.models.wav2vec2",
    "transformers.models.whisper",
    "transformers.models.auto",
    "transformers.pipelines",
    "transformers.tokenization_utils",
    "transformers.tokenization_utils_fast",
    "transformers.feature_extraction_utils",
    "transformers.modeling_utils",
    "transformers.configuration_utils",
]
try:
    # Collect non-model submodules (utils, generation, etc.) but skip models.*
    for sub in collect_submodules("transformers"):
        if not sub.startswith("transformers.models.") or any(
            sub.startswith(f"transformers.models.{m}")
            for m in ("wav2vec2", "whisper", "auto")
        ):
            hidden_imports.append(sub)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
datas = [
    # Application assets (icon SVG, etc.)
    (str(ASSETS_DIR), "assets"),
]

# Collect data files that packages need at runtime (configs, YAML, etc.)
for pkg in (
    "torch",
    "torchaudio",
    "transformers",
    "pyannote.audio",
    "pyannote.pipeline",
    "whisperx",
    "lightning",
    "lightning_fabric",
    "PySide6",
):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# External binaries
# ---------------------------------------------------------------------------
binaries = []

# FFmpeg (staged by build.ps1 into packaging/vendor/ffmpeg/)
if VENDOR_FFMPEG_BIN.exists():
    # Include all files from FFmpeg bin (exe + shared DLLs)
    for f in VENDOR_FFMPEG_BIN.iterdir():
        if f.is_file():
            binaries.append((str(f), "vendor/ffmpeg/bin"))

# ---------------------------------------------------------------------------
# Excludes  (reduce bundle size)
# ---------------------------------------------------------------------------
excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "sphinx",
    "docutils",
    # caffe2 / triton – not needed for local inference
    "caffe2",
    "triton",
]

# ---------------------------------------------------------------------------
# Shared analysis kwargs
# ---------------------------------------------------------------------------
common_kwargs = dict(
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[str(HOOKS_DIR)],
    hooksconfig={},
    runtime_hooks=[str(PACKAGING_DIR / "runtime_hook.py")],
    excludes=excludes,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Analysis: GUI entry point
# ---------------------------------------------------------------------------
gui_a = Analysis(
    [str(PACKAGING_DIR / "gui_entry.py")],
    **common_kwargs,
)
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="Transcritorio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ASSETS_DIR / "transcritorio_icon.ico") if (ASSETS_DIR / "transcritorio_icon.ico").exists() else None,
)

# ---------------------------------------------------------------------------
# Analysis: CLI entry point
# ---------------------------------------------------------------------------
cli_a = Analysis(
    [str(PACKAGING_DIR / "cli_entry.py")],
    **common_kwargs,
)
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="transcritorio-cli",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ASSETS_DIR / "transcritorio_icon.ico") if (ASSETS_DIR / "transcritorio_icon.ico").exists() else None,
)

# ---------------------------------------------------------------------------
# Analysis: WhisperX subprocess entry point
# ---------------------------------------------------------------------------
wx_a = Analysis(
    [str(PACKAGING_DIR / "whisperx_entry.py")],
    **common_kwargs,
)
wx_pyz = PYZ(wx_a.pure)
wx_exe = EXE(
    wx_pyz,
    wx_a.scripts,
    [],
    exclude_binaries=True,
    name="whisperx",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ASSETS_DIR / "transcritorio_icon.ico") if (ASSETS_DIR / "transcritorio_icon.ico").exists() else None,
)

# ---------------------------------------------------------------------------
# COLLECT: merge all three executables into one directory
# ---------------------------------------------------------------------------
coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    wx_exe, wx_a.binaries, wx_a.datas,
    strip=False,
    upx=False,
    name="Transcritorio",
)
