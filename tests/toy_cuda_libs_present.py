"""Toy test: runtime.cuda_libs_present()

Valida:
- Retorna True quando torch_cuda.dll (Windows) / libtorch_cuda.so (Linux) /
  libtorch_cuda.dylib (Mac) existe em torch.lib/
- Retorna False quando a lib esta ausente (simulando bundle CPU-only)
- Retorna False se torch nao importa (sem crash)
- Cacheia o resultado (chamadas subsequentes nao reavaliam)
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
    # cache interno de cuda_libs_present
    if hasattr(runtime, "_cuda_libs_detected"):
        runtime._cuda_libs_detected = None


def _install_fake_torch_with_libdir(libdir: Path) -> None:
    """Instala fake torch com __file__ apontando para libdir.parent/__init__.py."""
    fake = types.ModuleType("torch")
    fake.__file__ = str(libdir.parent / "__init__.py")
    sys.modules["torch"] = fake


def _clear_fake_torch() -> None:
    sys.modules.pop("torch", None)


def test_present_when_torch_cuda_dll_exists() -> None:
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        # Cria um stub de torch_cuda com nome do OS corrente
        if sys.platform == "win32":
            (libdir / "torch_cuda.dll").write_bytes(b"fake")
        elif sys.platform == "darwin":
            (libdir / "libtorch_cuda.dylib").write_bytes(b"fake")
        else:
            (libdir / "libtorch_cuda.so").write_bytes(b"fake")
        _install_fake_torch_with_libdir(libdir)
        try:
            assert runtime.cuda_libs_present() is True
            print("PASS cuda_libs_present: True quando torch_cuda.* existe")
        finally:
            _clear_fake_torch()
            _reset_cache()


def test_absent_when_lib_missing() -> None:
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        # Cria so o torch_cpu, sem torch_cuda
        if sys.platform == "win32":
            (libdir / "torch_cpu.dll").write_bytes(b"fake")
        else:
            (libdir / "libtorch_cpu.so").write_bytes(b"fake")
        _install_fake_torch_with_libdir(libdir)
        try:
            assert runtime.cuda_libs_present() is False
            print("PASS cuda_libs_present: False quando torch_cuda.* ausente (bundle CPU)")
        finally:
            _clear_fake_torch()
            _reset_cache()


def test_absent_when_torch_not_importable() -> None:
    _reset_cache()
    _clear_fake_torch()
    # Instala um fake torch que explode ao ser usado
    fake = types.ModuleType("torch")
    # Sem __file__ — nao podemos determinar libdir
    sys.modules["torch"] = fake
    try:
        assert runtime.cuda_libs_present() is False
        print("PASS cuda_libs_present: False quando torch sem __file__")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_absent_when_torch_import_fails() -> None:
    _reset_cache()
    _clear_fake_torch()
    # Bloqueia import de torch forcando ImportError
    orig_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("simulated: torch unavailable")
        return orig_import(name, *args, **kwargs)

    import builtins
    builtins.__import__ = blocking_import
    try:
        assert runtime.cuda_libs_present() is False
        print("PASS cuda_libs_present: False quando torch import levanta")
    finally:
        builtins.__import__ = orig_import
        _reset_cache()


def test_cache() -> None:
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        if sys.platform == "win32":
            (libdir / "torch_cuda.dll").write_bytes(b"fake")
        elif sys.platform == "darwin":
            (libdir / "libtorch_cuda.dylib").write_bytes(b"fake")
        else:
            (libdir / "libtorch_cuda.so").write_bytes(b"fake")
        _install_fake_torch_with_libdir(libdir)
        try:
            first = runtime.cuda_libs_present()
            # Apaga o lib: se nao cacheou, a 2a chamada seria False
            for f in libdir.iterdir():
                f.unlink()
            second = runtime.cuda_libs_present()
            assert first == second == True, f"cache quebrado: first={first}, second={second}"
            print("PASS cuda_libs_present: cacheia resultado (nao re-checa FS)")
        finally:
            _clear_fake_torch()
            _reset_cache()


if __name__ == "__main__":
    test_present_when_torch_cuda_dll_exists()
    test_absent_when_lib_missing()
    test_absent_when_torch_not_importable()
    test_absent_when_torch_import_fails()
    test_cache()
    print()
    print("PASS: toy_cuda_libs_present")
