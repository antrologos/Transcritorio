"""Frozen entry point for the Transcritorio GUI.

PyInstaller cannot handle relative imports when using the module's
review_studio_qt.py directly (it has imports from the package).
This wrapper uses an absolute import of the main() function.
"""
from __future__ import annotations

import sys

from transcribe_pipeline.review_studio_qt import main

if __name__ == "__main__":
    sys.exit(main())
