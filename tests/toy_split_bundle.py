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
    """2026-04-23: split separa APENAS as 14 DLLs CUDA lazy-load.
    torch_cuda/cudnn64/cublas/c10_cuda sao IAT obrigatorias, ficam no base."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        _make_file(dist / "Transcritorio.exe", 1_000_000)
        _make_file(dist / "transcritorio-cli.exe", 500_000)
        # 11 IAT obrigatorias — ficam no base
        _make_file(dist / "_internal" / "torch" / "lib" / "torch_cuda.dll", 1_000_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "torch_cpu.dll", 245_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn64_9.dll", 300_000)  # loader, pequeno
        _make_file(dist / "_internal" / "torch" / "lib" / "cublas64_12.dll", 109_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "c10.dll", 5_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "c10_cuda.dll", 5_000)  # IAT obrig
        # 3 lazy-load exclusivas de 'cpu' (MINIMAL ja removeria curand em full,
        # split_bundle pula essas — elas nunca chegam ao bundle full pra splitar)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn_ops64_9.dll", 120_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn_adv64_9.dll", 270_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "cudnn_graph64_9.dll", 2_500_000)
        _make_file(dist / "_internal" / "config.json", 100)

        pack = Path(tmp) / "cuda_pack"
        count, total = split_bundle(dist, pack)

        # Deve ter movido as 3 lazy-load (exclusivas variant=cpu)
        assert count == 3, f"esperado 3 arquivos (lazy-load), got {count}"
        assert total > 380_000_000, f"total muito baixo: {total} bytes"

        # IAT obrigatorias continuam em dist/
        assert (dist / "Transcritorio.exe").exists()
        assert (dist / "_internal" / "torch" / "lib" / "torch_cuda.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "torch_cpu.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "cudnn64_9.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "cublas64_12.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "c10_cuda.dll").exists()
        assert (dist / "_internal" / "torch" / "lib" / "c10.dll").exists()
        assert (dist / "_internal" / "config.json").exists()

        # Lazy-load movidas pro pack
        assert (pack / "_internal" / "torch" / "lib" / "cudnn_ops64_9.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "cudnn_adv64_9.dll").exists()
        assert (pack / "_internal" / "torch" / "lib" / "cudnn_graph64_9.dll").exists()
        assert not (dist / "_internal" / "torch" / "lib" / "cudnn_ops64_9.dll").exists()

        manifest = (pack / "FILES.manifest").read_text(encoding="utf-8").strip().split("\n")
        assert len(manifest) == 3, f"manifest deveria ter 3 linhas, got {len(manifest)}: {manifest}"
        assert all("/" in line for line in manifest), f"manifest deveria usar /, got: {manifest}"
        assert "_internal/torch/lib/cudnn_ops64_9.dll" in manifest

        print(f"PASS split_bundle: {count} lazy-load movidas ({total/1024/1024:.0f} MB) -> cuda_pack/")


def test_split_linux_bundle() -> None:
    """Bundle Linux: PyInstaller produz libtorch_cuda.so + libcudnn_ops.so.
    2026-04-23: libtorch_cuda.so e IAT obrigatoria; split so libcudnn_ops.so."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        _make_file(dist / "Transcritorio", 1_000_000)
        # IAT obrigatorias — ficam no base
        _make_file(dist / "_internal" / "torch" / "lib" / "libtorch_cuda.so", 980_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libtorch_cpu.so", 245_000_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libcudnn.so.9", 300_000)
        _make_file(dist / "_internal" / "torch" / "lib" / "libc10.so", 5_000)
        # lazy-load — vai pro pack
        _make_file(dist / "_internal" / "torch" / "lib" / "libcudnn_ops.so.9", 120_000_000)

        pack = Path(tmp) / "cuda_pack"
        count, total = split_bundle(dist, pack)

        assert count == 1, f"esperado 1 arquivo Linux lazy-load, got {count}"
        assert (pack / "_internal" / "torch" / "lib" / "libcudnn_ops.so.9").exists()
        # IAT obrigatorias preservadas
        assert (dist / "_internal" / "torch" / "lib" / "libtorch_cuda.so").exists()
        assert (dist / "_internal" / "torch" / "lib" / "libcudnn.so.9").exists()
        assert (dist / "_internal" / "torch" / "lib" / "libtorch_cpu.so").exists()
        assert (dist / "_internal" / "torch" / "lib" / "libc10.so").exists()
        print(f"PASS split_bundle Linux: {count} arquivo lazy-load movido")


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
    identico ao original (round-trip). 2026-04-23: precisa ter pelo menos
    uma lazy-load DLL no exemplo (so essas sao splittadas)."""
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "Transcritorio"
        original_files = {
            "Transcritorio.exe": b"A" * 1000,
            "_internal/torch/lib/torch_cuda.dll": b"B" * 2000,      # IAT — fica
            "_internal/torch/lib/torch_cpu.dll": b"C" * 3000,        # fica
            "_internal/torch/lib/cudnn64_9.dll": b"D" * 1500,        # IAT — fica
            "_internal/torch/lib/cudnn_ops64_9.dll": b"E" * 2500,    # lazy — vai pro pack
            "_internal/torch/lib/cudnn_adv64_9.dll": b"F" * 2000,    # lazy — vai pro pack
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
