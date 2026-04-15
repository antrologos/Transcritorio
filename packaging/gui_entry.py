"""Frozen entry point for the Transcritorio GUI.

PyInstaller cannot handle relative imports when using the module's
review_studio_qt.py directly (it has imports from the package).
This wrapper uses an absolute import of the main() function.
"""
from __future__ import annotations

import sys

from transcribe_pipeline.review_studio_qt import main

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        from PySide6.QtWidgets import QApplication
        app = QApplication(sys.argv)
        from transcribe_pipeline.review_studio_qt import ReviewStudioWindow  # noqa: F401
        print("  OK: GUI imports and Qt initialization successful")
        sys.exit(0)
    sys.exit(main())
