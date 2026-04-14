"""PyInstaller hook for asteroid_filterbanks (pyannote dependency)."""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("asteroid_filterbanks")
