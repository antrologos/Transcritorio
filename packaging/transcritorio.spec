# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Transcritorio.

Produces a single output directory (onedir) containing:
  Transcritorio.exe      – PySide6 GUI (no console window)
  transcritorio-cli.exe  – CLI with subcommands (manifest, transcribe, models …)
  whisperx.exe           – WhisperX ASR subprocess called by whisperx_runner.py

Run with:
  pyinstaller --distpath dist --workpath build --clean --noconfirm packaging/transcritorio.spec
"""

import fnmatch
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(SPECPATH).resolve().parent.parent    # d:/Dropbox/Transcritorio (SPECPATH is in packaging/)
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
# NOTE: torchvision excluded – not imported by any code in the pipeline
for pkg in ("torch", "torchaudio"):
    hidden_imports += collect_submodules(pkg)

# transformers: only collect core + models actually used by whisperx/pyannote
# (collecting ALL of transformers adds ~600 unused model modules)
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
# NOTE: "torch" is NOT listed here — the pyinstaller-hooks-contrib hook-torch.py
# already collects torch data with proper excludes (*.lib, *.h, *.pyi, etc.).
# Adding it here without excludes was re-introducing 2.7 GB of static libs.
_DATA_FILE_EXCLUDES = ["**/*.h", "**/*.hpp", "**/*.cuh", "**/*.lib",
                       "**/*.cpp", "**/*.pyi", "**/*.cmake"]

for pkg in (
    "torchaudio",
    "transformers",
    "pyannote.audio",
    "pyannote.pipeline",
    "whisperx",
    "lightning",
    "lightning_fabric",
):
    try:
        datas += collect_data_files(pkg, excludes=_DATA_FILE_EXCLUDES)
    except Exception:
        pass

# PySide6: collect only essential data (skip translations, qml, metatypes, etc.)
try:
    for entry in collect_data_files("PySide6"):
        dest = entry[0]
        # Skip large unnecessary PySide6 data directories
        if any(part in dest for part in (
            "/translations/", "\\translations\\",
            "/qml/", "\\qml\\",
            "/metatypes/", "\\metatypes\\",
            "/typesystems/", "\\typesystems\\",
            "/include/", "\\include\\",
            "/glue/", "\\glue\\",
            "/scripts/", "\\scripts\\",
            "/doc/", "\\doc\\",
        )):
            continue
        # Skip dev-only files
        if dest.endswith((".pyi", ".lib", ".h")):
            continue
        datas.append(entry)
except Exception:
    pass

# ---------------------------------------------------------------------------
# External binaries
# ---------------------------------------------------------------------------
binaries = []

# FFmpeg (staged by build.ps1 into packaging/vendor/ffmpeg/)
if VENDOR_FFMPEG_BIN.exists():
    for f in VENDOR_FFMPEG_BIN.iterdir():
        if f.is_file():
            binaries.append((str(f), "vendor/ffmpeg/bin"))

# ---------------------------------------------------------------------------
# Excludes  (reduce bundle size)
# ---------------------------------------------------------------------------
excludes = [
    # General
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "sphinx",
    "docutils",
    # Torch: not needed for local inference
    "caffe2",
    "triton",
    # torchvision: not used by any code in the pipeline (verified by grep)
    "torchvision",
    # PySide6: modules not used by Transcritorio (Widgets + Multimedia only)
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtDesigner",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtQmlCore",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation",
    "PySide6.QtCharts",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtLocation",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtSensors",
    "PySide6.QtTest",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtDBus",
    "PySide6.QtConcurrent",
    "PySide6.QtRemoteObjects",
    "PySide6.QtWebSockets",
    "PySide6.QtHttpServer",
    "PySide6.QtTextToSpeech",
    "PySide6.QtSpatialAudio",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtNetworkAuth",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtUiTools",
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

# ---------------------------------------------------------------------------
# Post-analysis filtering: remove build artifacts and unnecessary CUDA DLLs
# ---------------------------------------------------------------------------
# Patterns for files that are build-time artifacts, never needed at runtime
_FILE_EXCLUDE_PATTERNS = {"*.lib", "*.h", "*.hpp", "*.cuh", "*.cpp", "*.pyi", "*.cmake"}

# CUDA DLLs confirmed safe to remove for inference-only (tested individually):
# - cusolverMg: multi-GPU solver (single GPU laptop)
# - cusparse: sparse linear algebra (Whisper/pyannote use dense ops)
# - cufft/cufftw: FFT (audio FFT done by FFmpeg, not CUDA)
# - curand: random number gen (not needed for deterministic inference)
# - nvrtc.alt: alternate runtime compiler (torch.compile not used)
# - nvJitLink: JIT linker (no custom CUDA kernels)
_CUDA_DLL_EXCLUDES = [
    "cusolverMg64",
    "cusparse64",
    "cufft64",
    "cufftw64",
    "curand64",
    "nvrtc64_120_0.alt",
    "nvJitLink",
]

# PySide6 dev executables (designer, qmlls, qmlformat, etc.)
_PYSIDE6_DEV_EXES = {
    "designer.exe", "linguist.exe", "lrelease.exe", "lupdate.exe",
    "qmlformat.exe", "qmlls.exe", "qmllint.exe", "qmldom.exe",
    "qmltyperegistrar.exe", "qsb.exe", "balsam.exe", "balsamui.exe",
    "meshdebug.exe", "qmltc.exe", "qmlimportscanner.exe",
    "qmlcachegen.exe", "qtdiag.exe", "qtpaths.exe",
}

# Qt plugins to keep (the rest are unnecessary for Widgets + Multimedia)
_QT_PLUGINS_KEEP = {"platforms", "styles", "imageformats", "multimedia",
                     "generic", "iconengines", "platforminputcontexts"}


def _should_exclude_entry(name: str) -> bool:
    """Return True if this TOC entry should be stripped from the bundle."""
    basename = os.path.basename(name).lower()

    # Build artifacts: .lib, .h, .hpp, .pyi, etc.
    if any(fnmatch.fnmatch(basename, pat) for pat in _FILE_EXCLUDE_PATTERNS):
        return True

    # CUDA DLLs not needed for inference
    if basename.endswith(".dll"):
        for cuda_prefix in _CUDA_DLL_EXCLUDES:
            if basename.startswith(cuda_prefix.lower()):
                return True

    # PySide6 dev executables
    if basename in _PYSIDE6_DEV_EXES:
        return True

    # Qt plugins: keep only essential ones
    name_fwd = name.replace("\\", "/")
    if "/plugins/" in name_fwd:
        parts = name_fwd.split("/plugins/")
        if len(parts) > 1:
            plugin_dir = parts[1].split("/")[0]
            if plugin_dir not in _QT_PLUGINS_KEEP:
                return True

    # PySide6 unnecessary data: opengl32sw, WebEngine resources, etc.
    if basename == "opengl32sw.dll":
        return True
    if "webengine" in basename.lower():
        return True
    if basename.startswith("qtwebengine"):
        return True

    return False


# Apply filtering to ALL three Analysis objects
for analysis in (gui_a,):
    analysis.datas = [d for d in analysis.datas if not _should_exclude_entry(d[0])]
    analysis.binaries = [b for b in analysis.binaries if not _should_exclude_entry(b[0])]

# ---------------------------------------------------------------------------
# PYZ + EXE: GUI
# ---------------------------------------------------------------------------
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
# Analysis + PYZ + EXE: CLI
# ---------------------------------------------------------------------------
cli_a = Analysis(
    [str(PACKAGING_DIR / "cli_entry.py")],
    **common_kwargs,
)
cli_a.datas = [d for d in cli_a.datas if not _should_exclude_entry(d[0])]
cli_a.binaries = [b for b in cli_a.binaries if not _should_exclude_entry(b[0])]

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
# Analysis + PYZ + EXE: WhisperX subprocess
# ---------------------------------------------------------------------------
wx_a = Analysis(
    [str(PACKAGING_DIR / "whisperx_entry.py")],
    **common_kwargs,
)
wx_a.datas = [d for d in wx_a.datas if not _should_exclude_entry(d[0])]
wx_a.binaries = [b for b in wx_a.binaries if not _should_exclude_entry(b[0])]

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
