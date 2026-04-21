"""Rodada 5: inputs adversariais + seguranca + boundary conditions.

Agentes da 4a auditoria acharam estes cenarios reais. Tests escritos
ANTES do fix, conforme protocolo investigate→plan→test→fix.
"""
from __future__ import annotations

import json
import math
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


def _make_paths_and_cfg(interview_id: str = "X01", wav_path: str | None = None):
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths
    tmp = tempfile.mkdtemp(prefix="r5_")
    project_root = Path(tmp).resolve()
    cfg = dict(DEFAULT_CONFIG)
    cfg["project_root"] = str(project_root)
    paths = make_paths(cfg, base_dir=project_root)
    ensure_directories(paths)
    if wav_path is None:
        wav = paths.wav_dir / f"{interview_id}.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        wav_path = str(wav.relative_to(project_root))
    rows = [{"interview_id": interview_id, "wav_path": wav_path, "selected": "true"}]
    return rows, cfg, paths, project_root


# ============================================================
# Security: path traversal in interview_id
# ============================================================
def test_interview_id_with_path_traversal_rejected() -> None:
    """interview_id='../../../evil' must NOT create files outside project."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": [{"start": 0, "end": 1, "text": "x"}]})
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, project_root = _make_paths_and_cfg("../../../evil")
    # Create a fake WAV at the sanitized name too (runner may refuse based on
    # non-existence). We just want to ensure no files leak outside project.
    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    # Must NOT create a file at project_root.parent.parent.parent/evil.json
    suspicious = project_root.parent.parent.parent / "evil.json"
    assert not suspicious.exists(), f"path traversal succeeded: {suspicious}"
    # Must log error (failures >= 1) or sanitize the name so output stays inside
    for root, _dirs, files in os.walk(project_root):
        for f in files:
            p = Path(root) / f
            try:
                p.relative_to(project_root)
            except ValueError:
                raise AssertionError(f"output escaped project: {p}")
    print("PASS: interview_id path traversal does not escape project")
    _drop_fake_mlx()


# ============================================================
# Robustness: _normalize_mlx_result with weird shapes
# ============================================================
def test_normalize_handles_none_result() -> None:
    """mlx_whisper.transcribe() returning None must not crash the runner."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: None)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("NONE01")
    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    # None result is a failure, but must not propagate as unhandled exception.
    assert failures == 1, f"expected 1 failure for None result, got {failures}"
    log = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(L) for L in log.splitlines() if L.strip()]
    assert entries[-1]["status"] == "error", entries[-1]
    print("PASS: None result handled (failure logged, no crash)")
    _drop_fake_mlx()


def test_normalize_drops_nonfinite_timestamps() -> None:
    """NaN/Inf in start/end must be dropped, not written."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [
            {"start": float("nan"), "end": 1.0, "text": "nan_start"},
            {"start": 0.0, "end": float("inf"), "text": "inf_end"},
            {"start": 0.0, "end": 1.0, "text": "ok"},
        ],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("NF01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    data = json.loads((paths.asr_dir / "NF01.json").read_text(encoding="utf-8"))
    texts = [s["text"] for s in data["segments"]]
    assert texts == ["ok"], f"NaN/Inf segments should be dropped: {texts}"
    print("PASS: NaN/Inf timestamps dropped from segments")
    _drop_fake_mlx()


def test_normalize_fixes_inverted_timestamps() -> None:
    """start > end: either swap or drop; never write as-is."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [{"start": 5.0, "end": 2.0, "text": "invertido"}],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("INV01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    data = json.loads((paths.asr_dir / "INV01.json").read_text(encoding="utf-8"))
    if data["segments"]:
        seg = data["segments"][0]
        assert seg["end"] >= seg["start"], f"end still < start: {seg}"
    print(f"PASS: inverted timestamps handled ({len(data['segments'])} segments kept)")
    _drop_fake_mlx()


def test_normalize_non_dict_result() -> None:
    """transcribe returning a list/str/int must be a failure, not crash."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: ["not", "a", "dict"])
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("BAD01")
    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert failures == 1, f"non-dict result should be failure: got {failures}"
    print("PASS: non-dict transcribe result -> graceful failure")
    _drop_fake_mlx()


# ============================================================
# SRT/TSV injection via segment text
# ============================================================
def test_srt_text_with_arrow_does_not_inject_timestamp() -> None:
    """If segment text contains '-->' the SRT parser would get confused.
    Must escape/replace."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [{"start": 0.0, "end": 1.0,
                       "text": "ele disse --> 99:99:99,999 fake"}],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("SRT01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    srt = (paths.asr_dir / "SRT01.srt").read_text(encoding="utf-8")
    # The real timecode line should appear exactly once.
    arrow_lines = [L for L in srt.splitlines() if " --> " in L]
    assert len(arrow_lines) == 1, f"SRT injection? {arrow_lines}"
    print("PASS: SRT text with '-->' does not inject fake timestamp line")
    _drop_fake_mlx()


def test_tsv_escapes_tab_and_newline() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {
        "segments": [
            {"start": 0, "end": 1, "text": "col\tcom\ttab"},
            {"start": 1, "end": 2, "text": "linha\ncom\nquebra"},
        ],
    })
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("TSV01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    tsv = (paths.asr_dir / "TSV01.tsv").read_text(encoding="utf-8")
    lines = tsv.splitlines()
    # Header + 2 rows = 3 lines exactly (no extra from \n in text)
    assert len(lines) == 3, f"TSV has wrong line count: {len(lines)}: {lines}"
    # No \t past column 2 in row lines (col 0=start, 1=end, 2=text)
    for line in lines[1:]:
        tabs = line.count("\t")
        assert tabs == 2, f"row has extra tabs: {line!r}"
    print("PASS: TSV escapes \\t and \\n in segment text")
    _drop_fake_mlx()


# ============================================================
# Security: sanitize exception str before logging
# ============================================================
def test_jobs_jsonl_sanitizes_hf_token_in_error() -> None:
    """If exception contains an HF token, it must be redacted in jobs.jsonl."""
    _drop_fake_mlx()

    def boom(*a, **k):
        raise RuntimeError("auth failed: token hf_abcdefghijklmnop12345 not accepted")

    _install_fake_mlx(boom)
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("TOK01")
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    log = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(L) for L in log.splitlines() if L.strip()]
    err_entry = [e for e in entries if e.get("status") == "error"][-1]
    err_text = err_entry.get("error", "")
    assert "hf_abcdefghijklmnop12345" not in err_text, \
        f"HF token NOT redacted in jobs.jsonl: {err_text!r}"
    assert "<REDACTED>" in err_text or "REDACTED" in err_text, \
        f"expected redaction marker: {err_text!r}"
    print("PASS: HF token redacted in jobs.jsonl error entry")
    _drop_fake_mlx()


