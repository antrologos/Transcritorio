"""Toy test: runtime.describe_backend() returns a user-friendly backend
label based on detected device and mlx-whisper availability.

Shape expected in the GUI header:
  CUDA (NVIDIA)      — when CUDA detected
  MLX (Metal)        — when MPS detected AND mlx_whisper importable
  MPS (sem MLX)      — when MPS detected but mlx_whisper missing
                       (user-facing warning so they see the fallback)
  CPU                — default
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import runtime


def _install_fake_mlx() -> None:
    sys.modules["mlx_whisper"] = types.ModuleType("mlx_whisper")
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)


def _drop_fake_mlx() -> None:
    sys.modules.pop("mlx_whisper", None)
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)


def test_cuda() -> None:
    with patch.object(runtime, "detect_device", return_value="cuda"):
        label = runtime.describe_backend()
    assert "CUDA" in label, label
    print(f"PASS: cuda -> {label!r}")


def test_mps_with_mlx() -> None:
    _install_fake_mlx()
    with patch.object(runtime, "detect_device", return_value="mps"):
        label = runtime.describe_backend()
    assert "MLX" in label and "Metal" in label, label
    print(f"PASS: mps+mlx -> {label!r}")
    _drop_fake_mlx()


def test_mps_without_mlx() -> None:
    _drop_fake_mlx()  # ensure no mlx_whisper module
    with patch.object(runtime, "detect_device", return_value="mps"):
        label = runtime.describe_backend()
    assert "MPS" in label and "sem MLX" in label.lower() or "sem MLX" in label, label
    print(f"PASS: mps-no-mlx -> {label!r}")


def test_cpu() -> None:
    with patch.object(runtime, "detect_device", return_value="cpu"):
        label = runtime.describe_backend()
    assert "CPU" in label, label
    print(f"PASS: cpu -> {label!r}")


if __name__ == "__main__":
    test_cuda()
    test_mps_with_mlx()
    test_mps_without_mlx()
    test_cpu()
    print("\nPASS: toy_describe_backend")
