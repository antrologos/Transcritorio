"""Toy edges: runtime.cuda_libs_present() stress.

TIER B5 do plano. Testa comportamento em situacoes nao-normais:
- torch.__file__ aponta para symlink quebrado
- torch.__file__ aponta para path inexistente
- Erros de permissao/FS ao probar lib_dir
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import runtime


def _reset_cache() -> None:
    runtime._cuda_libs_detected = None


def _clear_fake_torch() -> None:
    sys.modules.pop("torch", None)


def test_torch_file_points_to_nonexistent_path() -> None:
    """Se torch.__file__ aponta pra um path que nao existe, cuda_libs_present
    deve retornar False (nao crashar com FileNotFoundError)."""
    _reset_cache()
    fake = types.ModuleType("torch")
    fake.__file__ = r"/path/que/nao/existe/torch/__init__.py"
    sys.modules["torch"] = fake
    try:
        result = runtime.cuda_libs_present()
        assert result is False, f"esperado False, got {result}"
        print("PASS: torch.__file__ inexistente -> False (sem crash)")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_broken_symlink_torch_cuda() -> None:
    """torch_cuda e um symlink para um arquivo que nao existe — cuda_libs_present
    deve retornar False (Path.exists() retorna False para symlink quebrado)."""
    _reset_cache()
    if sys.platform == "win32":
        print("SKIP: symlinks em Windows precisam de privilegio especial")
        return
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        # Symlink quebrado pra /nonexistent
        if sys.platform == "darwin":
            target_name = "libtorch_cuda.dylib"
        else:
            target_name = "libtorch_cuda.so"
        try:
            os.symlink("/path/que/nao/existe", libdir / target_name)
        except OSError:
            print(f"SKIP: symlink nao suportado neste FS")
            return
        fake = types.ModuleType("torch")
        fake.__file__ = str(libdir.parent / "__init__.py")
        sys.modules["torch"] = fake
        try:
            result = runtime.cuda_libs_present()
            assert result is False, f"esperado False (symlink quebrado), got {result}"
            print("PASS: symlink quebrado torch_cuda -> False")
        finally:
            _clear_fake_torch()
            _reset_cache()


def test_lib_dir_is_file_not_dir() -> None:
    """Se torch/lib existe mas e um arquivo, nao um diretorio —
    caso patologico de torch corrompido."""
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        torch_dir = Path(tmp) / "torch"
        torch_dir.mkdir()
        # 'lib' e um arquivo, nao dir
        (torch_dir / "lib").write_bytes(b"nao sou um diretorio")
        fake = types.ModuleType("torch")
        fake.__file__ = str(torch_dir / "__init__.py")
        sys.modules["torch"] = fake
        try:
            result = runtime.cuda_libs_present()
            # Path("torch/lib/libtorch_cuda.so").exists() retorna False
            # porque lib e arquivo, nao dir. Nao crasha.
            assert result is False, f"esperado False, got {result}"
            print("PASS: torch/lib e arquivo (nao dir) -> False (sem crash)")
        finally:
            _clear_fake_torch()
            _reset_cache()


def test_lib_dir_missing_entirely() -> None:
    """torch existe mas torch/lib nao — raro, mas acontece em algumas
    distribuicoes minimas."""
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        torch_dir = Path(tmp) / "torch"
        torch_dir.mkdir()
        # Nao cria lib/ — so o __init__
        fake = types.ModuleType("torch")
        fake.__file__ = str(torch_dir / "__init__.py")
        sys.modules["torch"] = fake
        try:
            result = runtime.cuda_libs_present()
            assert result is False, f"esperado False, got {result}"
            print("PASS: torch/lib inexistente -> False (sem crash)")
        finally:
            _clear_fake_torch()
            _reset_cache()


if __name__ == "__main__":
    test_torch_file_points_to_nonexistent_path()
    test_broken_symlink_torch_cuda()
    test_lib_dir_is_file_not_dir()
    test_lib_dir_missing_entirely()
    print()
    print("PASS: toy_cuda_libs_edges")
