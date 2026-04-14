"""Frozen entry point for the whisperx CLI subprocess.

PyInstaller bundles this as whisperx.exe so that
whisperx_runner.py can invoke it via resolve_executable("whisperx").
"""
from __future__ import annotations

import sys

from whisperx.__main__ import cli

if __name__ == "__main__":
    sys.exit(cli())
