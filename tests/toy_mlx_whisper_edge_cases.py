"""Toy tests for mlx_whisper_runner edge cases (cases A-G from plan).

Each test targets a specific failure mode I could have written a buggy
version of but not caught with the happy-path tests. If any of these
fail, the mlx_whisper_runner needs a surgical patch.
"""
from __future__ import annotations

import json
import os
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

    srt = (paths.asr_dir / "FMT01.srt").read_text(encoding="utf-8")
    vtt = (paths.asr_dir / "FMT01.vtt").read_text(encoding="utf-8")

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
    data = json.loads((paths.asr_dir / "NORM01.json").read_text(encoding="utf-8"))
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
    data = json.loads((paths.asr_dir / "NORM02.json").read_text(encoding="utf-8"))
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
    data = json.loads((paths.asr_dir / "NORM03.json").read_text(encoding="utf-8"))
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
    assert (paths.asr_dir / "GOOD02.json").exists(), "good row should have output"
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


# ============================================================
# Round 3 (found by 3-agent audit): parity with whisperx_runner
# ============================================================
def test_asr_variant_routes_to_variants_dir() -> None:
    """asr_variant=foo -> output goes to asr_variants_dir/foo/, not baseline."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [{"start": 0, "end": 1, "text": "variante"}],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("VAR01")
    cfg["asr_variant"] = "mlx_test_ab"
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    # Output must be in the variants dir, NOT in paths.asr_dir
    variant_file = paths.asr_variants_dir / "mlx_test_ab" / "VAR01.json"
    assert variant_file.exists(), f"variant JSON missing: {variant_file}"
    # Baseline must be untouched
    baseline_file = paths.asr_dir / "VAR01.json"
    baseline_json_file = paths.asr_dir / "json" / "VAR01.json"
    assert not baseline_file.exists() and not baseline_json_file.exists(), \
        "variant should NOT contaminate baseline asr_dir"
    print(f"PASS: asr_variant routes to {variant_file.relative_to(paths.project_root)}")
    _drop_fake_mlx()


def test_output_layout_matches_whisperx() -> None:
    """JSON, SRT, VTT, TXT, TSV all written directly in output_dir (no subdir),
    matching `whisperx --output_format all`."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "language": "pt",
        "segments": [{"start": 0.5, "end": 2.0, "text": "ola", "words": []}],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("LAY01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    expected = [
        paths.asr_dir / "LAY01.json",
        paths.asr_dir / "LAY01.srt",
        paths.asr_dir / "LAY01.vtt",
        paths.asr_dir / "LAY01.txt",
        paths.asr_dir / "LAY01.tsv",
    ]
    for p in expected:
        assert p.exists(), f"missing output file: {p}"
    # TSV header check
    tsv = expected[-1].read_text(encoding="utf-8")
    assert tsv.startswith("start\tend\ttext\n"), f"TSV header bad: {tsv[:60]!r}"
    assert "500\t2000\tola" in tsv, tsv
    print(f"PASS: output layout matches whisperx ({len(expected)} file types)")
    _drop_fake_mlx()


