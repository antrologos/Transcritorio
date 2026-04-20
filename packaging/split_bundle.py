"""Split a PyInstaller variant=full bundle into base (CPU) + cuda_pack.

Uso:
    python packaging/split_bundle.py dist/Transcritorio cuda_pack

Le o dist/ produzido pelo PyInstaller com variant=full (inclui CUDA
torch_cuda, cudnn, cublas, etc.) e separa em dois caminhos:

    dist/Transcritorio/          — arquivos da base (CPU-only)
    cuda_pack/                   — arquivos CUDA (para zip / download-on-demand)

A divisao usa `bundle_filter.should_exclude_entry(file, "cpu")`: se
retorna True, o arquivo e movido pra cuda_pack (+ registra o relative
path no manifest).

Ao final, produz:
    cuda_pack/FILES.manifest     — lista dos paths relativos (um por linha)
    dist/Transcritorio/          — base sem os arquivos CUDA

Se o cuda_pack for zipado e extraido sobre dist/, restaura o bundle full.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGING_DIR))

from bundle_filter import should_exclude_entry  # noqa: E402


def split_bundle(dist_dir: Path, pack_dir: Path) -> tuple[int, int]:
    """Move arquivos CUDA de dist_dir/ para pack_dir/.

    Retorna (moved_count, total_bytes_moved).
    """
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"dist nao e diretorio: {dist_dir}")
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    total_bytes = 0
    for src in dist_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(dist_dir)
        # variant="cpu" retorna True pros arquivos CUDA que queremos extrair
        if should_exclude_entry(str(rel), variant="cpu"):
            # Pula os que tambem seriam excluidos em variant="full"
            # (.lib, .h, dev exes) — nao fazem parte do dist de qualquer
            # jeito, mas por seguranca.
            if should_exclude_entry(str(rel), variant="full"):
                continue
            dst = pack_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            total_bytes += src.stat().st_size
            shutil.move(str(src), str(dst))
            manifest.append(str(rel).replace("\\", "/"))
    # Escreve manifest
    manifest_path = pack_dir / "FILES.manifest"
    manifest_path.write_text("\n".join(sorted(manifest)) + "\n", encoding="utf-8")
    return len(manifest), total_bytes


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    dist_dir = Path(argv[1]).resolve()
    pack_dir = Path(argv[2]).resolve()
    count, total = split_bundle(dist_dir, pack_dir)
    mb = total / (1024 * 1024)
    print(f"Split: {count} arquivos movidos ({mb:.1f} MB) -> {pack_dir}")
    # Imprime resumo dos 10 maiores
    sizes = []
    for f in pack_dir.rglob("*"):
        if f.is_file() and f.name != "FILES.manifest":
            sizes.append((f.stat().st_size, f.relative_to(pack_dir)))
    sizes.sort(reverse=True)
    print(f"Top 10 maiores no cuda_pack:")
    for s, rel in sizes[:10]:
        print(f"  {s/1024/1024:8.1f} MB  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
