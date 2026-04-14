"""Frozen entry point for the Transcritorio CLI.

PyInstaller cannot handle relative imports in __main__.py,
so this wrapper uses an absolute import.
"""
from __future__ import annotations

import sys

from transcribe_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
