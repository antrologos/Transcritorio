"""Toy test: runtime.cuda_libs_present()

Valida:
- Retorna True quando cudnn_ops64_9.dll (canario do cuda_pack) esta presente
  no torch.lib/ — Windows (2026-04-23: canario trocado de torch_cuda para
  cudnn_ops pois torch_cuda fica no bundle base por ser IAT obrigatoria).
  Linux usa libcudnn_ops.so e Mac libcudnn_ops.dylib para consistencia.
- Retorna False quando a lib esta ausente (bundle base sem cuda_pack)
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


def test_present_when_cudnn_ops_exists() -> None:
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        # Canario: cudnn_ops e uma das 14 DLLs lazy-load que ficam no
        # cuda_pack; presenca indica cuda_pack instalado.
        if sys.platform == "win32":
            (libdir / "cudnn_ops64_9.dll").write_bytes(b"fake")
        elif sys.platform == "darwin":
            (libdir / "libcudnn_ops.dylib").write_bytes(b"fake")
        else:
            (libdir / "libcudnn_ops.so").write_bytes(b"fake")
        _install_fake_torch_with_libdir(libdir)
        try:
            assert runtime.cuda_libs_present() is True
            print("PASS cuda_libs_present: True quando cudnn_ops canario existe")
        finally:
            _clear_fake_torch()
            _reset_cache()


def test_absent_when_lib_missing() -> None:
    _reset_cache()
    with tempfile.TemporaryDirectory() as tmp:
        libdir = Path(tmp) / "torch" / "lib"
        libdir.mkdir(parents=True)
        # Bundle base: tem torch_cpu e as 11 CUDA IAT obrigatorias, mas NAO
        # tem cudnn_ops (canario do cuda_pack). Deve retornar False.
        if sys.platform == "win32":
            (libdir / "torch_cpu.dll").write_bytes(b"fake")
            (libdir / "torch_cuda.dll").write_bytes(b"fake")  # IAT obrig, presente
            (libdir / "cudnn64_9.dll").write_bytes(b"fake")   # IAT obrig, loader
        else:
            (libdir / "libtorch_cpu.so").write_bytes(b"fake")
            (libdir / "libtorch_cuda.so").write_bytes(b"fake")
        _install_fake_torch_with_libdir(libdir)
        try:
            assert runtime.cuda_libs_present() is False
            print("PASS cuda_libs_present: False quando cudnn_ops ausente (bundle base, sem cuda_pack)")
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
            (libdir / "cudnn_ops64_9.dll").write_bytes(b"fake")
        elif sys.platform == "darwin":
            (libdir / "libcudnn_ops.dylib").write_bytes(b"fake")
        else:
            (libdir / "libcudnn_ops.so").write_bytes(b"fake")
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
    test_present_when_cudnn_ops_exists()
    test_absent_when_lib_missing()
    test_absent_when_torch_not_importable()
    test_absent_when_torch_import_fails()
    test_cache()
    print()
    print("PASS: toy_cuda_libs_present")
