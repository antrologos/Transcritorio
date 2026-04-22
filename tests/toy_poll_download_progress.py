"""Toy test for _poll_download_progress — verifies progress rises when files grow.

Reproduces the bug scenario and validates the fix:
- Spawns a 'fake downloader' thread that writes bytes to a cache dir in chunks.
- Runs _poll_download_progress against the same cache dir.
- Asserts the callback receives progress > 0 and that progress is monotonic.

Run with: python -B tests/toy_poll_download_progress.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.model_manager import _poll_download_progress  # noqa: E402


def test_poll_sees_growing_file() -> None:
    events: list[dict] = []
    events_lock = threading.Lock()

    def callback(detail: dict) -> None:
        with events_lock:
            events.append(dict(detail))

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        stop_event = threading.Event()
        writer_done = threading.Event()

        def writer() -> None:
            blob = cache / "blob.bin"
            try:
                for _ in range(30):
                    if stop_event.is_set():
                        break
                    with blob.open("ab") as fh:
                        fh.write(b"x" * 100_000)  # 100 KB per tick
                    time.sleep(0.1)
            finally:
                writer_done.set()

        w = threading.Thread(target=writer, daemon=True)
        w.start()

        p = threading.Thread(
            target=_poll_download_progress,
            kwargs=dict(
                cache_dir=cache,
                estimated_bytes=3_000_000,
                start_pct=0,
                end_pct=100,
                label="test-model",
                progress_callback=callback,
                stop_event=stop_event,
                interval=0.2,
            ),
            daemon=True,
        )
        p.start()

        writer_done.wait(timeout=5)
        time.sleep(0.4)
        stop_event.set()
        p.join(timeout=2)

    assert events, "callback nunca foi chamado"
    progresses = [e["progress"] for e in events]
    assert any(pct > 0 for pct in progresses), (
        f"progress nunca cresceu acima de 0: {progresses}"
    )
    last_pct = progresses[-1]
    assert last_pct >= 70, (
        f"progress final muito baixo (esperado >= 70): {last_pct}"
    )
    for i in range(1, len(progresses)):
        assert progresses[i] >= progresses[i - 1], (
            f"regressao em progresso: {progresses}"
        )
    assert all(e["event"] == "model_download_bytes" for e in events)
    assert all("test-model" in e["message"] for e in events)
    print(
        f"PASS: {len(events)} eventos, "
        f"progresso {progresses[0]}% -> {last_pct}%, monotonico"
    )


def test_poll_reports_zero_when_no_bytes_land() -> None:
    """Validates the bug scenario: no writes to cache -> progress stays 0."""
    events: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        stop_event = threading.Event()
        p = threading.Thread(
            target=_poll_download_progress,
            kwargs=dict(
                cache_dir=cache,
                estimated_bytes=3_000_000_000,
                start_pct=0,
                end_pct=100,
                label="stuck",
                progress_callback=lambda d: events.append(dict(d)),
                stop_event=stop_event,
                interval=0.1,
            ),
            daemon=True,
        )
        p.start()
        time.sleep(0.5)
        stop_event.set()
        p.join(timeout=2)

    assert events, "callback never fired during stall"
    assert all(e["progress"] == 0 for e in events), (
        f"progress deve ficar 0 quando nada escreve: {events}"
    )
    print(
        f"PASS: stall reproduz bug do usuario "
        f"({len(events)} events todos em 0%)"
    )


if __name__ == "__main__":
    test_poll_sees_growing_file()
    test_poll_reports_zero_when_no_bytes_land()
    print("\nOK: toy_poll_download_progress")
