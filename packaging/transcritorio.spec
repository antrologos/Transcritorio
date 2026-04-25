# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Transcritorio.

Produces a single output directory (onedir) containing:
  Transcritorio.exe      – PySide6 GUI (no console window)
  transcritorio-cli.exe  – CLI with subcommands (manifest, transcribe, models …)
  whisperx.exe           – WhisperX ASR subprocess called by whisperx_runner.py

Run with:
  pyinstaller --distpath dist --workpath build --clean --noconfirm packaging/transcritorio.spec
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# ---------------------------------------------------------------------------
# Paths — use env var if set by build.ps1 (avoids SPECPATH ambiguity)
# ---------------------------------------------------------------------------
_env_root = os.environ.get("TRANSCRITORIO_REPO_ROOT")
if _env_root:
    REPO_ROOT = Path(_env_root).resolve()
else:
    # Fallback: SPECPATH is the spec file's directory, go up one level.
    REPO_ROOT = Path(SPECPATH).resolve().parent
    print(f"WARNING: TRANSCRITORIO_REPO_ROOT not set. Using fallback: {REPO_ROOT}")
    print("WARNING: Run via build.ps1 for verified builds.")
PACKAGE_DIR = REPO_ROOT / "transcribe_pipeline"
ASSETS_DIR = REPO_ROOT / "assets"
PACKAGING_DIR = REPO_ROOT / "packaging"
HOOKS_DIR = PACKAGING_DIR / "hooks"
VENDOR_FFMPEG_BIN = PACKAGING_DIR / "vendor" / "ffmpeg" / "bin"

# ---------------------------------------------------------------------------
# Bundle variant — "full" (default, com CUDA) ou "cpu" (strip CUDA, ~3 GB menor)
# ---------------------------------------------------------------------------
BUNDLE_VARIANT = os.environ.get("TRANSCRITORIO_BUNDLE_VARIANT", "full").lower()
if BUNDLE_VARIANT not in ("full", "cpu"):
    print(f"WARNING: TRANSCRITORIO_BUNDLE_VARIANT={BUNDLE_VARIANT!r} invalido, usando 'full'")
    BUNDLE_VARIANT = "full"
print(f"=== Bundle variant: {BUNDLE_VARIANT} ===")

# Importa filtro testavel (tests/toy_bundle_filter.py valida a logica)
sys.path.insert(0, str(PACKAGING_DIR))
from bundle_filter import should_exclude_entry  # noqa: E402

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
    # torchcodec: importado lazy por pyannote.audio.core.io e transformers
    "torchcodec",
]
try:
    hidden_imports += collect_submodules("torchcodec")
except Exception:
    pass

# collect_submodules for heavy packages whose internal structure is complex
# torchvision is NOT used by Transcritorio code, but pyannote.audio ->
# lightning -> torchmetrics imports torchmetrics.functional.image.arniqa
# eagerly, which requires torchvision. Excluding torchvision breaks the
# whisperx import chain at runtime (CI gate "Frozen-bundle whisperx
# import chain" caught this in v0.1.3).
for pkg in ("torch", "torchaudio", "torchvision"):
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

# mlx-whisper: only available on macOS arm64. On Windows/Linux builds this
# import fails silently and the runtime code takes the whisperx CLI path.
try:
    import mlx_whisper  # noqa: F401
    hidden_imports += collect_submodules("mlx_whisper")
    hidden_imports += collect_submodules("mlx")
    print(f"=== mlx-whisper detected; bundling MLX acceleration ({sys.platform}) ===")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
datas = [
    # Application assets (icon SVG, etc.)
    (str(ASSETS_DIR), "assets"),
]

# copy_metadata: forca a inclusao de <pkg>-*.dist-info no bundle.
# Necessario porque transformers/audio_utils.py faz
# importlib.metadata.version("torchcodec") na hora de importar o modulo
# quando is_torchcodec_available() retorna True. Sem dist-info, o frozen
# bundle crasha com PackageNotFoundError antes de qualquer fallback.
# Outros pacotes listados aqui tambem consultam a propria versao via
# importlib.metadata.version("<pkg>") — defensivo para evitar o mesmo
# PackageNotFoundError em caminhos menos testados.
for _meta_pkg in (
    "torchcodec",        # transformers.audio_utils:55 queries it unconditionally
    "torch",
    "torchaudio",
    "transformers",
    "huggingface_hub",
    "tokenizers",
    "tqdm",
    "regex",
    "requests",
    "packaging",
    "filelock",
    "pyyaml",
    "numpy",
):
    try:
        datas += copy_metadata(_meta_pkg)
    except Exception as _exc:
        print(f"WARNING: copy_metadata({_meta_pkg!r}) falhou: {_exc}")

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

