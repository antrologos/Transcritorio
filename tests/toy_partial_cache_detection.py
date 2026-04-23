"""Toy test for _snapshot_has_weights and has_partial_cache.

Cobre o layout HF real (blobs/ + snapshots/{sha}/ + refs/main) em ambas
variantes de populacao do snapshot_dir:
- **Symlink**: Linux/Mac sempre; Windows com Developer Mode.
- **Copy**: Windows sem Dev Mode (fallback via shutil.copy2 no nosso
  _place_blob_in_snapshot).

O bug do 2026-04-23: implementacao anterior fazia `path.rglob('*')`
sobre o snapshot dir. No frozen bundle PyInstaller no Windows, rglob
nao enumerava os symlinks corretamente, entao has_weights voltava False
apesar dos blobs de GB estarem na pasta irma blobs/. UI mostrava
'Modelos ausentes ou incompletos apos o download' mesmo com cache
integro.

Fix: checar direto o dir blobs/ (arquivos regulares, nunca symlinks).

Run with: python -B tests/toy_partial_cache_detection.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline import model_manager  # noqa: E402

_WEIGHT_THRESHOLD = model_manager._WEIGHT_BLOB_MIN_BYTES  # 100 KB


def _make_hf_cache_layout(
    cache_root: Path,
    repo_id: str,
    revision: str,
    blobs: dict[str, bytes],
    *,
    use_symlinks: bool = True,
) -> Path:
    """Cria um diretorio com layout HF hub valido.

    `blobs` maps filename-in-snapshot -> blob content bytes. For each
    entry, writes the content to `blobs/{etag}` (etag = simple hash) AND
    creates the corresponding snapshot entry (symlink or copy).

    Returns the snapshot dir path (for passing to _snapshot_has_weights).
    """
    import hashlib
    repo_dir = cache_root / ("models--" + repo_id.replace("/", "--"))
    blobs_dir = repo_dir / "blobs"
    snap_dir = repo_dir / "snapshots" / revision
    blobs_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in blobs.items():
        etag = hashlib.sha256(content).hexdigest()
        blob_path = blobs_dir / etag
        blob_path.write_bytes(content)
        snap_entry = snap_dir / filename
        if use_symlinks:
            try:
                rel = os.path.relpath(blob_path, snap_entry.parent)
                snap_entry.symlink_to(rel)
                continue
            except (OSError, NotImplementedError):
                pass
        # Copy fallback (Windows sem dev mode)
        snap_entry.write_bytes(content)
    # refs/main
    refs = repo_dir / "refs" / "main"
    refs.parent.mkdir(parents=True, exist_ok=True)
    refs.write_text(revision, encoding="utf-8")
    return snap_dir


def test_empty_snapshot_dir_no_blobs_has_no_weights() -> None:
    """Caso base: dir novo, nem blobs/ existe."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "models--org--repo" / "snapshots" / "r"
        snap.mkdir(parents=True)
        # Nao criamos blobs/ sibling.
        assert model_manager._snapshot_has_weights(snap) is False
    print("PASS: sem blobs/ sibling -> has_weights=False")


def test_only_small_blobs_has_no_weights() -> None:
    """HF escreveu config.json + tokenizer (nenhum >= 100 KB) mas nao o peso."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = _make_hf_cache_layout(
            Path(tmp),
            "fakeorg/fakemodel",
            "rev-small",
            {
                "config.json": b'{"arch":"whisper"}' * 10,  # ~170 bytes
                "tokenizer.json": b"{}",
                "README.md": b"hello",
            },
        )
        assert model_manager._snapshot_has_weights(snap) is False
    print("PASS: so arquivos pequenos em blobs/ -> False")


def test_weight_blob_via_symlink_detected() -> None:
    """Layout Linux/Mac/Windows+devmode: snapshot tem symlink pro blob."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = _make_hf_cache_layout(
            Path(tmp),
            "fakeorg/fakemodel",
            "rev-symlink",
            {
                "config.json": b"{}",
                "model.bin": b"x" * (200 * 1024),  # 200 KB > threshold
            },
            use_symlinks=True,
        )
        assert model_manager._snapshot_has_weights(snap) is True, (
            "weight blob >= 100 KB deve ser detectado mesmo com snapshot via symlink"
        )
    print("PASS: blob via symlink detectado")


