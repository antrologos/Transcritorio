"""Toy test: detect_device() + resolve_device() com suporte a MPS.

Valida:
- detect_device() retorna "cuda" quando torch.cuda.is_available()
- detect_device() retorna "mps" quando torch.backends.mps.is_available()
- detect_device() retorna "cpu" caso contrario
- resolve_device("cpu") retorna ("cpu", False) independente do detectado
- resolve_device("cuda") retorna ("cuda", False) se detectado cuda
- resolve_device("cuda") retorna ("cpu", True) se detectado mps OU cpu
- detect_device e cacheada em _detected_device
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import runtime


def _reset_cache() -> None:
    runtime._detected_device = None
    runtime._cuda_libs_detected = None


def _install_fake_torch(cuda: bool, mps: bool, cuda_libs: bool | None = None) -> None:
    """Monkey-patch sys.modules com um fake torch.

    cuda_libs: 2026-04-23 — em Windows, detect_device() exige cuda_libs_present()
    ALEM de cuda.is_available() para escolher cuda. Default segue cuda.
    """
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps)
    )
    sys.modules["torch"] = fake
    # Seta cache de cuda_libs_present diretamente — simula cuda_pack instalado.
    runtime._cuda_libs_detected = cuda if cuda_libs is None else cuda_libs


def _clear_fake_torch() -> None:
    sys.modules.pop("torch", None)


def test_detect_cuda() -> None:
    _reset_cache()
    _install_fake_torch(cuda=True, mps=False)
    try:
        assert runtime.detect_device() == "cuda"
        print("PASS detect_device: cuda")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_detect_mps() -> None:
    _reset_cache()
    _install_fake_torch(cuda=False, mps=True)
    try:
        assert runtime.detect_device() == "mps"
        print("PASS detect_device: mps (Apple Silicon)")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_detect_cpu() -> None:
    _reset_cache()
    _install_fake_torch(cuda=False, mps=False)
    try:
        assert runtime.detect_device() == "cpu"
        print("PASS detect_device: cpu")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_detect_cpu_when_torch_broken() -> None:
    """Se torch nao importa/explode, cair em cpu."""
    _reset_cache()
    fake = types.ModuleType("torch")
    def _bad():
        raise RuntimeError("broken")
    fake.cuda = types.SimpleNamespace(is_available=_bad)
    sys.modules["torch"] = fake
    try:
        assert runtime.detect_device() == "cpu"
        print("PASS detect_device: fallback cpu quando torch quebra")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_cache() -> None:
    """Segunda chamada nao deve reavaliar torch."""
    _reset_cache()
    calls = []
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: (calls.append(1) or True))
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: False)
    )
    sys.modules["torch"] = fake
    try:
        runtime.detect_device()
        runtime.detect_device()
        runtime.detect_device()
        assert len(calls) == 1, f"detect_device nao cacheado, {len(calls)} chamadas"
        print("PASS detect_device: cacheia resultado")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_resolve_device_cpu_forced() -> None:
    _reset_cache()
    _install_fake_torch(cuda=True, mps=False)
    try:
        assert runtime.resolve_device("cpu") == ("cpu", False)
        print("PASS resolve_device: cpu forcado ignora cuda detectado")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_resolve_device_cuda_when_available() -> None:
    _reset_cache()
    _install_fake_torch(cuda=True, mps=False)
    try:
        assert runtime.resolve_device("cuda") == ("cuda", False)
        print("PASS resolve_device: cuda quando disponivel")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_resolve_device_mps_falls_back_to_cpu() -> None:
    """MPS nao e aceito por CT2 (faster-whisper). Fallback pra CPU."""
    _reset_cache()
    _install_fake_torch(cuda=False, mps=True)
    try:
        device, fell_back = runtime.resolve_device("cuda")
        assert device == "cpu"
        assert fell_back is True
        print("PASS resolve_device: mps -> cpu com fell_back=True (CT2 incompat)")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_resolve_device_no_accel() -> None:
    _reset_cache()
    _install_fake_torch(cuda=False, mps=False)
    try:
        device, fell_back = runtime.resolve_device("cuda")
        assert device == "cpu"
        assert fell_back is True
        print("PASS resolve_device: cpu-only -> fell_back=True")
    finally:
        _clear_fake_torch()
        _reset_cache()


if __name__ == "__main__":
    test_detect_cuda()
    test_detect_mps()
    test_detect_cpu()
    test_detect_cpu_when_torch_broken()
    test_cache()
    test_resolve_device_cpu_forced()
    test_resolve_device_cuda_when_available()
    test_resolve_device_mps_falls_back_to_cpu()
    test_resolve_device_no_accel()
    print()
    print("PASS: toy_device_select")
