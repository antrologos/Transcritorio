"""Toy test: promocao staging->files da Lixeira tolerante ao lock do Dropbox.

Incidente 2026-08-25: staging.rename(files) falhava com WinError 5 (Dropbox
segurando handles). O helper tenta o rename e cai para move arquivo-a-arquivo.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import _promote_staging_to_files
except ImportError as exc:  # PySide6 ausente no CI minimo
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def build_staging(base: Path) -> Path:
    staging = base / "staging"
    (staging / "Audios_X").mkdir(parents=True)
    (staging / "Audios_X" / "a.mp3").write_bytes(b"AAA")
    (staging / "raiz.wav").write_bytes(b"BBBB")
    return staging


# Caminho feliz: rename direto funciona
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    staging = build_staging(base)
    files_dir = base / "files"
    _promote_staging_to_files(staging, files_dir, delays=(0.0,))
    assert not staging.exists()
    assert (files_dir / "Audios_X" / "a.mp3").read_bytes() == b"AAA"
    assert (files_dir / "raiz.wav").read_bytes() == b"BBBB"
print("PASS: rename direto")

# Rename bloqueado (files ja existe -> OSError no Windows) -> fallback
# arquivo a arquivo preservando estrutura
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    staging = build_staging(base)
    files_dir = base / "files"
    files_dir.mkdir()  # forca a falha do rename
    _promote_staging_to_files(staging, files_dir, delays=(0.0,))
    assert not staging.exists()
    assert (files_dir / "Audios_X" / "a.mp3").read_bytes() == b"AAA"
    assert (files_dir / "raiz.wav").read_bytes() == b"BBBB"
print("PASS: fallback arquivo a arquivo")

print("PASS: toy_trash_promote")
