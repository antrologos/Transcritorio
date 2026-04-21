"""Toy tests for mlx_whisper_runner edge cases (cases A-G from plan).

Each test targets a specific failure mode I could have written a buggy
version of but not caught with the happy-path tests. If any of these
fail, the mlx_whisper_runner needs a surgical patch.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_fake_mlx(transcribe_fn) -> None:
    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = transcribe_fn  # type: ignore[attr-defined]
    sys.modules["mlx_whisper"] = fake
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)


def _drop_fake_mlx() -> None:
    sys.modules.pop("mlx_whisper", None)
    sys.modules.pop("transcribe_pipeline.mlx_whisper_runner", None)


def _make_rows_and_paths(interview_id: str = "EDGE01"):
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths
    tmp = tempfile.mkdtemp(prefix="edge_")
    project_root = Path(tmp)
    cfg = dict(DEFAULT_CONFIG)
    cfg["project_root"] = str(project_root)
    paths = make_paths(cfg, base_dir=project_root)
    ensure_directories(paths)
    wav = paths.wav_dir / f"{interview_id}.wav"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    rows = [{
        "interview_id": interview_id,
        "wav_path": str(wav.relative_to(project_root)),
        "selected": "true",
    }]
    return rows, cfg, paths


# ============================================================
# Case B: SRT/VTT timestamp format correctness
# ============================================================
def test_srt_timestamp_has_milliseconds_and_comma() -> None:
    """SRT needs HH:MM:SS,mmm. VTT needs HH:MM:SS.mmm."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "language": "pt", "text": "",
        "segments": [{"id": 0, "start": 65.5, "end": 67.123, "text": "teste", "words": []}],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("FMT01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    srt = (paths.asr_dir / "srt" / "FMT01.srt").read_text(encoding="utf-8")
    vtt = (paths.asr_dir / "vtt" / "FMT01.vtt").read_text(encoding="utf-8")

    # SRT: must contain e.g. "00:01:05,500 --> 00:01:07,123"
    assert "00:01:05,500 --> 00:01:07,123" in srt, f"SRT bad: {srt[:200]!r}"
    # VTT: must contain "00:01:05.500 --> 00:01:07.123"
    assert "00:01:05.500 --> 00:01:07.123" in vtt, f"VTT bad: {vtt[:200]!r}"
    print("PASS: SRT/VTT timestamp format (mmm with correct separator)")
    _drop_fake_mlx()


# ============================================================
# Case A: _normalize_mlx_result with malformed inputs
# ============================================================
def test_normalize_handles_missing_segments_key() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {})  # no "segments" at all
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("NORM01")
    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert failures == 0, "empty result dict should not be a failure"
    data = json.loads((paths.asr_dir / "json" / "NORM01.json").read_text(encoding="utf-8"))
    assert data["segments"] == [], data
    print("PASS: normalize survives missing 'segments' key")
    _drop_fake_mlx()


def test_normalize_filters_non_dict_segments() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [
            "not a dict",
            None,
            {"id": 0, "start": 1.0, "end": 2.0, "text": "ok", "words": []},
            42,
        ],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("NORM02")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    data = json.loads((paths.asr_dir / "json" / "NORM02.json").read_text(encoding="utf-8"))
    assert len(data["segments"]) == 1, data
    print("PASS: normalize filters non-dict segments")
    _drop_fake_mlx()


