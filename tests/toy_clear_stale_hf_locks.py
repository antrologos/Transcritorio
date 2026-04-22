"""Toy test for _clear_stale_hf_locks — removes stale HF filelock sentinels.

Reproduces the production bug (2026-04-22): HF downloads deadlock when a
previous crashed session left .lock files in cache/.locks/. This helper
now runs before every download_required_models() to unblock the flow.

Run with: python -B tests/toy_clear_stale_hf_locks.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.model_manager import _clear_stale_hf_locks  # noqa: E402


def test_no_locks_dir_returns_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert _clear_stale_hf_locks(Path(tmp)) == 0
    print("PASS: cache sem .locks/ retorna 0")


def test_empty_locks_dir_returns_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".locks").mkdir()
        assert _clear_stale_hf_locks(Path(tmp)) == 0
    print("PASS: .locks/ vazio retorna 0")


def test_removes_stale_locks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        locks = cache / ".locks" / "models--fake--repo"
        locks.mkdir(parents=True)
        (locks / "a.lock").write_text("pid:1234", encoding="utf-8")
        (locks / "b.lock").write_text("pid:5678", encoding="utf-8")
        (locks / "c.lock").write_text("", encoding="utf-8")
        removed = _clear_stale_hf_locks(cache)
        assert removed == 3, f"esperado 3 locks removidos, got {removed}"
        remaining = list(locks.glob("*.lock"))
        assert not remaining, f"locks restantes: {remaining}"
    print("PASS: 3 stale locks removidos")


def test_nested_lock_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        deep = cache / ".locks" / "models--a--b" / "blobs"
        deep.mkdir(parents=True)
        (deep / "deep.lock").write_text("x", encoding="utf-8")
        (cache / ".locks" / "top.lock").write_text("y", encoding="utf-8")
        removed = _clear_stale_hf_locks(cache)
        assert removed == 2, f"esperado 2, got {removed}"
    print("PASS: rglob encontra locks aninhados")


def test_non_lock_files_preserved() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        locks = cache / ".locks"
        locks.mkdir()
        (locks / "readme.txt").write_text("nao mexer", encoding="utf-8")
        (locks / "a.lock").write_text("pid", encoding="utf-8")
        removed = _clear_stale_hf_locks(cache)
        assert removed == 1
        assert (locks / "readme.txt").exists(), "arquivo nao-lock foi apagado"
    print("PASS: so *.lock e apagado")


if __name__ == "__main__":
    test_no_locks_dir_returns_zero()
    test_empty_locks_dir_returns_zero()
    test_removes_stale_locks()
    test_nested_lock_tree()
    test_non_lock_files_preserved()
    print("\nOK: toy_clear_stale_hf_locks")
