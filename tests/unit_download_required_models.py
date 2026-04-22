"""Unit test for download_required_models — HuggingFace snapshot_download mocked.

Validates the full progress-event sequence (start -> bytes -> done) with a
fake snapshot_download that writes bytes to the cache dir in chunks. Exercises
the integration of _poll_download_progress with the outer download loop.

Skipped if huggingface_hub is not importable (CI stub environment).

Run with: python -B tests/unit_download_required_models.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import huggingface_hub  # noqa: F401
except ImportError:
    print("SKIP: huggingface_hub nao instalado nesse venv")
    sys.exit(0)

from transcribe_pipeline import model_manager  # noqa: E402
from transcribe_pipeline.model_manager import ModelAsset  # noqa: E402


def _fake_snapshot_download(writer_bytes: int, chunks: int, chunk_delay_s: float):
    """Returns a stub snapshot_download that writes *writer_bytes* across *chunks*."""
    def _impl(**kwargs):
        cache_dir = Path(kwargs["cache_dir"])
        repo_id = kwargs["repo_id"]
        # Mirror HF cache layout: models--{org}--{name}/blobs/<hash>
        cache_subdir = cache_dir / f"models--{repo_id.replace('/', '--')}" / "blobs"
        cache_subdir.mkdir(parents=True, exist_ok=True)
        blob = cache_subdir / "abc123"
        chunk_size = max(1, writer_bytes // chunks)
        with blob.open("ab") as fh:
            for _ in range(chunks):
                fh.write(b"x" * chunk_size)
                fh.flush()
                time.sleep(chunk_delay_s)
        return str(cache_subdir.parent / "snapshots" / "deadbeef")
    return _impl


def test_full_progress_sequence() -> None:
    fake_asset = ModelAsset(
        key="test-variant",
        label="Test Model",
        repo_id="fakeorg/fakemodel",
        purpose="asr",
        gated=False,
        estimated_gb=0.005,  # 5 MB
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
             patch("huggingface_hub.snapshot_download",
                   side_effect=_fake_snapshot_download(
                       writer_bytes=5_000_000, chunks=5, chunk_delay_s=0.3)):
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
    assert bytes_events, "no bytes events emitted (poller nao pegou escrita)"
    pcts = [e["progress"] for e in bytes_events]
    assert any(p > 0 for p in pcts), (
        f"progress ficou todo em 0 — regressao do bug do usuario: {pcts}"
    )
    start_event = next(e for e in events if e["event"] == "model_download_start")
    done_event = next(e for e in events if e["event"] == "model_download_done")
    assert start_event["progress"] == 0, f"start pct: {start_event['progress']}"
    assert done_event["progress"] == 100, f"done pct: {done_event['progress']}"
    print(
        f"PASS: {len(events)} events, bytes phase {min(pcts)}% -> {max(pcts)}%, "
        f"start={start_event['progress']}, done={done_event['progress']}"
    )


if __name__ == "__main__":
    test_full_progress_sequence()
    print("\nOK: unit_download_required_models")
