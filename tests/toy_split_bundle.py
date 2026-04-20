"""Toy test: packaging/split_bundle.py

Simula um dist/ PyInstaller em tempdir com arquivos tipicos:
- torch_cuda.dll (CUDA, vai pro pack)
- torch_cpu.dll (base, fica)
- cudnn*.dll (varios; CUDA, vai pro pack)
- Transcritorio.exe (base)
- config.json (base)
- libcudnn.so.9 (Linux CUDA; vai pro pack)

Valida que:
- split_bundle move os arquivos CUDA pro pack_dir
- O manifest tem os paths relativos corretos
- Arquivos nao-CUDA ficam em dist_dir
- Total de bytes movidos e > 0
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))

from split_bundle import split_bundle  # noqa: E402


def _make_file(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_split_windows_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        # Bundle files (variant=full produz isso)
        _make_file(dist / "Transcritorio.exe", 1_000_000)
        _make_file(dist / "transcritorio-cli.exe", 500_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "torch_cuda.dll", 1_000_000_000)  # 1 GB
        _make_file(dist / "_internal" / "torch" / "lib" / "torch_cpu.dll", 245_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn64_9.dll", 50_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn_ops64_9.dll", 120_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cublas64_12.dll", 109_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "c10.dll", 5_000)  # NAO e _cuda
        _make_file(dist / "_internal" / "torch" / "lib" / "c10_cuda.dll", 5_000)  # E
        _make_file(dist / "_internal" / "config.json", 100)

        pack = Path(tmp) / "cuda_pack"
        count, total = split_bundle(dist, pack)

        # Deve ter movido: torch_cuda, cudnn64, cudnn_ops64, cublas64, c10_cuda = 5 arquivos
        assert count == 5, f"esperado 5 arquivos, got {count}"
        # Total aproximado: 1G + 50M + 120M + 109M + 5K = ~1.28 GB
        assert total > 1_200_000_000, f"total muito baixo: {total} bytes"

        # Arquivos que DEVEM estar em dist/ (nao movidos)
        assert (dist / "Transcritorio.exe").exists()
        assert (dist / "transcritorio-cli.exe").exists()
        assert (dist / "_internal" / "torch" / "lib" / "torch_cpu.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "c10.dll").exists()  # sem _cuda
        assert (dist / "_internal" / "config.json").exists()

        # Arquivos que DEVEM estar em pack/ (movidos)
        assert (pack / "_internal" / "torch" / "lib" / "torch_cuda.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "cudnn64_9.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "cudnn_ops64_9.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "cublas64_12.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "c10_cuda.dll").exists()

        # Os originais nao devem mais estar em dist/
        assert not (dist / "_internal" / "torch" / "lib" / "torch_cuda.dll").exists()
        assert not (dist / "_internal" / "torch" / "lib" / "cudnn64_9.dll").exists()

        # Manifest existe e tem os 5 paths
        manifest = (pack / "FILES.manifest").read_text(encoding="utf-8").strip().split("\n")
        assert len(manifest) == 5, f"manifest deveria ter 5 linhas, got {len(manifest)}: {manifest}"
        # Usa forward slashes
        assert all("/" in line for line in manifest), f"manifest deveria usar /, got: {manifest}"
        assert "_internal/torch/lib/torch_cuda.dll" in manifest

        print(f"PASS split_bundle: {count} arquivos ({total/1024/1024:.0f} MB) -> cuda_pack/")


def test_split_linux_bundle() -> None:
    """Bundle Linux (PyInstaller produz libtorch_cuda.so no lugar de .dll)."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        _make_file(dist / "Transcritorio", 1_000_000)  # exec Linux sem .exe
        _make_file(dist / "_internal" / "torch" / "lib" / "libtorch_cuda.so", 980_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libtorch_cpu.so", 245_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libcudnn.so.9", 180_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libc10.so", 5_000)

        pack = Path(tmp) / "cuda_pack"
        count, total = split_bundle(dist, pack)

        assert count == 2, f"esperado 2 arquivos Linux CUDA, got {count}"
        assert (pack / "_internal" / "torch" / "lib" / "libtorch_cuda.so").exists()
        assert (pack / "_internal" / "torch" / "lib" / "libcudnn.so.9").exists()
        assert (dist / "_internal" / "torch" / "lib" / "libtorch_cpu.so").exists()
        assert (dist / "_internal" / "torch" / "lib" / "libc10.so").exists()
        print(f"PASS split_bundle Linux: {count} arquivos .so movidos")


def test_split_empty_bundle() -> None:
    """Bundle sem nenhum arquivo CUDA — count=0, pack_dir vazio exceto manifest."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        _make_file(dist / "Transcritorio.exe", 1000)
        _make_file(dist / "_internal" / "torch" / "lib" / "torch_cpu.dll", 1000)
        _make_file(dist / "_internal" / "config.json", 1000)

        pack = Path(tmp) / "cuda_pack"
        count, total = split_bundle(dist, pack)

        assert count == 0, f"bundle sem CUDA, esperado 0, got {count}"
        assert total == 0
        # Manifest ainda criado (vazio)
        manifest_content = (pack / "FILES.manifest").read_text(encoding="utf-8").strip()
        assert manifest_content == "", f"manifest deveria estar vazio, got: {manifest_content!r}"
        # Arquivos originais intactos
        assert (dist / "Transcritorio.exe").exists()
        print("PASS split_bundle: 0 arquivos CUDA -> count=0 + manifest vazio")


def test_round_trip_via_overlay() -> None:
    """Apos split + copiar pack_dir sobre dist/, o bundle deve ser
    identico ao original (round-trip)."""
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        original_files = {
            "Transcritorio.exe": b"A" * 1000,
            "_internal/torch/lib/torch_cuda.dll": b"B" * 2000,
            "_internal/torch/lib/torch_cpu.dll": b"C" * 3000,
            "_internal/torch/lib/cudnn64_9.dll": b"D" * 1500,
            "_internal/config.json": b"{}",
        }
        for rel, content in original_files.items():
            p = dist / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

        pack = Path(tmp) / "cuda_pack"
        split_bundle(dist, pack)

        # Restore: copia tudo de pack/ pra dist/ (simula a extracao
        # do cuda_pack.zip sobre %ProgramFiles%\Transcritorio\)
        for src in pack.rglob("*"):
            if src.is_file() and src.name != "FILES.manifest":
                rel = src.relative_to(pack)
                dst = dist / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Agora dist/ deve ser identico ao original
        for rel, expected in original_files.items():
            got = (dist / rel).read_bytes()
            assert got == expected, f"round-trip quebrou: {rel}"
        print("PASS split_bundle: round-trip via overlay restaura bundle full")


if __name__ == "__main__":
    test_split_windows_bundle()
    test_split_linux_bundle()
    test_split_empty_bundle()
    test_round_trip_via_overlay()
    print()
    print("PASS: toy_split_bundle")
