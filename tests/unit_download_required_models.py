"""Unit test for download_required_models — _manual_snapshot_download mocked.

Validates the outer loop in download_required_models (progress events,
failure handling, revision enforcement) without hitting real HF network.
We stub _manual_snapshot_download directly — integration of the manual
downloader against real HF is covered by tests/live_smoke_hf_download.py.

Run with: python -B tests/unit_download_required_models.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline import model_manager  # noqa: E402
from transcribe_pipeline.model_manager import ModelAsset  # noqa: E402


def _fake_manual():
    """Stub _manual_snapshot_download that emits bytes events + returns."""
    def _impl(**kwargs):
        cb = kwargs.get("progress_callback")
        label = kwargs.get("label", "?")
        start_pct = kwargs.get("start_pct", 0)
        end_pct = kwargs.get("end_pct", 100)
        if cb:
            for pct_of_file in (25, 50, 75, 100):
                overall = start_pct + int((end_pct - start_pct) * pct_of_file / 100)
                cb({
                    "event": "model_download_bytes",
                    "progress": overall,
                    "message": f"{label} {pct_of_file}%",
                })
        return Path(str(kwargs["cache_dir"])) / "fake_snapshot"
    return _impl


def test_full_progress_sequence() -> None:
    fake_asset = ModelAsset(
        key="test-variant",
        label="Test Model",
        repo_id="fakeorg/fakemodel",
        purpose="asr",
        gated=False,
        estimated_gb=0.005,
        revision="a" * 40,  # pinned revision obrigatorio
    )
    events: list[dict] = []
    events_lock = threading.Lock()

    def callback(detail: dict) -> None:
        with events_lock:
            events.append(dict(detail))

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(model_manager, "get_required_models", return_value=[fake_asset]), \
             patch.object(model_manager.runtime, "model_cache_dir", return_value=Path(tmp)), \
             patch.object(model_manager.runtime, "apply_secure_hf_environment"), \
             patch.object(model_manager, "_manual_snapshot_download",
                          side_effect=_fake_manual()):
            failures = model_manager.download_required_models(
                token="fake-token",
                progress_callback=callback,
            )

    assert failures == 0, f"expected 0 failures, got {failures}"
    event_types = [e["event"] for e in events]
    assert "model_download_start" in event_types, f"missing start: {event_types}"
    assert "model_download_done" in event_types, f"missing done: {event_types}"
    assert "model_download_error" not in event_types, f"unexpected error: {events}"
    bytes_events = [e for e in events if e["event"] == "model_download_bytes"]
    assert bytes_events, "no bytes events emitted"
    pcts = [e["progress"] for e in bytes_events]
    assert any(p > 0 for p in pcts), f"progress ficou todo em 0: {pcts}"
    start_event = next(e for e in events if e["event"] == "model_download_start")
    done_event = next(e for e in events if e["event"] == "model_download_done")
    assert start_event["progress"] == 0
    assert done_event["progress"] == 100
    print(
        f"PASS: {len(events)} events, bytes phase {min(pcts)}% -> {max(pcts)}%, "
        f"start={start_event['progress']}, done={done_event['progress']}"
    )


def test_missing_revision_triggers_failure() -> None:
    """Asset sem revision deve gerar model_download_error + failures=1."""
    unpinned = ModelAsset(
        key="unpinned",
        label="Unpinned",
        repo_id="x/y",
        purpose="asr",
        estimated_gb=0.001,
        revision=None,
    )
    events: list[dict] = []

    def cb(detail):
        events.append(dict(detail))

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(model_manager, "get_required_models", return_value=[unpinned]), \
             patch.object(model_manager.runtime, "model_cache_dir", return_value=Path(tmp)), \
             patch.object(model_manager.runtime, "apply_secure_hf_environment"):
            failures = model_manager.download_required_models(
                token="fake-token",
                progress_callback=cb,
            )
    assert failures == 1, f"expected 1 failure (missing revision), got {failures}"
    err_events = [e for e in events if e["event"] == "model_download_error"]
    assert err_events, "missing model_download_error event"
    assert "revision" in err_events[0]["message"].lower()
    print("PASS: modelo sem revision pinada gera erro explicito")


if __name__ == "__main__":
    test_full_progress_sequence()
    test_missing_revision_triggers_failure()
    print("\nOK: unit_download_required_models")
