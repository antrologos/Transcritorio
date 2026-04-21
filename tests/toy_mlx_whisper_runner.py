"""Toy test: mlx_whisper_runner wraps mlx_whisper.transcribe and produces
JSON compatible with WhisperX pipeline consumers (render.py).

Cannot run a real Apple MPS path here — we mock mlx_whisper so that the test
runs on Windows/Linux. The point is to validate:
  1. Module imports cleanly without mlx_whisper installed.
  2. is_available() returns True when mlx_whisper is importable.
  3. resolve_mlx_model() maps known names to HF repos.
  4. run_mlx_whisper() writes {interview_id}.json in asr_dir with the expected
     top-level shape (segments list, each with text/start/end and optional words).
  5. The produced JSON feeds render.build_turns() without crashing and yields
     at least one turn.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_fake_mlx_whisper(monkey_result: dict) -> None:
    """Plant a fake mlx_whisper module in sys.modules so import succeeds."""
    fake = types.ModuleType("mlx_whisper")

    def transcribe(audio_path, **kwargs):  # noqa: ARG001
        return dict(monkey_result)

    fake.transcribe = transcribe  # type: ignore[attr-defined]
    sys.modules["mlx_whisper"] = fake


def _remove_fake_mlx_whisper() -> None:
    sys.modules.pop("mlx_whisper", None)
    # Force re-import of runner to pick up new sys.modules state next time
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)


def test_import_without_mlx_present() -> None:
    """Runner imports cleanly on a system without mlx_whisper."""
    _remove_fake_mlx_whisper()
    from transcribe_pipeline import mlx_whisper_runner  # noqa: F401
    assert mlx_whisper_runner.is_available() is False, \
        "is_available() should be False when mlx_whisper is not installed"
    print("PASS: runner imports without mlx_whisper; is_available()=False")


def test_is_available_with_fake() -> None:
    _install_fake_mlx_whisper({"segments": []})
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)
    from transcribe_pipeline import mlx_whisper_runner
    assert mlx_whisper_runner.is_available() is True
    print("PASS: is_available()=True when mlx_whisper module is present")
    _remove_fake_mlx_whisper()


def test_resolve_mlx_model_mapping() -> None:
    from transcribe_pipeline import mlx_whisper_runner
    assert mlx_whisper_runner.resolve_mlx_model("large-v3") == "mlx-community/whisper-large-v3-mlx"
    assert mlx_whisper_runner.resolve_mlx_model("turbo") == "mlx-community/whisper-large-v3-turbo"
    # Pass-through for HF repo string
    assert mlx_whisper_runner.resolve_mlx_model("mlx-community/custom") == "mlx-community/custom"
    print("PASS: resolve_mlx_model mapping")


def test_run_writes_compatible_json() -> None:
    """Fake mlx_whisper output -> JSON on disk that render.build_turns accepts."""
    fake_result = {
        "language": "pt",
        "segments": [
            {
                "id": 0,
                "start": 0.5,
                "end": 2.8,
                "text": " Bom dia, tudo bem.",
                "words": [
                    {"word": "Bom", "start": 0.5, "end": 0.9, "probability": 0.98},
                    {"word": "dia", "start": 1.0, "end": 1.4, "probability": 0.97},
                    {"word": "tudo", "start": 1.7, "end": 2.1, "probability": 0.96},
                    {"word": "bem", "start": 2.3, "end": 2.8, "probability": 0.95},
                ],
            },
            {
                "id": 1,
                "start": 3.0,
                "end": 5.0,
                "text": " Muito obrigado.",
                "words": [
                    {"word": "Muito", "start": 3.0, "end": 3.8, "probability": 0.94},
                    {"word": "obrigado", "start": 3.9, "end": 5.0, "probability": 0.97},
                ],
            },
        ],
    }
    _install_fake_mlx_whisper(fake_result)
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)

    from transcribe_pipeline import mlx_whisper_runner
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths
    from transcribe_pipeline import render

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        cfg = dict(DEFAULT_CONFIG)
        cfg["project_root"] = str(project_root)
        paths = make_paths(cfg, base_dir=project_root)
        ensure_directories(paths)
        wav = paths.wav_dir / "A01.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        rows = [
            {
                "interview_id": "A01",
                "wav_path": str(wav.relative_to(project_root)),
                "source_path": "orig/A01.mp3",
                "source_sha256": "abc",
                "selected": "true",
            }
        ]
        cfg.update({
            "asr_model": "large-v3",
            "asr_language": "pt",
            "diarize": False,
            "diarization_source": "",
            "turn_gap_seconds": 0.6,
            "max_turn_seconds": 90,
            "speaker_labels": [],
        })
        failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
        assert failures == 0, f"run_mlx_whisper returned {failures}"

        # Runner must produce the JSON the pipeline reads
        json_path = render.find_whisperx_json(paths, "A01")
        assert json_path is not None, "No WhisperX-compatible JSON emitted"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "segments" in data and len(data["segments"]) == 2
        first = data["segments"][0]
        assert first["text"].strip() == "Bom dia, tudo bem."
        assert len(first["words"]) == 4
        w = first["words"][0]
        assert "word" in w and "start" in w and "end" in w

        # Run render.build_turns to prove downstream compatibility
        turns = render.build_turns(data, cfg, {})
        assert len(turns) >= 1, "build_turns produced no turns"
        assert turns[0].get("text")
        print(f"PASS: run_mlx_whisper emits render-compatible JSON ({len(turns)} turns)")

    _remove_fake_mlx_whisper()


def test_run_appends_job_log() -> None:
    """Runner appends an entry to jobs.jsonl with status=ok."""
    fake_result = {
        "language": "pt",
        "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "oi", "words": []}],
    }
    _install_fake_mlx_whisper(fake_result)
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)

    from transcribe_pipeline import mlx_whisper_runner
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        cfg = dict(DEFAULT_CONFIG)
        cfg["project_root"] = str(project_root)
        cfg["asr_model"] = "turbo"
        cfg["diarize"] = False
        paths = make_paths(cfg, base_dir=project_root)
        ensure_directories(paths)
        wav = paths.wav_dir / "B02.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        rows = [{"interview_id": "B02", "wav_path": str(wav.relative_to(project_root)), "selected": "true"}]
        mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

        log = paths.manifest_dir / "jobs.jsonl"
        assert log.exists(), "jobs.jsonl not written"
        lines = [json.loads(L) for L in log.read_text(encoding="utf-8").splitlines() if L.strip()]
        assert lines, "jobs.jsonl is empty"
        last = lines[-1]
        assert last["stage"] == "transcribe"
        assert last["status"] == "ok"
        assert last["model"] == "turbo"
        assert last["backend"] == "mlx-whisper"
        print("PASS: jobs.jsonl entry recorded with backend=mlx-whisper")

    _remove_fake_mlx_whisper()


if __name__ == "__main__":
    test_import_without_mlx_present()
    test_is_available_with_fake()
    test_resolve_mlx_model_mapping()
    test_run_writes_compatible_json()
    test_run_appends_job_log()
    print("\nPASS: toy_mlx_whisper_runner")
