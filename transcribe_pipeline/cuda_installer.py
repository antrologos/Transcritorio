"""Download-on-demand CUDA pack for Windows NVIDIA acceleration.

Downloads transcritorio-cuda-pack-{version}-win64.zip from the GitHub
Release and extracts it into the app install directory. Called from the
GUI first-launch flow (review_studio_qt._maybe_offer_cuda_install) so the
user sees a native Qt progress bar instead of a silent installer step.
"""
from __future__ import annotations

import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str, int], None]

CUDA_PACK_URL_TEMPLATE = (
    "https://github.com/antrologos/Transcritorio/releases/download/"
    "v{version}/transcritorio-cuda-pack-{version}-win64.zip"
)


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def install_dir_writable() -> bool:
    probe = install_dir() / ".cuda_install_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def download_and_extract(
    version: str,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    def _emit(message: str, percent: int) -> None:
        if progress_callback is not None:
            progress_callback(message, percent)

    def _cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    target = install_dir()
    url = CUDA_PACK_URL_TEMPLATE.format(version=version)
    tmp_zip = Path(tempfile.gettempdir()) / f"transcritorio-cuda-pack-{version}.zip"
    if tmp_zip.exists():
        try:
            tmp_zip.unlink()
        except OSError:
            pass

    _emit("Conectando ao GitHub...", 0)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Transcritorio/cuda-installer"}
    )
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha ao conectar: {exc}") from exc

    try:
        total = int(response.headers.get("Content-Length", 0)) or 0
        downloaded = 0
        chunk_size = 256 * 1024
        with tmp_zip.open("wb") as fh:
            while True:
                if _cancelled():
                    raise RuntimeError("Cancelado pelo usuario")
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded * 70 / total)
                    mb_dl = downloaded // (1024 * 1024)
                    mb_total = total // (1024 * 1024)
                    _emit(f"Baixando: {mb_dl} / {mb_total} MB", pct)
                else:
                    mb_dl = downloaded // (1024 * 1024)
                    _emit(f"Baixando: {mb_dl} MB", 35)
    finally:
        response.close()

    _emit("Extraindo arquivos...", 70)
    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            total_entries = max(len(names), 1)
            for i, name in enumerate(names):
                if _cancelled():
                    raise RuntimeError("Cancelado pelo usuario")
                zf.extract(name, target)
                if i % 20 == 0 or i == len(names) - 1:
                    pct = 70 + int((i + 1) * 30 / total_entries)
                    _emit(f"Extraindo: {i + 1} / {len(names)}", pct)
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass

    _emit("Concluido", 100)
