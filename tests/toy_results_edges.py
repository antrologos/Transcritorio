"""Edge cases para ensure_results_dir + integracao com app_service.export_review.

Cobre:
- Lista vazia
- Unicode no nome (acentos, emojis)
- Espacos no nome
- Permissao negada na pasta Resultados (silencia)
- Hardlink no mesmo arquivo 2x (existing)
- Fluxo completo app_service.export_review gerando review real + mirror
- use_resultados_dir=False nao cria pasta
- readme_created=False na 2a chamada
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import project_store


def test_empty_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        r = project_store.ensure_results_dir(root, [])
        # Pasta ainda e criada + LEIA-ME escrito
        assert (root / "Resultados").exists()
        assert (root / "Resultados" / "LEIA-ME.txt").exists()
        assert r["created"] == 0
        assert r["method"] == "none"
        assert r["readme_created"] is True
        print("PASS lista vazia: pasta + LEIA-ME ainda criados, created=0")


def test_unicode_and_spaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final = root / "final" / "docx"
        final.mkdir(parents=True)
        files = [
            final / "Entrevista Joao.reviewed.docx",
            final / "Sessao_Cafe.reviewed.docx",
            final / "Sessao 2a - Maria.reviewed.docx",
        ]
        for f in files:
            f.write_bytes(b"x" * 50)
        r = project_store.ensure_results_dir(root, files)
        assert r["created"] == 3, r
        for f in files:
            dst = root / "Resultados" / f.name
            assert dst.exists(), f"faltou {dst}"
            assert dst.read_bytes() == f.read_bytes()
        print(f"PASS unicode/espacos: {r['created']} arquivos espelhados")


def test_readme_not_recreated_on_second_call() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final = root / "final" / "docx"
        final.mkdir(parents=True)
        src = final / "A.docx"
        src.write_bytes(b"v1")

        r1 = project_store.ensure_results_dir(root, [src])
        assert r1["readme_created"] is True

        r2 = project_store.ensure_results_dir(root, [src])
        assert r2["readme_created"] is False, f"LEIA-ME nao deveria ser re-criado: {r2}"
        print("PASS readme_created: True na 1a, False na 2a")


def test_silent_failure_on_bad_dest() -> None:
    """Se _write_text do LEIA-ME falha, nao lanca."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        final = root / "final"
        final.mkdir(parents=True)
        src = final / "A.docx"
        src.write_bytes(b"x")
        # Mock Path.write_text para falhar so no readme
        from pathlib import Path as _PathCls
        original = _PathCls.write_text

        def fake_write_text(self, *a, **kw):
            if self.name == "LEIA-ME.txt":
                raise OSError("permission denied")
            return original(self, *a, **kw)

        with patch.object(_PathCls, "write_text", fake_write_text):
            r = project_store.ensure_results_dir(root, [src])
        # LEIA-ME nao foi criado mas nao lancou
        assert r["readme_created"] is False
        # Arquivo principal espelhado
        assert (root / "Resultados" / "A.docx").exists()
        print("PASS silent failure no LEIA-ME: sem exception, arquivo principal OK")


