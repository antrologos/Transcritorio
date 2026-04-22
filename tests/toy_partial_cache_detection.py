"""Toy test for _snapshot_has_weights and has_partial_cache.

Validates the fix for the 'config.json-only' false-positive where
any(path.iterdir()) returned True before any real model blob landed.

Run with: python -B tests/toy_partial_cache_detection.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline import model_manager  # noqa: E402


def test_empty_dir_has_no_weights() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert model_manager._snapshot_has_weights(Path(tmp)) is False
    print("PASS: dir vazio -> sem weights")


def test_only_config_json_is_not_weights() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        cfg.write_text('{"arch":"whisper"}', encoding="utf-8")
        (Path(tmp) / "tokenizer.json").write_text("{}", encoding="utf-8")
        (Path(tmp) / "refs.txt").write_text("abc", encoding="utf-8")
        assert model_manager._snapshot_has_weights(Path(tmp)) is False, (
            "arquivos < 100 KB nao devem contar como weights"
        )
    print("PASS: so config/tokenizer nao conta como pronto")


def test_blob_over_threshold_counts_as_weights() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / "model.safetensors"
        blob.write_bytes(b"x" * (200 * 1024))  # 200 KB > 100 KB threshold
        assert model_manager._snapshot_has_weights(Path(tmp)) is True
    print("PASS: blob >= 100 KB conta como weights")


def test_has_partial_cache_with_config_only() -> None:
    """Simula situacao do Lucas: HF escreveu config.json mas caiu antes do blob."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        # Mock get_required_models to return one fake asset
        fake_asset = model_manager.ModelAsset(
            key="fake",
            label="Fake",
            repo_id="fakeorg/fakemodel",
            purpose="asr",
            estimated_gb=1.0,
        )
        repo_cache = cache / "models--fakeorg--fakemodel"
        snapshot_dir = repo_cache / "snapshots" / "rev123"
        snapshot_dir.mkdir(parents=True)
        # Only a tiny config file — no blobs
        (snapshot_dir / "config.json").write_text('{"x":1}', encoding="utf-8")
        refs_main = repo_cache / "refs" / "main"
        refs_main.parent.mkdir(parents=True, exist_ok=True)
        refs_main.write_text("rev123", encoding="utf-8")
        with patch.object(
            model_manager, "get_required_models", return_value=[fake_asset]
        ), patch.object(
            model_manager.runtime, "model_cache_dir", return_value=cache
        ):
            assert model_manager.has_partial_cache() is True, (
                "cache com so config.json deve ser partial"
            )
            assert model_manager.all_required_models_cached() is False, (
                "cache incompleto nao deve virar 'ready'"
            )
            # Agora simula download completo (adiciona blob)
            (snapshot_dir / "model.safetensors").write_bytes(
                b"x" * (500 * 1024)
            )
            assert model_manager.has_partial_cache() is False, (
                "com blob real, deve deixar de ser partial"
            )
            assert model_manager.all_required_models_cached() is True, (
                "com blob >= 100 KB, deve virar 'ready'"
            )
    print("PASS: has_partial_cache distingue config-only de completo")


def test_empty_cache_is_not_partial() -> None:
    """Ausencia total de cache nao e 'partial' — e 'nunca comecou'."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        fake_asset = model_manager.ModelAsset(
            key="fake", label="Fake", repo_id="a/b", purpose="asr", estimated_gb=1.0,
        )
        with patch.object(
            model_manager, "get_required_models", return_value=[fake_asset]
        ), patch.object(
            model_manager.runtime, "model_cache_dir", return_value=cache
        ):
            assert model_manager.has_partial_cache() is False, (
                "cache inexistente nao e partial"
            )
    print("PASS: cache inexistente nao triggers 'retomar'")


if __name__ == "__main__":
    test_empty_dir_has_no_weights()
    test_only_config_json_is_not_weights()
    test_blob_over_threshold_counts_as_weights()
    test_has_partial_cache_with_config_only()
    test_empty_cache_is_not_partial()
    print("\nOK: toy_partial_cache_detection")
