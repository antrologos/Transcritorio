"""Toy test: packaging.bundle_filter.should_exclude_entry()

Valida:
- Build artifacts (.lib, .h, etc) sao excluidos sempre
- PySide6 dev exes (designer.exe, linguist.exe) sao excluidos
- Qt plugins fora da whitelist sao excluidos
- Variant 'full': exclude list minima (cusolverMg, cufft, etc — como hoje)
- Variant 'cpu': exclude list estendida (torch_cuda, cudnn, cublas...)
- Nomes sem match passam
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))

from bundle_filter import should_exclude_entry  # noqa: E402


def test_build_artifacts_always_excluded() -> None:
    for name in ["foo.lib", "header.h", "header.hpp", "stub.pyi", "x.cmake"]:
        for variant in ("full", "cpu"):
            assert should_exclude_entry(name, variant), f"{name} ({variant}) deveria ser excluido"
    print("PASS: build artifacts (.lib, .h, .pyi, .cmake) excluidos em ambos variants")


def test_pyside6_dev_exes_always_excluded() -> None:
    for name in ["designer.exe", "linguist.exe", "qmlls.exe", "qmlformat.exe"]:
        for variant in ("full", "cpu"):
            assert should_exclude_entry(name, variant), f"{name} ({variant}) deveria ser excluido"
    print("PASS: PySide6 dev exes sempre excluidos")


def test_full_variant_preserves_all_cuda() -> None:
    """Variant 'full' nao exclui NENHUMA DLL CUDA (MINIMAL vazia em 2026-04-23).
    Necessario para split_bundle no CI conseguir rotear as 14 lazy-load
    para cuda_pack — se MINIMAL strippar alguma delas em full, o bundle
    da PyInstaller ja chega ao split_bundle sem elas e o cuda_pack fica
    incompleto."""
    # Todas as 11 IAT obrigatorias preservadas
    iat_mandatory = [
        "torch_cuda.dll", "cudnn64_9.dll", "cublas64_12.dll", "cublasLt64_12.dll",
        "cufft64_11.dll", "cusparse64_12.dll", "nvJitLink_120_0.dll",
        "cupti64_2025.1.1.dll", "cudart64_12.dll", "c10_cuda.dll", "cusolver64_11.dll",
    ]
    # Todas as 14 lazy-load ALSO preservadas em 'full' (strippadas so em 'cpu')
    lazy = [
        "cudnn_adv64_9.dll", "cudnn_cnn64_9.dll",
        "cudnn_engines_precompiled64_9.dll", "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn_graph64_9.dll", "cudnn_heuristic64_9.dll", "cudnn_ops64_9.dll",
        "caffe2_nvrtc.dll", "cufftw64_11.dll", "curand64_10.dll", "cusolverMg64_11.dll",
        "nvrtc-builtins64_128.dll", "nvrtc64_120_0.alt.dll", "nvrtc64_120_0.dll",
    ]
    for name in iat_mandatory + lazy:
        assert not should_exclude_entry(name, "full"), f"{name} NAO deveria ser excluido em 'full'"
    print("PASS: variant 'full' preserva TODAS as 25 DLLs CUDA (11 IAT + 14 lazy)")


def test_cpu_variant_strips_only_lazyload_cuda() -> None:
    """Variant 'cpu' exclui APENAS as 14 DLLs CUDA que carregam sob demanda
    via dlopen (nao IAT). Mapeado empiricamente via pefile em 2026-04-23.
    Sem elas, `import torch` ainda funciona — falhas aparecem so quando o
    codigo chama cudnn conv/lstm ou nvrtc/curand. O cuda_pack as traz de
    volta quando o usuario opta por aceleracao GPU."""
    for name in [
        # cuDNN engines (carregados pelo cudnn64_9.dll sob demanda)
        "cudnn_adv64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_engines_precompiled64_9.dll",
        "cudnn_engines_runtime_compiled64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn_heuristic64_9.dll",
        "cudnn_ops64_9.dll",
        # Outras dlopen
        "caffe2_nvrtc.dll",
        "cufftw64_11.dll",
        "curand64_10.dll",
        "cusolverMg64_11.dll",
        "nvrtc-builtins64_128.dll",
        "nvrtc64_120_0.alt.dll",
        "nvrtc64_120_0.dll",
    ]:
        assert should_exclude_entry(name, "cpu"), f"{name} deveria ser excluido em 'cpu'"
    print("PASS: variant 'cpu' exclui as 14 DLLs CUDA lazy-load (cuda_pack fornece)")


def test_cpu_variant_keeps_torch_cpu_and_mandatory_cuda() -> None:
    """Variant 'cpu' mantem torch_cpu/torch_python/c10 E as 11 DLLs CUDA
    obrigatorias (IAT do torch core). Sem elas shm.dll/torch_cpu.dll nao
    carregam no Windows cu128 — bundle inteiro quebra no import."""
    # torch CPU essentials
    cpu_essentials = ["torch_cpu.dll", "torch_python.dll", "c10.dll", "asmjit.dll"]
    # 11 CUDA DLLs obrigatorias (IAT) que ficam no bundle base mesmo em cpu
    mandatory_cuda = [
        "torch_cuda.dll",
        "c10_cuda.dll",
        "cudart64_12.dll",
        "cupti64_2025.1.1.dll",
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_9.dll",
        "cufft64_11.dll",
        "cusolver64_11.dll",
        "cusparse64_12.dll",
        "nvJitLink_120_0.dll",
    ]
    for name in cpu_essentials + mandatory_cuda:
        assert not should_exclude_entry(name, "cpu"), f"{name} NAO deveria ser excluido em 'cpu'"
    print("PASS: variant 'cpu' mantem torch_cpu + 11 CUDA obrigatorias (IAT do torch core)")


def test_qt_plugins_filter() -> None:
    """Qt plugins: so whitelist passa; 'platforms', 'styles', 'multimedia' mantidos."""
    # Whitelist — passa
    for name in [
        "PySide6/plugins/platforms/qwindows.dll",
        "PySide6/plugins/styles/fusion.dll",
        "PySide6/plugins/imageformats/qjpeg.dll",
        "PySide6/plugins/multimedia/ffmpegmediaplugin.dll",
    ]:
        for variant in ("full", "cpu"):
            assert not should_exclude_entry(name, variant), f"{name} ({variant}) NAO deveria ser excluido"
    # Nao-whitelist — exclui
    for name in [
        "PySide6/plugins/sqldrivers/qsqlite.dll",
        "PySide6/plugins/bearer/qgenericbearer.dll",
        "PySide6/plugins/sensors/x.dll",
    ]:
        for variant in ("full", "cpu"):
            assert should_exclude_entry(name, variant), f"{name} ({variant}) deveria ser excluido"
    print("PASS: Qt plugins whitelist (platforms, styles, multimedia) preservada")


def test_unrelated_files_pass() -> None:
    """Arquivos normais (.dll random, .py, .json) nao devem ser excluidos."""
    for name in ["app.dll", "config.json", "module.py", "asset.png", "Transcritorio.exe"]:
        for variant in ("full", "cpu"):
            assert not should_exclude_entry(name, variant), f"{name} ({variant}) NAO deveria ser excluido"
    print("PASS: arquivos nao-CUDA/nao-dev passam em ambos variants")


if __name__ == "__main__":
    test_build_artifacts_always_excluded()
    test_pyside6_dev_exes_always_excluded()
    test_full_variant_preserves_all_cuda()
    test_cpu_variant_strips_only_lazyload_cuda()
    test_cpu_variant_keeps_torch_cpu_and_mandatory_cuda()
    test_qt_plugins_filter()
    test_unrelated_files_pass()
    print()
    print("PASS: toy_bundle_filter")