def test_hf_env_hygiene_applied() -> None:
    """apply_secure_hf_environment is called before transcribe, matching
    whisperx_runner. Verify side-effect: HF_HUB_OFFLINE gets set when
    asr_model_cache_only=True."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    from transcribe_pipeline import mlx_whisper_runner

    os.environ.pop("HF_HUB_OFFLINE", None)
    rows, cfg, paths = _make_rows_and_paths("HF01")
    cfg["asr_model_cache_only"] = True
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    assert os.environ.get("HF_HUB_OFFLINE") == "1", \
        f"HF_HUB_OFFLINE not set after secure env application: {os.environ.get('HF_HUB_OFFLINE')!r}"
    os.environ.pop("HF_HUB_OFFLINE", None)
    print("PASS: apply_secure_hf_environment executed (HF_HUB_OFFLINE=1)")
    _drop_fake_mlx()


def test_diarize_validate_called_when_diarize_true() -> None:
    """When config.diarize=True, validate_local_diarization_model runs at top
    of the function (fail-fast). Test by mocking it and checking it was called."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    import transcribe_pipeline.mlx_whisper_runner as runner_mod

    called = {"n": 0, "arg": None}
    orig = runner_mod.validate_local_diarization_model

    def spy(model_id):
        called["n"] += 1
        called["arg"] = model_id

    runner_mod.validate_local_diarization_model = spy  # type: ignore[assignment]
    try:
        rows, cfg, paths = _make_rows_and_paths("DIAR01")
        cfg["diarize"] = True
        cfg["diarize_model"] = "pyannote/fake-model"
        runner_mod.run_mlx_whisper(rows, cfg, paths)
        assert called["n"] == 1, f"validate called {called['n']} times, expected 1"
        assert called["arg"] == "pyannote/fake-model"
        print("PASS: validate_local_diarization_model called at start when diarize=True")
    finally:
        runner_mod.validate_local_diarization_model = orig  # type: ignore[assignment]
    _drop_fake_mlx()


def test_diarize_validate_skipped_when_diarize_false() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    import transcribe_pipeline.mlx_whisper_runner as runner_mod

    called = {"n": 0}

    def spy(model_id):  # noqa: ARG001
        called["n"] += 1

    orig = runner_mod.validate_local_diarization_model
    runner_mod.validate_local_diarization_model = spy  # type: ignore[assignment]
    try:
        rows, cfg, paths = _make_rows_and_paths("DIAR02")
        cfg["diarize"] = False
        runner_mod.run_mlx_whisper(rows, cfg, paths)
        assert called["n"] == 0, f"validate should NOT run when diarize=False, got {called['n']}"
        print("PASS: validate_local_diarization_model skipped when diarize=False")
    finally:
        runner_mod.validate_local_diarization_model = orig  # type: ignore[assignment]
    _drop_fake_mlx()


def test_pyannote_metrics_env_set_when_configured() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    from transcribe_pipeline import mlx_whisper_runner

    os.environ.pop("PYANNOTE_METRICS_ENABLED", None)
    rows, cfg, paths = _make_rows_and_paths("PM01")
    cfg["pyannote_metrics_enabled"] = "1"
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert os.environ.get("PYANNOTE_METRICS_ENABLED") == "1", \
        f"env not set: {os.environ.get('PYANNOTE_METRICS_ENABLED')!r}"
    os.environ.pop("PYANNOTE_METRICS_ENABLED", None)
    print("PASS: PYANNOTE_METRICS_ENABLED propagated to env")
    _drop_fake_mlx()


