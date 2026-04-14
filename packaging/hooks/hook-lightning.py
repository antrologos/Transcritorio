"""PyInstaller hook for lightning / lightning_fabric (pyannote dependency)."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("lightning") + collect_submodules("lightning_fabric")
datas = collect_data_files("lightning") + collect_data_files("lightning_fabric")
