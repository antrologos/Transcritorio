"""Toy edges: bundle_filter.should_exclude_entry em Linux/Mac.

Stress-test TIER A1 do plano de estressing. O filtro atual tem:

    if basename.endswith(".dll"):
        for cuda_prefix in _cuda_excludes_for_variant(variant):
            if basename.startswith(cuda_prefix.lower()):
                return True

Isso SO funciona pra Windows (.dll). Em Linux (.so) e Mac (.dylib) o
branch nunca dispara — CUDA libs nao sao strippadas pro variant='cpu'.

Em producao hoje isso e latente (CI usa torch CPU, nao tem CUDA libs
pra strippar). Mas se alguem builder Linux com torch GPU + variant=cpu,
o resultado nao seria menor — silencioso bug.

Tests deste arquivo FALHAM antes do fix em bundle_filter.py, e passam
depois. E o padrao: red first, green after fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))

from bundle_filter import should_exclude_entry  # noqa: E402


def test_linux_so_cuda_stripped_cpu_variant() -> None:
    """Linux: libtorch_cuda.so deve ser excluido em variant='cpu'."""
    cases = [
        ("libtorch_cuda.so", True),
        ("libtorch_cuda_linalg.so", True),
        ("libcudnn.so.9", True),
        ("libcudnn_ops.so.9", True),
        ("libcublas.so.12", True),
        ("libcublasLt.so.12", True),
        ("libc10_cuda.so", True),
        ("libcaffe2_nvrtc.so", True),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "cpu")
        assert got == expected, f"{name} (cpu) esperado {expected}, got {got}"
    print(f"PASS linux .so: {len(cases)} CUDA libs excluidas em variant='cpu'")


def test_mac_dylib_cuda_stripped_cpu_variant() -> None:
    """Mac: libtorch_cuda.dylib (teorico; Mac nao tem CUDA em producao)."""
    cases = [
        ("libtorch_cuda.dylib", True),
        ("libcudnn.dylib", True),
        ("libcublas.12.dylib", True),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "cpu")
        assert got == expected, f"{name} (cpu) esperado {expected}, got {got}"
    print(f"PASS mac .dylib: {len(cases)} CUDA libs excluidas em variant='cpu'")


def test_linux_torch_cpu_preserved_cpu_variant() -> None:
    """variant='cpu' NAO deve excluir torch_cpu, c10 (sem _cuda), torch_python."""
    cases = [
        ("libtorch_cpu.so", False),
        ("libtorch_python.so", False),
        ("libc10.so", False),  # c10 sem sufixo _cuda
        ("libasmjit.so", False),
        ("libgomp.so.1", False),
        ("libiomp5.so", False),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "cpu")
        assert got == expected, f"{name} (cpu) esperado {expected}, got {got}"
    print(f"PASS linux .so: {len(cases)} libs essenciais preservadas em variant='cpu'")


def test_full_variant_preserves_linux_cuda() -> None:
    """variant='full' NAO deve excluir libtorch_cuda.so (CUDA preservada)."""
    cases = [
        ("libtorch_cuda.so", False),
        ("libcudnn.so.9", False),
        ("libcublas.so.12", False),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "full")
        assert got == expected, f"{name} (full) esperado {expected}, got {got}"
    print(f"PASS variant='full' preserva CUDA .so (como preserva .dll)")


def test_versioned_so_suffix_handled() -> None:
    """Linux versiona .so com numeros: .so, .so.9, .so.12, .so.9.3.0.
    O filtro deve reconhecer todos."""
    cases = [
        ("libtorch_cuda.so", True),
        ("libtorch_cuda.so.1", True),
        ("libtorch_cuda.so.2.0", True),
        ("libcudnn_ops.so.9.3.0", True),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "cpu")
        assert got == expected, f"{name} (cpu) esperado {expected}, got {got}"
    print(f"PASS .so versionado (so.N, so.N.M) reconhecido como CUDA")


def test_windows_dll_still_works_after_fix() -> None:
    """O fix nao pode quebrar Windows. .dll continua sendo tratado."""
    cases = [
        ("torch_cuda.dll", "cpu", True),
        ("torch_cpu.dll", "cpu", False),
        ("cudnn64_9.dll", "cpu", True),
        ("torch_cuda.dll", "full", False),
    ]
    for name, variant, expected in cases:
        got = should_exclude_entry(name, variant)
        assert got == expected, f"{name} ({variant}) esperado {expected}, got {got}"
    print(f"PASS Windows .dll continua consistente pos-fix")


def test_non_shared_lib_files_not_affected() -> None:
    """Arquivos .py, .json, .txt com 'cuda' no nome NAO devem ser excluidos
    (o filtro so aplica a shared libs)."""
    cases = [
        ("cuda_helper.py", False),
        ("torch_cuda_config.json", False),
        ("cuda_readme.txt", False),
        ("cudnn_notes.md", False),
    ]
    for name, expected in cases:
        got = should_exclude_entry(name, "cpu")
        assert got == expected, f"{name} esperado {expected}, got {got}"
    print(f"PASS arquivos nao-shared-lib com 'cuda' no nome nao sao excluidos")


if __name__ == "__main__":
    test_linux_so_cuda_stripped_cpu_variant()
    test_mac_dylib_cuda_stripped_cpu_variant()
    test_linux_torch_cpu_preserved_cpu_variant()
    test_full_variant_preserves_linux_cuda()
    test_versioned_so_suffix_handled()
    test_windows_dll_still_works_after_fix()
    test_non_shared_lib_files_not_affected()
    print()
    print("PASS: toy_bundle_filter_edges")
