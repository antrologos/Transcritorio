"""Prepara um cache HF fake com layout real pra rodar `models verify` contra.

Cria pra cada asset em REQUIRED_MODELS (Whisper turbo + alignment + diarization):
  {cache}/models--{org}--{name}/
    blobs/{dummy-hash}        (200 KB dummy weight — acima do threshold 100 KB)
    snapshots/{pinned-sha}/   (com weight.bin como symlink OU copia)
    refs/main                 (pinned-sha)

Uso em release.yml pos-build:
    python tests/prepare_fake_hf_cache.py /tmp/fakecache
    TRANSCRITORIO_MODEL_CACHE=/tmp/fakecache ./Transcritorio-cli models verify

Se `models verify` sair com exit 0 → o FROZEN bundle roda o fluxo
verify_required_models → cached_snapshot_path → _snapshot_has_weights
corretamente contra o layout HF real. Esse eh o gate que pega bugs tipo
o do 2026-04-23 (rglob+symlink no PyInstaller Windows) ANTES de publicar.

Uso extra: `python tests/prepare_fake_hf_cache.py /tmp/fakecache --force-copy`
forca variante copy (simula Windows sem Developer Mode).

Run: python -B tests/prepare_fake_hf_cache.py <cache_dir> [--force-copy]
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.model_manager import (  # noqa: E402
    REQUIRED_MODELS,
    _WEIGHT_BLOB_MIN_BYTES,
)


def _prep_asset(
    cache_root: Path,
    repo_id: str,
    revision: str,
    *,
    force_copy: bool,
) -> dict[str, str]:
    """Cria o layout do asset. Retorna resumo pra log."""
    repo_dir = cache_root / ("models--" + repo_id.replace("/", "--"))
    blobs_dir = repo_dir / "blobs"
    snap_dir = repo_dir / "snapshots" / revision
    blobs_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Dummy weight blob (>= 100 KB threshold)
    dummy_bytes = b"\x00" * (200 * 1024)
    weight_etag = hashlib.sha256(dummy_bytes).hexdigest()
    weight_blob = blobs_dir / weight_etag
    weight_blob.write_bytes(dummy_bytes)

    # Dummy config (< 100 KB, nao conta como weight)
    config_bytes = b'{"dummy":"config"}'
    config_etag = hashlib.sha256(config_bytes).hexdigest()
    config_blob = blobs_dir / config_etag
    config_blob.write_bytes(config_bytes)

    # Snapshot entries (symlink ou copy)
    weight_snap = snap_dir / "model.bin"
    config_snap = snap_dir / "config.json"
    linked = True
    if not force_copy:
        try:
            weight_snap.symlink_to(os.path.relpath(weight_blob, snap_dir))
            config_snap.symlink_to(os.path.relpath(config_blob, snap_dir))
        except (OSError, NotImplementedError):
            linked = False
    else:
        linked = False
    if not linked:
        weight_snap.write_bytes(dummy_bytes)
        config_snap.write_bytes(config_bytes)

    # refs/main
    refs = repo_dir / "refs" / "main"
    refs.parent.mkdir(parents=True, exist_ok=True)
    refs.write_text(revision, encoding="utf-8")

    return {
        "repo": repo_id,
        "revision": revision[:12],
        "variant": "symlink" if linked else "copy",
        "weight_size": str(len(dummy_bytes)),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cache_dir = Path(argv[1]).resolve()
    force_copy = "--force-copy" in argv[2:]

    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"[prepare] cache_dir={cache_dir} force_copy={force_copy}")
    print(f"[prepare] REQUIRED_MODELS = {len(REQUIRED_MODELS)} assets")
    print(f"[prepare] _WEIGHT_BLOB_MIN_BYTES = {_WEIGHT_BLOB_MIN_BYTES}")

    for asset in REQUIRED_MODELS:
        if not asset.revision:
            print(f"[prepare] SKIP {asset.repo_id}: sem revision pinada")
            continue
        summary = _prep_asset(
            cache_dir, asset.repo_id, asset.revision, force_copy=force_copy
        )
        print(
            f"[prepare]   {summary['repo']}@{summary['revision']} "
            f"variant={summary['variant']} weight_size={summary['weight_size']}B"
        )
    print("[prepare] OK — cache fake pronto")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