# ============================================================
# Robustness: progress_callback raises
# ============================================================
def test_progress_callback_exception_does_not_break_run() -> None:
    """If the GUI callback raises, the runner must not abort the batch."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": [{"start": 0, "end": 1, "text": "ok"}]})
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, _ = _make_paths_and_cfg("CB01")

    calls = {"n": 0}

    def rude_callback(evt):
        calls["n"] += 1
        raise ValueError("simulated Qt signal disconnect")

    failures = mlx_whisper_runner.run_mlx_whisper(
        rows, cfg, paths, progress_callback=rude_callback
    )
    # Batch must still finish; file must be written
    assert (paths.asr_dir / "CB01.json").exists(), "output missing after callback raised"
    assert failures == 0, f"callback exception should not fail row: {failures}"
    assert calls["n"] >= 1, "callback was never called"
    print(f"PASS: progress_callback exceptions swallowed ({calls['n']} attempted calls)")
    _drop_fake_mlx()


# ============================================================
# config.model_cache_dir override flows to HF env
# ============================================================
def test_model_cache_dir_override_applied() -> None:
    """When config.model_cache_dir is set, HF_HOME/HF_HUB_CACHE point to it."""
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    from transcribe_pipeline import mlx_whisper_runner

    rows, cfg, paths, project_root = _make_paths_and_cfg("CACHE01")
    custom_cache = project_root / "custom_cache"
    custom_cache.mkdir()
    cfg["model_cache_dir"] = str(custom_cache)

    # Clear any prior env
    os.environ.pop("HF_HUB_CACHE", None)
    os.environ.pop("HF_HOME", None)
    mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)

    hf_cache = os.environ.get("HF_HUB_CACHE", "")
    assert str(custom_cache) in hf_cache, \
        f"HF_HUB_CACHE should reflect config override; got {hf_cache!r}"
    os.environ.pop("HF_HUB_CACHE", None)
    os.environ.pop("HF_HOME", None)
    print(f"PASS: config.model_cache_dir -> HF_HUB_CACHE ({hf_cache})")
    _drop_fake_mlx()


# ============================================================
# Empty interview_id edge
# ============================================================
def test_empty_interview_id_is_failure_not_crash() -> None:
    _drop_fake_mlx()
    _install_fake_mlx(lambda *a, **k: {"segments": []})
    from transcribe_pipeline import mlx_whisper_runner
    from transcribe_pipeline.config import DEFAULT_CONFIG, ensure_directories, make_paths

    tmp = tempfile.mkdtemp(prefix="empty_")
    project_root = Path(tmp).resolve()
    cfg = dict(DEFAULT_CONFIG)
    cfg["project_root"] = str(project_root)
    paths = make_paths(cfg, base_dir=project_root)
    ensure_directories(paths)
    wav = paths.wav_dir / "X.wav"
    wav.write_bytes(b"RIFF")
    rows = [{"interview_id": "", "wav_path": str(wav.relative_to(project_root)), "selected": "true"}]

    failures = mlx_whisper_runner.run_mlx_whisper(rows, cfg, paths)
    assert failures == 1, f"empty interview_id should fail row: got {failures}"
    # No bogus '.json' file at output_dir root
    bogus = paths.asr_dir / ".json"
    assert not bogus.exists(), "empty id created a suspicious file"
    print("PASS: empty interview_id -> graceful failure")
    _drop_fake_mlx()


if __name__ == "__main__":
    test_interview_id_with_path_traversal_rejected()
    test_normalize_handles_none_result()
    test_normalize_drops_nonfinite_timestamps()
    test_normalize_fixes_inverted_timestamps()
    test_normalize_non_dict_result()
    test_srt_text_with_arrow_does_not_inject_timestamp()
    test_tsv_escapes_tab_and_newline()
    test_jobs_jsonl_sanitizes_hf_token_in_error()
    test_progress_callback_exception_does_not_break_run()
    test_model_cache_dir_override_applied()
    test_empty_interview_id_is_failure_not_crash()
    print("\nPASS: toy_mlx_whisper_round5")
