"""Filtro de entradas do bundle PyInstaller — logica testavel.

Antes vivia inline em `transcritorio.spec`. Extraido para permitir:
  1. Toy tests (tests/toy_bundle_filter.py) sem precisar rodar PyInstaller.
  2. Variant-aware excludes: variant='cpu' descarta torch_cuda + cudnn +
     cublas (~3 GB) do bundle; variant='full' preserva CUDA.

Uso no spec:
    from bundle_filter import should_exclude_entry
    variant = os.environ.get("TRANSCRITORIO_BUNDLE_VARIANT", "full")
    analysis.binaries = [b for b in analysis.binaries
                         if not should_exclude_entry(b[0], variant)]
"""
from __future__ import annotations

import fnmatch
import os

# ---------------------------------------------------------------------------
# File extensions sempre excluidas (build artifacts)
# ---------------------------------------------------------------------------
FILE_EXCLUDE_PATTERNS = frozenset({"*.lib", "*.h", "*.hpp", "*.cuh", "*.cpp", "*.pyi", "*.cmake"})

# ---------------------------------------------------------------------------
# CUDA DLLs: duas listas segundo o variant.
# ---------------------------------------------------------------------------

# MINIMAL (variant='full' — comportamento atual): remove DLLs CUDA
# redundantes/desnecessarias para inference-only. Preserva torch_cuda,
# cudnn e cublas que sao usados pela aceleracao real.
CUDA_DLL_EXCLUDES_MINIMAL = [
    "cusolverMg64",     # multi-GPU solver — laptop tem 1 GPU
    "cusparse64",       # sparse linalg — Whisper/pyannote usam dense
    "cufft64",          # FFT — feito pelo FFmpeg, nao pelo CUDA
    "cufftw64",
    "curand64",         # random nums — deterministico em inference
    "nvrtc64_120_0.alt",  # runtime compiler alternativo — torch.compile nao usado
    "nvJitLink",        # JIT linker — sem kernels customizados
]

# CPU-ONLY (variant='cpu'): remove TODO o stack CUDA — torch_cuda
# (~982 MB Windows, ~similar Linux), cudnn* (~180 MB), cublas*,
# c10_cuda, cudart, nvrtc. Total removido: ~3 GB.
#
# Prefixos sao cross-plataforma: _shared_lib_stem() strippa o 'lib'
# prefix do Linux e o sufixo versionado (.so, .so.N, .dylib, .dll),
# entao 'cudnn' matches Windows 'cudnn64_9.dll' E Linux 'libcudnn.so.9'.
CUDA_DLL_EXCLUDES_CPU_EXTRA = [
    "torch_cuda",        # Win: torch_cuda.dll  Linux: libtorch_cuda.so
    "cudnn",             # cudnn*, cudnn_adv*, cudnn_ops*, cudnn_graph*, etc.
    "cublas",            # cublas*, cublasLt*  (cublaslt via lowercase)
    "caffe2_nvrtc",
    "c10_cuda",
    "cudart",
    "nvrtc",             # nvrtc*, nvrtc-builtins* (era em MINIMAL tambem)
]

# ---------------------------------------------------------------------------
# PySide6: dev executables + plugin whitelist
# ---------------------------------------------------------------------------
PYSIDE6_DEV_EXES = frozenset({
    "designer.exe", "linguist.exe", "lrelease.exe", "lupdate.exe",
    "qmlformat.exe", "qmlls.exe", "qmllint.exe", "qmldom.exe",
    "qmltyperegistrar.exe", "qsb.exe", "balsam.exe", "balsamui.exe",
    "meshdebug.exe", "qmltc.exe", "qmlimportscanner.exe",
    "qmlcachegen.exe", "qtdiag.exe", "qtpaths.exe",
})

# Qt plugins preservados (resto descartado)
QT_PLUGINS_KEEP = frozenset({
    "platforms", "styles", "imageformats", "multimedia",
    "generic", "iconengines", "platforminputcontexts",
})


def _cuda_excludes_for_variant(variant: str) -> list[str]:
    """Retorna a lista de prefixos CUDA a excluir segundo o variant."""
    base = list(CUDA_DLL_EXCLUDES_MINIMAL)
    if variant == "cpu":
        base.extend(CUDA_DLL_EXCLUDES_CPU_EXTRA)
    return base


_SHARED_LIB_EXTS = (".dll", ".so", ".dylib")


def _shared_lib_stem(basename: str) -> str | None:
    """Se basename e uma shared lib (.dll / .so[.N] / .dylib), retorna
    o 'stem' normalizado sem prefixo lib* (Linux) e sem sufixo de versao.

    Exemplos:
        torch_cuda.dll          -> torch_cuda
        libtorch_cuda.so        -> torch_cuda
        libtorch_cuda.so.9.3.0  -> torch_cuda
        libcudnn.dylib          -> cudnn
        config.dll              -> config
        torch_cuda.py           -> None  (nao e shared lib)

    Retorna None se nao for shared lib reconhecida.
    """
    # .dll e .dylib: sempre no final
    for ext in (".dll", ".dylib"):
        if basename.endswith(ext):
            stem = basename[: -len(ext)]
            if stem.startswith("lib"):
                stem = stem[3:]
            return stem
    # .so: aparece sozinho ou com sufixo versionado (.so.N, .so.N.M)
    if ".so" in basename:
        # corta tudo apos a primeira ocorrencia de ".so"
        idx = basename.find(".so")
        # valida que o que segue e '' ou '.N' (digit)
        tail = basename[idx + 3:]
        if tail == "" or (tail.startswith(".") and all(c.isdigit() or c == "." for c in tail[1:])):
            stem = basename[:idx]
            if stem.startswith("lib"):
                stem = stem[3:]
            return stem
    return None


def should_exclude_entry(name: str, variant: str = "full") -> bool:
    """Return True if this TOC entry should be stripped from the bundle.

    Args:
        name: path of the entry (forward or back slashes OK).
        variant: "full" (preserve CUDA) or "cpu" (strip CUDA stack).

    Matches CUDA libs across Windows (.dll), Linux (.so[.N]) and Mac
    (.dylib) via a normalized stem (strip extension + 'lib' prefix).
    """
    basename = os.path.basename(name).lower()

    # Build artifacts: .lib, .h, .hpp, .pyi, etc.
    if any(fnmatch.fnmatch(basename, pat) for pat in FILE_EXCLUDE_PATTERNS):
        return True

    # CUDA shared libs conforme o variant (.dll / .so / .dylib cobertos)
    stem = _shared_lib_stem(basename)
    if stem is not None:
        for cuda_prefix in _cuda_excludes_for_variant(variant):
            if stem.startswith(cuda_prefix.lower()):
                return True

    # PySide6 dev executables
    if basename in PYSIDE6_DEV_EXES:
        return True

    # Qt plugins: manter so whitelist
    name_fwd = name.replace("\\", "/")
    if "/plugins/" in name_fwd:
        parts = name_fwd.split("/plugins/")
        if len(parts) > 1:
            plugin_dir = parts[1].split("/")[0]
            if plugin_dir not in QT_PLUGINS_KEEP:
                return True

    # PySide6 unnecessary data
    if basename == "opengl32sw.dll":
        return True
    if "webengine" in basename.lower():
        return True
    if basename.startswith("qtwebengine"):
        return True

    return False