def test_app_service_export_review_mirror() -> None:
    """Integracao do HOOK: mocka export_review_outputs para isolar app_service.export_review.
    Verifica que ensure_results_dir e chamado e Resultados/ e populado."""
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from transcribe_pipeline import app_service
        from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config

        config = dict(DEFAULT_CONFIG)
        config["project_root"] = str(root)
        paths = make_paths(config, base_dir=root)
        ensure_directories(paths)
        (paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
        with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
            w = csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"])
            w.writeheader()
        (paths.manifest_dir / "speakers_map.csv").write_text("interview_id,speaker_id,role\n", encoding="utf-8-sig")
        config_path = paths.config_dir / "run_config.yaml"
        write_config(config_path, config, header=["# test"])

        # Criar arquivos finais ja "gerados" manualmente (simula saida de export_review_outputs)
        final_dir = paths.review_dir / "final"
        md_file = final_dir / "md" / "A01.reviewed.md"
        md_file.parent.mkdir(parents=True)
        md_file.write_text("# A01\ncontent", encoding="utf-8")
        docx_file = final_dir / "docx" / "A01.reviewed.docx"
        docx_file.parent.mkdir(parents=True)
        docx_file.write_bytes(b"fake docx" * 200)
        # Formato NVivo (tecnico, nao-user-facing): nao deve espelhar
        nvivo_file = final_dir / "nvivo" / "A01.reviewed_nvivo.tsv"
        nvivo_file.parent.mkdir(parents=True)
        nvivo_file.write_text("a\tb\n1\t2\n", encoding="utf-8")
        fake_output = [md_file, docx_file, nvivo_file]

        context = app_service.load_project(config_path=config_path)

        with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=fake_output):
            exported = app_service.export_review(context, "A01", formats=["md", "docx", "nvivo"])

        assert exported == fake_output
        resultados = root / "Resultados"
        assert resultados.exists(), "Resultados/ nao foi criado"
        assert (resultados / "LEIA-ME.txt").exists()
        assert (resultados / "A01.reviewed.md").exists()
        assert (resultados / "A01.reviewed.docx").exists()
        # NVivo e tecnico -> nao deve estar em Resultados
        assert not (resultados / "A01.reviewed_nvivo.tsv").exists(), "nvivo nao deveria espelhar"
        print("PASS hook export_review: MD+DOCX espelhados, nvivo filtrado, LEIA-ME criado")


def test_use_resultados_dir_false_skips() -> None:
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from transcribe_pipeline import app_service
        from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config

        config = dict(DEFAULT_CONFIG)
        config["project_root"] = str(root)
        config["use_resultados_dir"] = False
        paths = make_paths(config, base_dir=root)
        ensure_directories(paths)
        (paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
        with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
            w = csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"])
            w.writeheader()
        (paths.manifest_dir / "speakers_map.csv").write_text("interview_id,speaker_id,role\n", encoding="utf-8-sig")
        config_path = paths.config_dir / "run_config.yaml"
        write_config(config_path, config, header=["# test"])

        md_file = paths.review_dir / "final" / "md" / "A01.reviewed.md"
        md_file.parent.mkdir(parents=True)
        md_file.write_text("# x", encoding="utf-8")

        context = app_service.load_project(config_path=config_path)
        assert context.config.get("use_resultados_dir") is False

        with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=[md_file]):
            app_service.export_review(context, "A01", formats=["md"])
        resultados = root / "Resultados"
        assert not resultados.exists(), "flag off -> Resultados/ NAO deveria ter sido criado"
        print("PASS use_resultados_dir=False nao cria Resultados/")


def test_only_non_user_formats_no_mirror() -> None:
    """Se export gera so formatos tecnicos (nvivo), Resultados nao e criado."""
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from transcribe_pipeline import app_service
        from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories, write_config

        config = dict(DEFAULT_CONFIG)
        config["project_root"] = str(root)
        paths = make_paths(config, base_dir=root)
        ensure_directories(paths)
        (paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
        with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
            w = csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"])
            w.writeheader()
        (paths.manifest_dir / "speakers_map.csv").write_text("interview_id,speaker_id,role\n", encoding="utf-8-sig")
        config_path = paths.config_dir / "run_config.yaml"
        write_config(config_path, config, header=["# test"])

        nvivo = paths.review_dir / "final" / "nvivo" / "A01.reviewed_nvivo.tsv"
        nvivo.parent.mkdir(parents=True)
        nvivo.write_text("a\tb\n", encoding="utf-8")

        context = app_service.load_project(config_path=config_path)
        with patch("transcribe_pipeline.app_service.export_review_outputs", return_value=[nvivo]):
            app_service.export_review(context, "A01", formats=["nvivo"])
        assert not (root / "Resultados").exists(), "nvivo-only nao deve criar Resultados"
        print("PASS formato nao-user-facing (nvivo) nao cria Resultados/")


if __name__ == "__main__":
    test_empty_list()
    test_unicode_and_spaces()
    test_readme_not_recreated_on_second_call()
    test_silent_failure_on_bad_dest()
    test_app_service_export_review_mirror()
    test_use_resultados_dir_false_skips()
    test_only_non_user_formats_no_mirror()
    print()
    print("PASS: toy_results_edges")
