"""Toy test: runtime.has_nvidia_gpu()

Usa mocks em subprocess.run pra simular presenca/ausencia de nvidia-smi.
Valida:
- Retorna True quando nvidia-smi exit 0
- Retorna False quando nvidia-smi exit != 0
- Retorna False quando nvidia-smi nao existe (FileNotFoundError)
- Retorna False quando subprocess.TimeoutExpired
- Cache: 2a chamada nao re-executa
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import runtime


def _reset_cache() -> None:
    runtime._nvidia_gpu_detected = None


def _patch_subprocess(run_fn) -> None:
    """Temporariamente substitui subprocess.run."""
    subprocess.run = run_fn


def _restore_subprocess(original) -> None:
    subprocess.run = original


def test_nvidia_present() -> None:
    _reset_cache()
    original = subprocess.run

    def fake_run(*args, **kwargs):
        r = types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return r

    _patch_subprocess(fake_run)
    try:
        assert runtime.has_nvidia_gpu() is True
        print("PASS has_nvidia_gpu: True quando nvidia-smi exit 0")
    finally:
        _restore_subprocess(original)
        _reset_cache()


def test_nvidia_absent_nonzero_exit() -> None:
    _reset_cache()
    original = subprocess.run

    def fake_run(*args, **kwargs):
        r = types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        return r

    _patch_subprocess(fake_run)
    try:
        assert runtime.has_nvidia_gpu() is False
        print("PASS has_nvidia_gpu: False quando nvidia-smi exit 1")
    finally:
        _restore_subprocess(original)
        _reset_cache()


def test_nvidia_smi_not_found() -> None:
    _reset_cache()
    original = subprocess.run

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi: no such file")

    _patch_subprocess(fake_run)
    try:
        assert runtime.has_nvidia_gpu() is False
        print("PASS has_nvidia_gpu: False quando nvidia-smi nao existe")
    finally:
        _restore_subprocess(original)
        _reset_cache()


def test_timeout_expired() -> None:
    _reset_cache()
    original = subprocess.run

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5)

    _patch_subprocess(fake_run)
    try:
        assert runtime.has_nvidia_gpu() is False
        print("PASS has_nvidia_gpu: False quando timeout")
    finally:
        _restore_subprocess(original)
        _reset_cache()


def test_cache() -> None:
    _reset_cache()
    original = subprocess.run
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(1)
        r = types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return r

    _patch_subprocess(fake_run)
    try:
        runtime.has_nvidia_gpu()
        runtime.has_nvidia_gpu()
        runtime.has_nvidia_gpu()
        assert len(calls) == 1, f"cacheamento quebrado: {len(calls)} chamadas a subprocess.run"
        print("PASS has_nvidia_gpu: cache (so 1 call a subprocess.run)")
    finally:
        _restore_subprocess(original)
        _reset_cache()


if __name__ == "__main__":
    test_nvidia_present()
    test_nvidia_absent_nonzero_exit()
    test_nvidia_smi_not_found()
    test_timeout_expired()
    test_cache()
    print()
    print("PASS: toy_has_nvidia_gpu")
