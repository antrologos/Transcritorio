"""Toy edges: runtime.detect_device() + resolve_device() stress.

TIER B6 do plano. Testa comportamento com torch em estado inusual:
- torch presente mas sem atributo .cuda (hipotetico, nao deve passar import)
- torch.cuda.is_available levanta (nao apenas retorna False)
- torch.backends ausente (torch cpu-only muito antigo)
- torch.backends.mps ausente
- Chamadas concorrentes (invariantes do cache global)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import runtime


def _reset_cache() -> None:
    runtime._detected_device = None


def _clear_fake_torch() -> None:
    sys.modules.pop("torch", None)


def test_torch_cuda_is_available_raises() -> None:
    """Se torch.cuda.is_available() levanta (nao retorna False),
    detect_device deve cair em cpu via except."""
    _reset_cache()
    fake = types.ModuleType("torch")

    def _boom_cuda():
        raise RuntimeError("driver corrupto")

    fake.cuda = types.SimpleNamespace(is_available=_boom_cuda)
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: False)
    )
    sys.modules["torch"] = fake
    try:
        result = runtime.detect_device()
        assert result == "cpu", f"esperado cpu (torch.cuda explode), got {result}"
        print("PASS: torch.cuda.is_available() raise -> cpu")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_torch_backends_missing_entirely() -> None:
    """Torch CPU-only pode nao ter o attr .backends. Nao crashar."""
    _reset_cache()
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: False)
    # Sem fake.backends
    sys.modules["torch"] = fake
    try:
        result = runtime.detect_device()
        assert result == "cpu", f"esperado cpu (sem backends), got {result}"
        print("PASS: torch sem .backends -> cpu (sem AttributeError)")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_torch_backends_without_mps() -> None:
    """Torch com backends mas sem .mps (pytorch pre-1.12)."""
    _reset_cache()
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: False)
    fake.backends = types.SimpleNamespace()  # vazio — sem mps
    sys.modules["torch"] = fake
    try:
        result = runtime.detect_device()
        assert result == "cpu", f"esperado cpu (backends sem mps), got {result}"
        print("PASS: torch.backends sem .mps -> cpu (hasattr protege)")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_resolve_device_invalid_config_value() -> None:
    """resolve_device() com valor nonstandard em config: strings
    'gpu', 'auto', None, maiuscula. Codigo deve ser defensivo."""
    _reset_cache()
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: False)
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: False)
    )
    sys.modules["torch"] = fake
    try:
        # None -> default 'cuda' -> cai em cpu
        d, fb = runtime.resolve_device(None)
        assert d == "cpu" and fb is True, f"None: esperado ('cpu', True), got ({d!r}, {fb})"
        # Uppercase 'CUDA' -> lower() -> cuda -> cai em cpu
        runtime._detected_device = None
        d, fb = runtime.resolve_device("CUDA")
        assert d == "cpu" and fb is True, f"'CUDA': esperado ('cpu', True), got ({d!r}, {fb})"
        # 'gpu' nao e cpu, entao entra no branch cuda, cai em cpu
        runtime._detected_device = None
        d, fb = runtime.resolve_device("gpu")
        assert d == "cpu" and fb is True, f"'gpu': esperado ('cpu', True), got ({d!r}, {fb})"
        print("PASS: resolve_device defensivo contra None/maiuscula/valor desconhecido")
    finally:
        _clear_fake_torch()
        _reset_cache()


def test_detect_device_cache_consistent_across_calls() -> None:
    """Mesmo que torch mude entre chamadas, o cache preserva o primeiro
    resultado (trade-off: consistencia vs dinamismo). Valida invariante."""
    _reset_cache()
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: True)
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: False)
    )
    sys.modules["torch"] = fake
    # 2026-04-23: no Windows detect_device exige cuda_libs_present tambem.
    runtime._cuda_libs_detected = True
    try:
        first = runtime.detect_device()
        assert first == "cuda"
        # Muda torch: agora cuda nao disponivel
        fake.cuda.is_available = lambda: False
        # Segunda chamada preserva cache
        second = runtime.detect_device()
        assert second == "cuda", f"cache quebrado: first={first}, second={second}"
        print("PASS: detect_device cache preserva resultado apos mudanca de torch")
    finally:
        _clear_fake_torch()
        _reset_cache()


if __name__ == "__main__":
    test_torch_cuda_is_available_raises()
    test_torch_backends_missing_entirely()
    test_torch_backends_without_mps()
    test_resolve_device_invalid_config_value()
    test_detect_device_cache_consistent_across_calls()
    print()
    print("PASS: toy_device_detect_edges")
