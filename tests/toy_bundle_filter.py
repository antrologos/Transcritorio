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


def test_full_variant_minimal_cuda_excludes() -> None:
    """Variant 'full' mantem comportamento atual — exclui so cuda DLLs
    redundantes/desnecessarias para inference (cusolverMg, cufft, etc)."""
    # Excluido mesmo em 'full'
    for name in ["cusolverMg64_11.dll", "cufft64_11.dll", "curand64_10.dll", "nvJitLink_120_0.dll", "nvrtc64_120_0.alt.dll"]:
        assert should_exclude_entry(name, "full"), f"{name} deveria ser excluido em 'full'"
    # NAO excluido em 'full' (mantem CUDA completa)
    for name in ["torch_cuda.dll", "cudnn64_9.dll", "cublas64_12.dll", "cublasLt64_12.dll"]:
        assert not should_exclude_entry(name, "full"), f"{name} NAO deveria ser excluido em 'full'"
    print("PASS: variant 'full' preserva torch_cuda + cudnn + cublas (comportamento atual)")


def test_cpu_variant_strips_cuda_heavyweights() -> None:
    """Variant 'cpu' adiciona torch_cuda + cudnn + cublas aos excludes —
    reduz bundle em ~3 GB."""
    for name in [
        "torch_cuda.dll",
        "torch_cuda_linalg.dll",
        "cudnn64_9.dll",
        "cudnn_adv64_9.dll",
        "cudnn_ops64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_heuristic64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn_engines_precompiled64_9.dll",
        "cudnn_engines_runtime_compiled64_9.dll",
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "caffe2_nvrtc.dll",
        "c10_cuda.dll",
    ]:
        assert should_exclude_entry(name, "cpu"), f"{name} deveria ser excluido em 'cpu'"
    print("PASS: variant 'cpu' exclui torch_cuda + 8 cudnn* + cublas* + cublasLt* + caffe2_nvrtc + c10_cuda")


def test_cpu_variant_keeps_torch_cpu_essentials() -> None:
    """Variant 'cpu' NAO deve excluir torch_cpu, torch_python, c10 (sem _cuda)."""
    for name in ["torch_cpu.dll", "torch_python.dll", "c10.dll", "asmjit.dll"]:
        assert not should_exclude_entry(name, "cpu"), f"{name} NAO deveria ser excluido em 'cpu'"
    print("PASS: variant 'cpu' mantem torch_cpu + torch_python + c10 (sem _cuda)")


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
    test_full_variant_minimal_cuda_excludes()
    test_cpu_variant_strips_cuda_heavyweights()
    test_cpu_variant_keeps_torch_cpu_essentials()
    test_qt_plugins_filter()
    test_unrelated_files_pass()
    print()
    print("PASS: toy_bundle_filter")
