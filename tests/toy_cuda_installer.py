"""Smoke test for cuda_installer module.

Verifies:
- Module imports cleanly.
- install_dir() returns a Path.
- install_dir_writable() returns a bool (and truthy under tempdir).
- download_and_extract() progress callback is invoked with the expected
  message/percent shape when given a tiny in-memory zip.
"""
from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline import cuda_installer  # noqa: E402


def test_install_dir_is_path() -> None:
    p = cuda_installer.install_dir()
    assert isinstance(p, Path), f"install_dir() deve retornar Path, got {type(p)}"
    print(f"PASS: install_dir() -> {p}")


def test_install_dir_writable_bool() -> None:
    result = cuda_installer.install_dir_writable()
    assert isinstance(result, bool)
    print(f"PASS: install_dir_writable() -> {result}")


def test_download_and_extract_progress() -> None:
    # Fake zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "oi")
        zf.writestr("sub/world.txt", "mundo")
    zip_bytes = buf.getvalue()

    class FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._pos = 0
            self.headers = {"Content-Length": str(len(data))}
        def read(self, n: int = -1) -> bytes:
            if n < 0 or n >= len(self._data) - self._pos:
                chunk = self._data[self._pos:]
                self._pos = len(self._data)
            else:
                chunk = self._data[self._pos:self._pos + n]
                self._pos += n
            return chunk
        def close(self) -> None:
            pass

    events: list[tuple[str, int]] = []

    def _progress(msg: str, pct: int) -> None:
        events.append((msg, pct))

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(cuda_installer, "install_dir", return_value=Path(tmpdir)), \
             patch("urllib.request.urlopen", return_value=FakeResponse(zip_bytes)):
            cuda_installer.download_and_extract(
                version="0.0.0-test",
                progress_callback=_progress,
            )

        extracted_a = Path(tmpdir) / "hello.txt"
        extracted_b = Path(tmpdir) / "sub" / "world.txt"
        assert extracted_a.read_text(encoding="utf-8") == "oi"
        assert extracted_b.read_text(encoding="utf-8") == "mundo"

    assert events, "progress_callback nunca foi chamado"
    last_msg, last_pct = events[-1]
    assert last_pct == 100, f"progresso final deve ser 100, got {last_pct}"
    assert any("Baixando" in m for m, _ in events), "deve ter fase de download"
    assert any("Extraindo" in m for m, _ in events), "deve ter fase de extracao"
    print(f"PASS: download_and_extract() emitiu {len(events)} eventos, finalizou em 100%")


if __name__ == "__main__":
    test_install_dir_is_path()
    test_install_dir_writable_bool()
    test_download_and_extract_progress()
    print("\nOK: cuda_installer smoke test")
