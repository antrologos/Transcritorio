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

# MINIMAL (variant='full'): vazia em 2026-04-23.
#
# Antes listava cusolverMg64/cufftw64/curand64/nvrtc64.alt para strippar
# sempre. Mas o spec.py agora adiciona explicitamente as 14 DLLs lazy-load
# para o bundle (via binaries list) de modo que split_bundle possa rotear
# TODAS para cuda_pack. Se MINIMAL strippar 4 dessas 14 em variant=full,
# o cuda_pack fica incompleto e GPU inference quebra parcialmente.
#
# Semantica: variant=full produz bundle completo (nada CUDA removido);
# variant=cpu remove as 14 lazy-load (CPU_EXTRA); split_bundle em CI
# assume que build foi variant=full e usa `_exclude(variant=cpu)` para
# decidir o que mover pro cuda_pack.
CUDA_DLL_EXCLUDES_MINIMAL: list[str] = []

# CPU-ONLY (variant='cpu'): remove APENAS as 14 DLLs CUDA que o torch
# cu128 carrega sob demanda via dlopen (nao via IAT). Descoberto
# empiricamente em 2026-04-23 analisando os imports PE com pefile:
# `torch_cpu.dll`, `torch.dll` e `shm.dll` tem 11 DLLs CUDA no IAT
# (torch_cuda, cublas, cublasLt, cusparse, cufft, cusolver, cudnn64,
# cupti, cudart, c10_cuda, nvJitLink). Elas SAO obrigatorias — sem
# elas `import torch` falha com OSError [WinError 126].
#
# As 14 listadas aqui sao lazy-loaded: cudnn_ops/cnn/adv/engines_*/
# heuristic/graph (pelo cudnn64_9.dll quando chamam conv/lstm),
# nvrtc/nvrtc-builtins (torch.compile), curand (CUDA RNG),
# cusolverMg (multi-GPU), cufftw (FFTW wrapper), caffe2_nvrtc.
# Remover so essas economiza ~1.3 GB e mantem `import torch` funcional.
# cuda_pack on-demand traz essas 14 de volta quando o usuario ativa GPU.
#
# IMPORTANTE: os prefixos sao exatos pra evitar colisoes. "cudnn" puro
# casaria com cudnn64_9.dll (obrigatoria) — por isso listamos cudnn_*
# com underscore. "cublas" puro casaria com cublas64_12 (obrigatoria).
# cross-plataforma: _shared_lib_stem() strippa prefixo 'lib' do Linux.
CUDA_DLL_EXCLUDES_CPU_EXTRA = [
    # cuDNN engines (dlopen pelo cudnn64_9.dll — ~960 MB juntos)
    "cudnn_adv",              # advanced ops
    "cudnn_cnn",              # CNN kernels
    "cudnn_engines_precompiled",  # 490 MB — maior single file
    "cudnn_engines_runtime_compiled",
    "cudnn_graph",
    "cudnn_heuristic",
    "cudnn_ops",              # CANARIO do cuda_pack instalado (120 MB)
    # Outras dlopen
    "caffe2_nvrtc",           # CTranslate2 NVRTC wrapper
    "cufftw",                 # FFTW wrapper (cufft puro e obrigatoria)
    "curand",                 # CUDA RNG (CPU RNG usa CPU; nao obrigatorio)
    "cusolvermg",             # multi-GPU solver (laptop so tem 1 GPU)
    "nvrtc-builtins",         # NVRTC builtins
    "nvrtc64_120_0.alt",      # NVRTC alt compiler (prefixo mais especifico primeiro)
    "nvrtc64_120_0",          # NVRTC compiler (~82 MB, torch.compile only)
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
