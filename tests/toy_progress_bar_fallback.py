"""Toy test for the progress bar marquee-until-first-byte controller.

Validates _ProgressBarController (standalone, no Qt) which:
- Starts in marquee mode (range 0-0 = indeterminate animation).
- Switches to determinate (range 0-100) on the first percent > 0.
- Forwards subsequent setValue calls normally.

This is the fallback we add so users don't see a frozen 0% bar while the
HuggingFace downloader is stuck in a pre-byte phase (auth, metadata, DNS).

Run with: python -B tests/toy_progress_bar_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.progress_bar_fallback import ProgressBarController  # noqa: E402


class FakeBar:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def setRange(self, a: int, b: int) -> None:
        self.calls.append(("setRange", a, b))

    def setValue(self, v: int) -> None:
        self.calls.append(("setValue", v))


def test_marquee_starts_and_switches_on_first_nonzero() -> None:
    bar = FakeBar()
    ctrl = ProgressBarController()
    ctrl.start(bar)
    assert ("setRange", 0, 0) in bar.calls
    for _ in range(5):
        ctrl.update(bar, 0, "aguardando")
    assert not any(c[0] == "setValue" for c in bar.calls), (
        f"nao deveria setValue enquanto em marquee: {bar.calls}"
    )
    ctrl.update(bar, 7, "primeiros bytes")
    assert ("setRange", 0, 100) in bar.calls
    assert ("setValue", 7) in bar.calls
    ctrl.update(bar, 42, "metade")
    assert ("setValue", 42) in bar.calls
    print(f"PASS: marquee -> determinate no 1o nonzero. calls={len(bar.calls)}")


def test_update_clamps_percent_to_0_100() -> None:
    bar = FakeBar()
    ctrl = ProgressBarController()
    ctrl.start(bar)
    ctrl.update(bar, -5, "bug upstream")
    # Negative percent should not flip to determinate
    assert not any(c == ("setRange", 0, 100) for c in bar.calls)
    ctrl.update(bar, 150, "bug upstream")
    assert ("setValue", 100) in bar.calls, (
        f"percent >100 deveria clampar a 100: {bar.calls}"
    )
    print("PASS: clamps negative/overflow percents")


def test_subsequent_zeros_after_nonzero_stay_determinate() -> None:
    """Once determinate, don't flip back to marquee even if a zero arrives."""
    bar = FakeBar()
    ctrl = ProgressBarController()
    ctrl.start(bar)
    ctrl.update(bar, 10, "started")
    ctrl.update(bar, 0, "spurious zero")
    range_calls = [c for c in bar.calls if c[0] == "setRange"]
    # Should be exactly [start marquee, switch to 0-100]; no marquee again
    assert range_calls == [("setRange", 0, 0), ("setRange", 0, 100)], (
        f"nao deve voltar a marquee: {range_calls}"
    )
    print("PASS: sticky determinate apos 1o nonzero")


if __name__ == "__main__":
    test_marquee_starts_and_switches_on_first_nonzero()
    test_update_clamps_percent_to_0_100()
    test_subsequent_zeros_after_nonzero_stay_determinate()
    print("\nOK: toy_progress_bar_fallback")