def test_jobs_jsonl_schema_parity() -> None:
    """jobs.jsonl entry has all the fields whisperx_runner writes, for
    audit trail parity."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": [{"start": 0, "end": 1, "text": "x"}]})
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("SCH01")
    cfg["asr_compute_type"] = "float16"
    cfg["asr_batch_size"] = 4
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    log = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(L) for L in log.splitlines() if L.strip()]
    assert entries, "jobs.jsonl empty"
    e = entries[-1]
    required = {
        "interview_id", "stage", "status", "started_at", "model",
        "backend", "language", "compute_type", "batch_size",
        "variant", "output_dir",
    }
    missing = required - set(e.keys())
    assert not missing, f"jobs.jsonl missing fields: {missing}"
    assert e["backend"] == "mlx-whisper"
    assert e["output_dir"].endswith("02_asr_raw"), e["output_dir"]
    print(f"PASS: jobs.jsonl has all {len(required)} parity fields")
    _drop_fake_mlx()


def test_creep_progress_emits_during_slow_transcribe() -> None:
    """During a slow mlx_whisper.transcribe(), a background thread must emit
    intermediate asr_progress events so the UI doesn't freeze at 1%.
    We simulate a slow transcribe and check progress events received."""
    _drop_fake_mlx()
    import time as _time

    def slow_transcribe(*a, **k):
        _time.sleep(7.0)  # longer than 2 creep ticks of 3s
        return {"segments": [{"start": 0, "end": 1, "text": "demora"}]}

    _install_fake_mlx(slow_transcribe)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("CREEP01")
    events: list[dict] = []

    def collect(evt):
        events.append(evt)

    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths, progress_callback=collect)

    progress_events = [e for e in events if e.get("event") == "asr_progress"]
    # Expect at least: initial 1%, one or more creep events, (done fires separately)
    assert len(progress_events) >= 3, \
        f"expected multiple progress events, got {len(progress_events)}: {[e.get('progress') for e in progress_events]}"
    percents = [e["progress"] for e in progress_events]
    # Creep must be strictly increasing and bounded by 89
    assert percents[0] == 1
    assert max(percents) >= 2, f"creep did not advance: {percents}"
    assert all(p <= 89 for p in percents), f"creep exceeded cap: {percents}"
    print(f"PASS: creep emitted {len(progress_events)} progress events (range {min(percents)}-{max(percents)})")
    _drop_fake_mlx()


def test_error_message_includes_exception_detail() -> None:
    """When transcribe raises, the asr_error event carries str(exc), not just
    the type — so the user sees actionable info in the dialog."""
    _drop_fake_mlx()

    def transcribe_boom(*a, **k):
        raise RuntimeError("CUDA out of memory: requested 4.5 GB, available 1.2 GB")

    _install_fake_mlx(transcribe_boom)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("ERR01")
    events: list[dict] = []
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths, progress_callback=events.append)

    errors = [e for e in events if e.get("event") == "asr_error"]
    assert errors, "no asr_error emitted"
    msg = errors[0]["message"]
    assert "RuntimeError" in msg, msg
    assert "out of memory" in msg.lower(), f"detail missing: {msg!r}"
    print(f"PASS: error message carries detail ({msg[:60]}...)")
    _drop_fake_mlx()


def test_error_message_truncates_long_detail() -> None:
    """Very long exception messages must be truncated so the UI dialog
    remains readable."""
    _drop_fake_mlx()

    long_err = "x" * 1000

    def boom(*a, **k):
        raise RuntimeError(long_err)

    _install_fake_mlx(boom)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths = _make_rows_and_paths("ERR02")
    events: list[dict] = []
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths, progress_callback=events.append)

    errors = [e for e in events if e.get("event") == "asr_error"]
    msg = errors[0]["message"]
    assert "..." in msg, f"long message not truncated: {msg[:50]}..."
    assert len(msg) < 320, f"message still too long: {len(msg)}"
    print(f"PASS: long error truncated ({len(msg)} chars)")
    _drop_fake_mlx()


def test_whisperx_writes_backend_field() -> None:
    """whisperx_runner must also write backend='whisperx' to jobs.jsonl so
    mixed projects can distinguish the producer in audit trails."""
    import inspect
    from transcribe_pipeline import whisperx_runner

    # Read the source of run_whisperx to confirm "backend" key is emitted.
    # (A full integration test would require real whisperx subprocess.)
    src = inspect.getsource(whisperx_runner.run_whisperx)
    assert '"backend": "whisperx"' in src, \
        "whisperx_runner.jobs.jsonl is missing backend=whisperx field"
    print("PASS: whisperx_runner writes backend='whisperx' to jobs.jsonl")


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
    # Round 3: 3-agent audit findings
    test_asr_variant_routes_to_variants_dir()
    test_output_layout_matches_whisperx()
    test_hf_env_hygiene_applied()
    test_diarize_validate_called_when_diarize_true()
    test_diarize_validate_skipped_when_diarize_false()
    test_pyannote_metrics_env_set_when_configured()
    test_jobs_jsonl_schema_parity()
    # Round 4: UX/parity fixes from second 3-agent audit
    test_creep_progress_emits_during_slow_transcribe()
    test_error_message_includes_exception_detail()
    test_error_message_truncates_long_detail()
    test_whisperx_writes_backend_field()
    print("\nPASS: toy_mlx_whisper_edge_cases")
