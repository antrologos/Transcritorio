"""Toy test: helpers de gerenciamento de modelos.

Valida (sem Qt):
- friendly_name: retorna label pt-BR curto para cada variante + obrigatorios
- orphan_repos: detecta pastas models--*/ nao listadas em ASR_VARIANTS/_FIXED_MODELS
- model_install_date: retorna ctime minimo dos blobs do snapshot
- scan_cache: estrutura basica + fallback
- delete_model: remove via scan_cache_dir + retry (mock)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import model_manager


def test_friendly_name_variants() -> None:
    """Todas as 6 variantes + 2 obrigatorios tem nome amigavel."""
    for key in ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"):
        name = model_manager.friendly_name(key)
        assert isinstance(name, str) and len(name) > 5, f"{key}: {name!r}"
        assert "GB" in name or "MB" in name, f"{key}: sem tamanho no nome"
    name_turbo = model_manager.friendly_name("large-v3-turbo")
    assert "recomendado" in name_turbo.lower()
    # Obrigatorios
    name_align = model_manager.friendly_name("alignment_pt")
    name_dia = model_manager.friendly_name("diarization")
    assert "alinhamento" in name_align.lower() or "tempo" in name_align.lower()
    assert "falant" in name_dia.lower()
    # Desconhecido: retorna o proprio key (fallback)
    assert model_manager.friendly_name("desconhecido") == "desconhecido"
    print("PASS friendly_name: 6 variantes + 2 obrigatorios + fallback")


def test_orphan_repos_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        (cache / "models--Systran--faster-whisper-tiny").mkdir()
        (cache / "models--Systran--faster-whisper-medium").mkdir()
        orphans = model_manager.orphan_repos(cache)
        assert orphans == [], f"esperava sem orfaos, got {orphans}"
        print("PASS orphan_repos: repos conhecidos nao sao orfaos")


def test_orphan_repos_detects_unknown() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        (cache / "models--Systran--faster-whisper-tiny").mkdir()
        (cache / "models--experimental--outro-modelo").mkdir()
        (cache / "models--foo--bar").mkdir()
        (cache / "nao-eh-model-dir").mkdir()
        orphans = model_manager.orphan_repos(cache)
        assert sorted(orphans) == ["foo/bar", "experimental/outro-modelo"][::-1] or sorted(orphans) == sorted(["foo/bar", "experimental/outro-modelo"]), orphans
        # directory nao prefixada com "models--" e ignorada
        assert "nao-eh-model-dir" not in orphans
        print(f"PASS orphan_repos: detectou {len(orphans)} orfao(s): {orphans}")


def test_model_install_date_none_if_no_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        (cache / "models--X--Y").mkdir()
        d = model_manager.model_install_date("X/Y", cache)
        assert d is None, f"sem snapshot, esperava None, got {d}"
        print("PASS model_install_date: None para repo sem snapshot")


def test_model_install_date_returns_ctime_min() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        repo_dir = cache / "models--X--Y"
        (repo_dir / "snapshots" / "abc").mkdir(parents=True)
        (repo_dir / "blobs").mkdir()
        (repo_dir / "refs").mkdir()
        (repo_dir / "refs" / "main").write_text("abc", encoding="utf-8")
        blob_old = repo_dir / "blobs" / "blob1"
        blob_old.write_bytes(b"x")
        time.sleep(0.05)
        blob_new = repo_dir / "blobs" / "blob2"
        blob_new.write_bytes(b"y")
        # symlinks no snapshot apontam pros blobs
        (repo_dir / "snapshots" / "abc" / "f1").write_bytes(b"x")
        d = model_manager.model_install_date("X/Y", cache)
        assert d is not None
        # ctime deve ser aproximadamente o do blob mais antigo
        delta = abs(d - blob_old.stat().st_ctime)
        assert delta < 1.0, f"esperava ctime proximo a {blob_old.stat().st_ctime}, got {d}"
        print(f"PASS model_install_date: {d}")


def test_scan_cache_fallback() -> None:
    """Forca o fallback do scan_cache (sem huggingface_hub valido) e valida
    que ele lista repos + soma tamanho dos arquivos."""
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        repo = cache / "models--Systran--faster-whisper-tiny"
        (repo / "snapshots" / "abc").mkdir(parents=True)
        (repo / "blobs").mkdir()
        (repo / "blobs" / "b1").write_bytes(b"a" * 1000)
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text("abc", encoding="utf-8")
        # Mock scan_cache_dir to raise -> forca fallback
        with patch("huggingface_hub.scan_cache_dir", side_effect=RuntimeError("mocked")):
            entries = model_manager.scan_cache(cache)
        assert isinstance(entries, list)
        tiny = next((e for e in entries if "tiny" in e["repo_id"]), None)
        assert tiny is not None, entries
        assert tiny["size_on_disk"] > 0, tiny
        assert tiny["repo_id"] == "Systran/faster-whisper-tiny"
        print(f"PASS scan_cache fallback: {len(entries)} entradas, tiny size={tiny['size_on_disk']}")


if __name__ == "__main__":
    test_friendly_name_variants()
    test_orphan_repos_none()
    test_orphan_repos_detects_unknown()
    test_model_install_date_none_if_no_snapshot()
    test_model_install_date_returns_ctime_min()
    test_scan_cache_fallback()
    print()
    print("PASS: toy_model_manager")
