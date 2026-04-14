"""PyInstaller hook for pyannote (lazy-imported in diarization.py)."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("pyannote")
    + collect_submodules("pyannote.audio")
    + collect_submodules("pyannote.core")
    + collect_submodules("pyannote.pipeline")
)
datas = (
    collect_data_files("pyannote.audio")
    + collect_data_files("pyannote.pipeline")
)
