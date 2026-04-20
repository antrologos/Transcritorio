"""Toy test: ensure_results_dir com hardlink + fallback copy + LEIA-ME.

Valida:
- Hardlink criado na mesma particao (inode igual)
- Fallback copy se hardlink falha (mock EXDEV)
- LEIA-ME escrito uma vez
- Nao sobrescreve LEIA-ME existente
- Idempotente: chamar 2x nao duplica ou crasha
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import project_store


def test_hardlink_same_device() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final_dir = root / "final" / "docx"
        final_dir.mkdir(parents=True)
        results_dir = root / "Resultados"
        src = final_dir / "A01.reviewed.docx"
        src.write_bytes(b"hello" * 100)

        results = project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        assert results["created"] == 1, results
        linked_path = results_dir / "A01.reviewed.docx"
        assert linked_path.exists()
        # Hardlink: mesmo inode
        if os.name != "nt":
            assert linked_path.stat().st_ino == src.stat().st_ino, "esperava hardlink (mesmo inode)"
        # Conteudo bate
        assert linked_path.read_bytes() == src.read_bytes()
        assert results["method"] in ("hardlink", "copy"), results
        print(f"PASS hardlink same device: method={results['method']}")


def test_fallback_copy_on_exdev() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final_dir = root / "final" / "docx"
        final_dir.mkdir(parents=True)
        src = final_dir / "A01.reviewed.docx"
        src.write_bytes(b"content" * 50)

        # Mock os.link para lancar EXDEV
        import errno
        original_link = os.link

        def fake_link(src_, dst_, **kwargs):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        with patch.object(os, "link", fake_link):
            results = project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        linked_path = root / "Resultados" / "A01.reviewed.docx"
        assert linked_path.exists()
        assert linked_path.read_bytes() == src.read_bytes()
        assert results["method"] == "copy", f"esperava fallback copy, got {results}"
        print(f"PASS fallback copy: method={results['method']}")


def test_readme_written_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final_dir = root / "final" / "docx"
        final_dir.mkdir(parents=True)
        src = final_dir / "A01.reviewed.docx"
        src.write_bytes(b"x")

        project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        readme = root / "Resultados" / "LEIA-ME.txt"
        assert readme.exists()
        content = readme.read_text(encoding="utf-8")
        assert "Resultados" in content or "docx" in content
        print(f"PASS LEIA-ME criado: {len(content)} chars")

        # Modificar o LEIA-ME simulando edicao do usuario
        readme.write_text("EDITADO PELO USUARIO", encoding="utf-8")
        # Re-chamar ensure_results_dir NAO deve sobrescrever
        project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        assert readme.read_text(encoding="utf-8") == "EDITADO PELO USUARIO"
        print("PASS LEIA-ME nao sobrescreve edicao do usuario")


def test_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final_dir = root / "final" / "docx"
        final_dir.mkdir(parents=True)
        src = final_dir / "A01.reviewed.docx"
        src.write_bytes(b"abc")

        r1 = project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        r2 = project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        linked = root / "Resultados" / "A01.reviewed.docx"
        assert linked.exists()
        # Conteudo correto nas duas chamadas
        assert linked.read_bytes() == b"abc"
        print(f"PASS idempotente: 2 chamadas OK (r1.created={r1['created']}, r2.created={r2['created']})")


def test_updates_existing_link_on_changed_content() -> None:
    """Se o arquivo original foi re-exportado com conteudo novo, Resultados/ reflete."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final_dir = root / "final" / "docx"
        final_dir.mkdir(parents=True)
        src = final_dir / "A01.reviewed.docx"
        src.write_bytes(b"v1")

        project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        linked = root / "Resultados" / "A01.reviewed.docx"
        assert linked.read_bytes() == b"v1"

        # Re-exportar: overwrite src
        src.write_bytes(b"v2_new_content")
        project_store.ensure_results_dir(root, [src], results_subpath="Resultados")
        # Se hardlink, conteudo ja e v2. Se copy, precisa ter sido refeito.
        assert linked.read_bytes() == b"v2_new_content", linked.read_bytes()
        print("PASS atualiza link apos re-export")


def test_multiple_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for fmt in ("docx", "md"):
            d = root / "final" / fmt
            d.mkdir(parents=True)
        files = [
            root / "final" / "docx" / "A01.reviewed.docx",
            root / "final" / "docx" / "A02.reviewed.docx",
            root / "final" / "md" / "A01.reviewed.md",
        ]
        for f in files:
            f.write_bytes(b"x" * 20)

        results = project_store.ensure_results_dir(root, files, results_subpath="Resultados")
        res_dir = root / "Resultados"
        for f in files:
            assert (res_dir / f.name).exists(), f
        assert (res_dir / "LEIA-ME.txt").exists()
        print(f"PASS multiplos arquivos: {results['created']} criados")


if __name__ == "__main__":
    test_hardlink_same_device()
    test_fallback_copy_on_exdev()
    test_readme_written_once()
    test_idempotent()
    test_updates_existing_link_on_changed_content()
    test_multiple_files()
    print()
    print("PASS: toy_results_dir")