# -----------------------------------------------------------------------
# Lazy-load CUDA DLLs (Windows cu128 only) — 2026-04-23
# PyInstaller hook-torch collects only IAT imports; the 14 DLLs that
# torch loads via dlopen (cudnn engines, nvrtc, caffe2_nvrtc, curand,
# cufftw, cusolverMg) are invisible to static analysis. Add them
# explicitly so:
#   1. variant=full bundle is complete (GPU Conv/LSTM work)
#   2. variant=cpu + split_bundle moves them to cuda_pack for on-demand
#      download (keeps base bundle small while GPU users still get them)
# -----------------------------------------------------------------------
if sys.platform == "win32":
    _LAZY_CUDA_DLLS = [
        "cudnn_adv64_9.dll", "cudnn_cnn64_9.dll",
        "cudnn_engines_precompiled64_9.dll",
        "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn_graph64_9.dll", "cudnn_heuristic64_9.dll",
        "cudnn_ops64_9.dll",
        "caffe2_nvrtc.dll",
        "cufftw64_11.dll", "curand64_10.dll", "cusolverMg64_11.dll",
        "nvrtc-builtins64_128.dll", "nvrtc64_120_0.alt.dll", "nvrtc64_120_0.dll",
    ]
    try:
        import torch as _torch_probe
        _torch_lib = Path(_torch_probe.__file__).resolve().parent / "lib"
        _found = 0
        for _name in _LAZY_CUDA_DLLS:
            _p = _torch_lib / _name
            if _p.exists():
                binaries.append((str(_p), "torch/lib"))
                _found += 1
        print(f"=== Lazy-load CUDA DLLs collected: {_found}/{len(_LAZY_CUDA_DLLS)} ===")
    except Exception as _exc:
        print(f"WARNING: lazy-load CUDA DLLs collection failed: {_exc}")

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
    # NB: torchvision was previously excluded — but torchmetrics.functional.image.arniqa
    # (loaded transitively by lightning -> torchmetrics on whisperx import) imports
    # torchvision eagerly at module load. Excluding it breaks the whole frozen chain
    # with `ModuleNotFoundError: No module named 'torchvision'`. v0.1.3 CI gate caught.
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
# Post-analysis filtering: delegado ao packaging/bundle_filter.py
# (testado por tests/toy_bundle_filter.py). Comportamento varia com
# BUNDLE_VARIANT ("full" preserva CUDA; "cpu" remove ~3 GB de CUDA DLLs).
# ---------------------------------------------------------------------------
def _exclude(name: str) -> bool:
    return should_exclude_entry(name, BUNDLE_VARIANT)


# Apply filtering to ALL three Analysis objects
for analysis in (gui_a,):
    analysis.datas = [d for d in analysis.datas if not _exclude(d[0])]
    analysis.binaries = [b for b in analysis.binaries if not _exclude(b[0])]

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
cli_a.datas = [d for d in cli_a.datas if not _exclude(d[0])]
cli_a.binaries = [b for b in cli_a.binaries if not _exclude(b[0])]

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
wx_a.datas = [d for d in wx_a.datas if not _exclude(d[0])]
wx_a.binaries = [b for b in wx_a.binaries if not _exclude(b[0])]

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

# ---------------------------------------------------------------------------
# macOS: wrap COLLECT output in a .app bundle so Finder + Launchpad + .dmg
# recognize it as an application (release.yml uses this to produce a .dmg).
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    # Prefer .icns (generated by release.yml from the .svg); fall back to no icon.
    _icns_path = ASSETS_DIR / "transcritorio_icon.icns"
    _icon_arg = str(_icns_path) if _icns_path.exists() else None
    app = BUNDLE(
        coll,
        name="Transcritorio.app",
        icon=_icon_arg,
        bundle_identifier="com.antrologos.transcritorio",
        info_plist={
            "CFBundleDisplayName": "Transcritorio",
            "CFBundleShortVersionString": "0.1.5",
            "CFBundleVersion": "0.1.5",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "O Transcritorio nao captura audio diretamente — trabalha com "
                "arquivos ja gravados. Esta permissao aparece por causa do "
                "framework de multimedia do macOS."
            ),
            "LSMinimumSystemVersion": "11.0",
        },
    )
