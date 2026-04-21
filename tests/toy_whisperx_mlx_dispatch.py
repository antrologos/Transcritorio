"""Toy test: whisperx_runner.run_whisperx dispatches to mlx_whisper_runner
when MPS is the detected device AND mlx_whisper is importable AND the user
did not force cpu. Otherwise it stays on the whisperx CLI path.

Critical non-regression: on Windows (no torch/MPS), dispatch must NOT trigger.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fake_mlx_whisper_module() -> None:
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = lambda *a, **k: {"segments": []}  # type: ignore[attr-defined]
    sys.modules["mlx_whisper"] = fake


def _drop_mlx_modules() -> None:
    sys.modules.pop("mlx_whisper", None)
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)
    sys.modules.pop("transcribe_pipeline.whisperx_runner", None)


def test_mps_plus_mlx_dispatches() -> None:
    _drop_mlx_modules()
    _fake_mlx_whisper_module()
    from transcribe_pipeline import whisperx_runner, runtime

    # Force detected device to mps by monkeypatching the cache
    with patch.object(runtime, "detect_device", return_value="mps"):
        mlx_called = {"count": 0}

        def fake_mlx_runner(rows, config, paths, **kwargs):  # noqa: ARG001
            mlx_called["count"] += 1
            return 0

        with patch.object(whisperx_runner, "mlx_whisper_runner") as mock_mod:
            mock_mod.is_available.return_value = True
            mock_mod.run_mlx_whisper = fake_mlx_runner
            result = whisperx_runner.run_whisperx(
                rows=[],
                config={
                    "asr_device": "mps",
                    "model_download_token_env": "HF_TOKEN",
                },
                paths=None,  # type: ignore[arg-type]
            )
        assert result == 0
        assert mlx_called["count"] == 1, "MLX path was not taken"
    print("PASS: MPS + mlx-whisper -> dispatched to MLX runner")
    _drop_mlx_modules()


def test_mps_without_mlx_stays_on_cli_path() -> None:
    _drop_mlx_modules()
    # Do NOT install fake mlx module -> is_available() returns False
    from transcribe_pipeline import whisperx_runner, runtime

    with patch.object(runtime, "detect_device", return_value="mps"):
        with patch.object(whisperx_runner, "mlx_whisper_runner") as mock_mod:
            mock_mod.is_available.return_value = False
            # Also stub out the heavy downstream call so we don't actually
            # invoke whisperx. run_whisperx will iterate rows; we pass [] so
            # the loop body never runs and it returns 0 naturally.
            result = whisperx_runner.run_whisperx(
                rows=[],
                config={
                    "asr_device": "mps",
                    "model_download_token_env": "HF_TOKEN",
                },
                paths=_StubPaths(),
            )
        # No MLX dispatch happened; returns 0 because rows=[]
        assert result == 0
        mock_mod.run_mlx_whisper.assert_not_called()
    print("PASS: MPS without mlx-whisper -> stays on whisperx CLI path")
    _drop_mlx_modules()


def test_cpu_forced_skips_mlx() -> None:
    _drop_mlx_modules()
    _fake_mlx_whisper_module()
    from transcribe_pipeline import whisperx_runner, runtime

    with patch.object(runtime, "detect_device", return_value="mps"):
        with patch.object(whisperx_runner, "mlx_whisper_runner") as mock_mod:
            mock_mod.is_available.return_value = True
            result = whisperx_runner.run_whisperx(
                rows=[],
                config={
                    "asr_device": "cpu",
                    "model_download_token_env": "HF_TOKEN",
                },
                paths=_StubPaths(),
            )
        assert result == 0
        mock_mod.run_mlx_whisper.assert_not_called()
    print("PASS: asr_device=cpu skips MLX even on MPS")
    _drop_mlx_modules()


def test_windows_like_stays_cuda_path() -> None:
    """On Windows (no MPS detected), MLX path must never fire."""
    _drop_mlx_modules()
    _fake_mlx_whisper_module()  # even if the module magically imports
    from transcribe_pipeline import whisperx_runner, runtime

    with patch.object(runtime, "detect_device", return_value="cuda"):
        with patch.object(whisperx_runner, "mlx_whisper_runner") as mock_mod:
            mock_mod.is_available.return_value = True
            result = whisperx_runner.run_whisperx(
                rows=[],
                config={
                    "asr_device": "cuda",
                    "model_download_token_env": "HF_TOKEN",
                },
                paths=_StubPaths(),
            )
        assert result == 0
        mock_mod.run_mlx_whisper.assert_not_called()
    print("PASS: CUDA device detected -> whisperx CLI path kept")
    _drop_mlx_modules()


class _StubPaths:
    """Minimal Paths stub: only attributes touched on the non-dispatch
    path AFTER the MLX check. We short-circuit by passing rows=[]."""
    def __init__(self) -> None:
        import tempfile
        self._tmp = Path(tempfile.mkdtemp(prefix="mlx_toy_"))
        self.project_root = self._tmp
        self.asr_dir = self._tmp / "asr"
        self.asr_variants_dir = self._tmp / "variants"
        self.manifest_dir = self._tmp / "manifest"
        self.asr_dir.mkdir(exist_ok=True)


if __name__ == "__main__":
    test_mps_plus_mlx_dispatches()
    test_mps_without_mlx_stays_on_cli_path()
    test_cpu_forced_skips_mlx()
    test_windows_like_stays_cuda_path()
    print("\nPASS: toy_whisperx_mlx_dispatch")
