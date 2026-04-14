"""PyInstaller hook for whisperx."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("whisperx")
datas = collect_data_files("whisperx")