def test_normalize_handles_missing_word_probability() -> None:
    """Real mlx_whisper word objects have 'probability' but we shouldn't crash
    if it's missing or word objects are weird."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [{
            "start": 0.0, "end": 1.0, "text": "a b c",
            "words": [
                {"word": "a", "start": 0.0, "end": 0.3},  # no probability
                {"text": "b", "start": 0.4, "end": 0.6},  # "text" instead of "word"
                {"word": "", "start": 0.7, "end": 0.8},   # empty word text -> skip
                {"word": "c", "start": "bad", "end": 1.0},  # bad start -> skip
                "not a dict",  # -> skip
                {"word": "d", "start": 1.1, "end": 1.5},  # OK
            ],
        }],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("NORM03")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    data = json.loads((paths.asr_dir / "json" / "NORM03.json").read_text(encoding="utf-8"))
    words = data["segments"][0]["words"]
    # Expected: a, b, d (3 valid)
    assert [w["word"] for w in words] == ["a", "b", "d"], words
    print(f"PASS: normalize filters malformed words ({len(words)} valid of 6 raw)")
    _drop_fake_mlx()


# ============================================================
# Case C: exception from mlx_whisper.transcribe is captured
# ============================================================
def test_transcribe_exception_caught_and_logged() -> None:
    """If mlx_whisper.transcribe raises, the runner should log, increment
    failures, and continue with remaining rows (not crash the whole batch)."""
    _drop_fake_mlx()

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("metal OOM or whatever")
        return {"segments": [{"start": 0, "end": 1, "text": "ok"}]}

    _install_fake_mlx(boom)
    from transcribe_pipeline import mlx_whisper_runner
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths

    tmp = tempfile.mkdtemp(prefix="boom_")
    project_root = Path(tmp)
    cfg = dict(DEFAULT_CONFIG)
    cfg["project_root"] = str(project_root)
    paths = make_paths(cfg, base_dir=project_root)
    ensure_directories(paths)
    for iid in ("BAD01", "GOOD02"):
        (paths.wav_dir / f"{iid}.wav").write_bytes(b"RIFF")
    rows = [
        {"interview_id": "BAD01", "wav_path": f"Transcricoes/01_audio_wav16k_mono/BAD01.wav", "selected": "true"},
        {"interview_id": "GOOD02", "wav_path": f"Transcricoes/01_audio_wav16k_mono/GOOD02.wav", "selected": "true"},
    ]
    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert failures == 1, f"one failure expected, got {failures}"
    assert calls["n"] == 2, "runner should have called both rows"
    # Good row still produced JSON
    assert (paths.asr_dir / "json" / "GOOD02.json").exists(), "good row should have output"
    # jobs.jsonl should have an error entry
    log = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(L) for L in log.splitlines() if L.strip()]
    statuses = [e["status"] for e in entries]
    assert "error" in statuses and "ok" in statuses, statuses
    print(f"PASS: exception in 1 row doesn't kill batch ({statuses})")
    _drop_fake_mlx()


# ============================================================
# Case D: Existing configs load with new keys merged in
# ============================================================
def test_legacy_config_loads_with_new_keys() -> None:
    """A run_config.yaml without asr_use_mlx_on_mps must still load and
    receive the new default from DEFAULT_CONFIG."""
    from transcribe_pipeline.config import load_config

    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write("asr_model: large-v3\nasr_device: cpu\n")
        legacy_path = Path(f.name)

    try:
        cfg = load_config(legacy_path)
        # New keys present via DEFAULT_CONFIG merge
        assert cfg["asr_use_mlx_on_mps"] is True, cfg
        assert cfg["asr_word_timestamps"] is True, cfg
        # Legacy keys preserved
        assert cfg["asr_model"] == "large-v3"
        assert cfg["asr_device"] == "cpu"
        print("PASS: legacy config loads with new keys defaulting to True")
    finally:
        legacy_path.unlink(missing_ok=True)


# ============================================================
# Case F: word_timestamps config flag propagates
# ============================================================
def test_word_timestamps_flag_propagates() -> None:
    _drop_fake_mlx()
    received = {}

    def record(*a, **k):
        received.update(k)
        return {"segments": []}

    _install_fake_mlx(record)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("WT01")
    cfg["asr_word_timestamps"] = False  # explicit opt-out
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert received.get("word_timestamps") is False, received
    print("PASS: asr_word_timestamps=False reaches mlx_whisper.transcribe")
    _drop_fake_mlx()


def test_word_timestamps_default_is_true() -> None:
    _drop_fake_mlx()
    received = {}

    def record(*a, **k):
        received.update(k)
        return {"segments": []}

    _install_fake_mlx(record)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("WT02")
    # Do NOT set asr_word_timestamps -> should default to True
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert received.get("word_timestamps") is True, received
    print("PASS: asr_word_timestamps defaults to True when unset")
    _drop_fake_mlx()


# ============================================================
# Case G: resolve_mlx_model with empty/None/weird inputs
# ============================================================
def test_resolve_mlx_model_with_empty_and_none() -> None:
    from transcribe_pipeline import mlx_whisper_runner

    # Empty string -> fall back to large-v3
    assert mlx_whisper_runner.resolve_mlx_model("") == "mlx-community/whisper-large-v3-mlx"
    # None (cast to str) -> same fallback
    assert mlx_whisper_runner.resolve_mlx_model(None) == "mlx-community/whisper-large-v3-mlx"  # type: ignore[arg-type]
    # Whitespace -> fallback
    assert mlx_whisper_runner.resolve_mlx_model("   ") == "mlx-community/whisper-large-v3-mlx"
    # Unknown name -> constructed repo
    assert mlx_whisper_runner.resolve_mlx_model("foo") == "mlx-community/whisper-foo-mlx"
    print("PASS: resolve_mlx_model handles empty/None/whitespace/unknown")


if __name__ == "__main__":
    test_srt_timestamp_has_milliseconds_and_comma()
    test_normalize_handles_missing_segments_key()
    test_normalize_filters_non_dict_segments()
    test_normalize_handles_missing_word_probability()
    test_transcribe_exception_caught_and_logged()
    test_legacy_config_loads_with_new_keys()
    test_word_timestamps_flag_propagates()
    test_word_timestamps_default_is_true()
    test_resolve_mlx_model_with_empty_and_none()
    print("\nPASS: toy_mlx_whisper_edge_cases")
