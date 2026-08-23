"""Toy test para model_manager._snapshot_has_weights().

Bug original: qualquer blob >= 100 KB contava como "peso" — inclusive
tokenizer.json (~2.4 MB) e downloads parciais *.incomplete — marcando modelo
quebrado como pronto e desabilitando o fluxo de retomada de download.

Regra corrigida: blob regular >= 4 MB, ignorando *.incomplete.

Depende apenas de model_manager ser importavel (sem torch/pyannote no import).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.model_manager import _snapshot_has_weights


def make_repo(root: Path, blobs: dict[str, int]) -> Path:
    """Cria layout HF: models--org--repo/{blobs,snapshots/sha}/ e retorna o snapshot dir."""
    repo = root / "models--org--repo"
    blobs_dir = repo / "blobs"
    snap = repo / "snapshots" / "abc123"
    blobs_dir.mkdir(parents=True)
    snap.mkdir(parents=True)
    for name, size in blobs.items():
        (blobs_dir / name).write_bytes(b"\0" * size)
    return snap


MB = 1024 * 1024

# 1. Repo so com metadados pequenos (config, tokenizer 2.4MB) -> SEM peso
with tempfile.TemporaryDirectory() as tmp:
    snap = make_repo(Path(tmp), {"cfg": 2_000, "tokenizerblob": int(2.4 * MB)})
    assert not _snapshot_has_weights(snap), "tokenizer 2.4MB nao pode contar como peso"
print("PASS: tokenizer/config nao contam como peso")

# 2. Blob parcial *.incomplete de 500 MB -> SEM peso (retomada deve reativar)
with tempfile.TemporaryDirectory() as tmp:
    snap = make_repo(Path(tmp), {"bighash.incomplete": 8 * MB})
    assert not _snapshot_has_weights(snap), "*.incomplete nao pode contar como peso"
print("PASS: blob .incomplete nao conta como peso")

# 3. Peso real (>= 4 MB regular) -> COM peso
with tempfile.TemporaryDirectory() as tmp:
    snap = make_repo(Path(tmp), {"cfg": 2_000, "weighthash": 5 * MB})
    assert _snapshot_has_weights(snap), "peso regular de 5MB deve contar"
print("PASS: peso real de 5MB conta")

# 4. Mistura: incomplete grande + metadados, sem peso completo -> SEM peso
with tempfile.TemporaryDirectory() as tmp:
    snap = make_repo(Path(tmp), {"a.incomplete": 100 * MB, "tok": 2 * MB})
    assert not _snapshot_has_weights(snap), "parcial+metadados nao e modelo pronto"
print("PASS: parcial grande + metadados nao e modelo pronto")

# 5. path None / blobs ausente -> False sem crash
assert not _snapshot_has_weights(None)
with tempfile.TemporaryDirectory() as tmp:
    orphan = Path(tmp) / "models--x--y" / "snapshots" / "sha"
    orphan.mkdir(parents=True)
    assert not _snapshot_has_weights(orphan)
print("PASS: None/blobs ausente -> False")

print("PASS: toy_snapshot_has_weights")