def test_weight_blob_via_copy_detected() -> None:
    """Layout Windows sem dev mode: snapshot tem COPIA do blob, nao symlink."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = _make_hf_cache_layout(
            Path(tmp),
            "fakeorg/fakemodel",
            "rev-copy",
            {
                "config.json": b"{}",
                "model.bin": b"x" * (200 * 1024),
            },
            use_symlinks=False,
        )
        assert model_manager._snapshot_has_weights(snap) is True
    print("PASS: blob via copy detectado (Windows sem devmode)")


def test_threshold_exactly_at_100k() -> None:
    """Exatamente 100 KB conta; 99 KB nao conta."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = _make_hf_cache_layout(
            Path(tmp), "a/b", "r1",
            {"model.bin": b"x" * (100 * 1024)},
            use_symlinks=False,
        )
        assert model_manager._snapshot_has_weights(snap) is True

    with tempfile.TemporaryDirectory() as tmp:
        snap = _make_hf_cache_layout(
            Path(tmp), "a/b", "r2",
            {"model.bin": b"x" * (99 * 1024)},
            use_symlinks=False,
        )
        assert model_manager._snapshot_has_weights(snap) is False
    print("PASS: threshold 100 KB exato")


def test_has_partial_cache_distingue_config_only_vs_complete() -> None:
    """Simula o caso real do usuario: HF comecou download, escreveu so
    config.json, entao cache eh PARTIAL (precisa retomar) ate blob aparecer."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        fake = model_manager.ModelAsset(
            key="fake", label="Fake", repo_id="fakeorg/fakemodel",
            purpose="asr", estimated_gb=1.0,
            revision="aa" * 20,
        )
        # Escreve so config — partial state
        _make_hf_cache_layout(
            cache, fake.repo_id, fake.revision,
            {"config.json": b"{}"}, use_symlinks=False,
        )
        with patch.object(model_manager, "get_required_models", return_value=[fake]), \
             patch.object(model_manager.runtime, "model_cache_dir", return_value=cache):
            assert model_manager.has_partial_cache() is True, "config-only deve ser partial"
            assert model_manager.all_required_models_cached() is False
            # Agora adiciona blob grande (completion)
            _make_hf_cache_layout(
                cache, fake.repo_id, fake.revision,
                {"config.json": b"{}", "model.bin": b"x" * (500 * 1024)},
                use_symlinks=False,
            )
            assert model_manager.has_partial_cache() is False
            assert model_manager.all_required_models_cached() is True
    print("PASS: has_partial_cache distingue partial vs complete")


def test_empty_cache_not_partial() -> None:
    """Sem cache nenhum != partial (nao e 'retomar', e 'nunca comecou')."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        fake = model_manager.ModelAsset(
            key="fake", label="Fake", repo_id="x/y", purpose="asr",
            estimated_gb=1.0, revision="bb" * 20,
        )
        with patch.object(model_manager, "get_required_models", return_value=[fake]), \
             patch.object(model_manager.runtime, "model_cache_dir", return_value=cache):
            assert model_manager.has_partial_cache() is False
    print("PASS: cache ausente nao e partial")


if __name__ == "__main__":
    test_empty_snapshot_dir_no_blobs_has_no_weights()
    test_only_small_blobs_has_no_weights()
    test_weight_blob_via_symlink_detected()
    test_weight_blob_via_copy_detected()
    test_threshold_exactly_at_100k()
    test_has_partial_cache_distingue_config_only_vs_complete()
    test_empty_cache_not_partial()
    print("\nOK: toy_partial_cache_detection")
