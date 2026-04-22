"""Marquee-until-first-byte progress bar controller.

Qt-agnostic state machine: starts a progress bar in indeterminate/marquee
mode (range 0-0) and switches to determinate (range 0-100) on the first
percent > 0. Prevents the UX of a stuck 0% bar while HuggingFace
snapshot_download is in a pre-byte phase (auth, metadata resolution, DNS).

Decoupled from PySide6 so it can be unit-tested without a QApplication.
The caller passes any object exposing setRange(a, b) and setValue(v).
"""
from __future__ import annotations

from typing import Protocol


class _ProgressBarLike(Protocol):
    def setRange(self, a: int, b: int) -> None: ...
    def setValue(self, v: int) -> None: ...


class ProgressBarController:
    def __init__(self) -> None:
        self._determinate = False

    def start(self, bar: _ProgressBarLike) -> None:
        bar.setRange(0, 0)
        self._determinate = False

    def update(self, bar: _ProgressBarLike, percent: int, message: str = "") -> None:
        clamped = max(0, min(100, int(percent)))
        if clamped > 0 and not self._determinate:
            bar.setRange(0, 100)
            self._determinate = True
        if self._determinate:
            bar.setValue(clamped)
