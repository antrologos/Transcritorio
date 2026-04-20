"""End-to-end de trash/restore/redo com arquivos REAIS em tmpdir.

Nao usa Qt — exerce app_service diretamente.
Cria contexto, arquivos de audio fake, CSVs; chama prepare+finalize (sync);
depois restore; depois redo; depois purge.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import app_service, project_store
from transcribe_pipeline.config import DEFAULT_CONFIG, make_paths, ensure_directories
from transcribe_pipeline.utils import read_json, write_json


def make_fake_context(tmp: Path):
    """Cria um ProjectContext minimo com 2 entrevistas e alguns arquivos."""
    import csv
    config = dict(DEFAULT_CONFIG)
    config["project_root"] = str(tmp)
    paths = make_paths(config, base_dir=tmp)
    ensure_directories(paths)
    # jobs.json dir
    (paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
    (paths.output_root / "00_project" / "waveforms").mkdir(parents=True, exist_ok=True)

    # Criar arquivos fake: originais + wavs + waveforms + derivados
    (tmp / "audio").mkdir(exist_ok=True)
    (tmp / "audio" / "A01.mp3").write_bytes(b"FAKE_AUDIO_A01" * 100)
    (tmp / "audio" / "A02.mp3").write_bytes(b"FAKE_AUDIO_A02" * 100)
    (paths.wav_dir / "A01.wav").write_bytes(b"WAV_A01" * 50)
    (paths.wav_dir / "A02.wav").write_bytes(b"WAV_A02" * 50)
    (paths.output_root / "00_project" / "waveforms" / "A01.wf").write_bytes(b"wf_A01")
    (paths.output_root / "00_project" / "waveforms" / "A02.wf").write_bytes(b"wf_A02")
    (paths.asr_dir / "json").mkdir(parents=True, exist_ok=True)
    (paths.asr_dir / "json" / "A01.json").write_text("{}", encoding="utf-8")
    (paths.diarization_dir / "json").mkdir(parents=True, exist_ok=True)
    (paths.diarization_dir / "json" / "A01.exclusive.json").write_text("{}", encoding="utf-8")
    (paths.review_dir / "md").mkdir(parents=True, exist_ok=True)
    (paths.review_dir / "md" / "A01.md").write_text("# A01", encoding="utf-8")

    # CSVs
    with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
        w = csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"])
        w.writeheader()
        w.writerow({"interview_id": "A01", "source_path": "audio/A01.mp3", "selected": "true"})
        w.writerow({"interview_id": "A02", "source_path": "audio/A02.mp3", "selected": "true"})
    metadata = {}
    for iid in ["A01", "A02"]:
        item = {c: "" for c in project_store.METADATA_COLUMNS}
        item["file_id"] = iid
        item["title"] = f"Entrevista {iid}"
        item["source_path"] = f"audio/{iid}.mp3"
        metadata[iid] = item
    project_store.write_file_metadata(project_store.metadata_path(paths), metadata)
    with (paths.manifest_dir / "speakers_map.csv").open("w", newline="", encoding="utf-8-sig") as h:
        w = csv.DictWriter(h, fieldnames=["interview_id", "speaker_id", "role"])
        w.writeheader()
        w.writerow({"interview_id": "A01", "speaker_id": "SP1", "role": "Entrevistado"})
    write_json(paths.output_root / "00_project" / "jobs.json", {
        "A01": {"status": "Pendente", "progress": 0},
        "A02": {"status": "Concluido", "progress": 100},
    })

    # Build context
    project = project_store.normalize_project({}, paths, config)
    project["defaults"] = project_store.default_transcription_settings(config)
    project_store.save_project(paths, project)
    rows = [
        {"interview_id": "A01", "source_path": "audio/A01.mp3", "selected": "true"},
        {"interview_id": "A02", "source_path": "audio/A02.mp3", "selected": "true"},
    ]

    config_path = paths.config_dir / "run_config.yaml"
    context = app_service.build_context(config_path, config, paths, rows)
    return context, paths


def simulate_worker(trash_entry: dict) -> dict:
    """Espelha o que TrashMoveWorker.run faz. Sincrono para teste."""
    from datetime import datetime
    trash_dir = Path(trash_entry["trash_dir"])
    staging = trash_dir / "staging"
    project_root = Path(trash_entry["project_root"])
    staging.mkdir(parents=True, exist_ok=True)
    moved_files = []
    for mf in trash_entry.get("files_to_move") or []:
        src = Path(mf["original"])
        if not src.exists():
            continue
        try:
            rel = src.resolve().relative_to(project_root.resolve())
            dest = staging / rel
        except ValueError:
            dest = staging / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while (dest.parent / f"{stem}__{counter}{suffix}").exists():
                counter += 1
            dest = dest.parent / f"{stem}__{counter}{suffix}"
        shutil.copy2(str(src), str(dest))
        assert src.stat().st_size == dest.stat().st_size
        trashed_rel = str(dest.relative_to(trash_dir)).replace("\\", "/")
        moved_files.append({
            "original": str(src.resolve()),
            "trashed": trashed_rel,
            "size": int(src.stat().st_size),
            "mtime": float(src.stat().st_mtime),
        })
    files_dir = trash_dir / "files"
    staging.rename(files_dir)
    for mf in moved_files:
        mf["trashed"] = mf["trashed"].replace("staging/", "files/", 1)
    entry_dict = project_store._build_undo_entry(
        trash_id=trash_entry["trash_id"],
        interview_ids=trash_entry["interview_ids"],
        csv_mtimes=trash_entry.get("csv_mtimes") or {},
        snapshots=trash_entry.get("snapshots") or {},
        moved_files=moved_files,
        status="complete",
    )
    entry_dict["project_root"] = str(project_root)
    write_json(trash_dir / project_store.TRASH_MANIFEST, entry_dict)
    entry_dict["trash_dir"] = str(trash_dir)
    return entry_dict


def test_trash_restore_redo_e2e() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        context, paths = make_fake_context(tmp)

        # Estados originais
        orig_a01 = (tmp / "audio" / "A01.mp3").read_bytes()
        orig_a01_wav = (paths.wav_dir / "A01.wav").read_bytes()
        orig_manifest_count = 2

        # Passo 1: prepare_trash_move (main thread)
        entry_preparacao = app_service.prepare_trash_move(context, ["A01"])
        assert entry_preparacao["trash_id"]
        assert entry_preparacao["total_bytes"] > 0
        assert len(entry_preparacao["files_to_move"]) >= 3  # original + wav + wf + derivados
        print(f"PASS prepare_trash_move: {len(entry_preparacao['files_to_move'])} arquivos, {entry_preparacao['total_bytes']} bytes")

        # Passo 2: simular worker (sincrono para teste)
        entry_final = simulate_worker(entry_preparacao)
        assert entry_final["status"] == "complete"
        trash_dir = Path(entry_final["trash_dir"])
        assert (trash_dir / "files").exists()
        assert (trash_dir / project_store.TRASH_MANIFEST).exists()
        # Arquivos AINDA estao no lugar original (worker nao apaga)
        assert (tmp / "audio" / "A01.mp3").exists()
        print("PASS worker: copied to files/, originais ainda intactos")

        # Passo 3: finalize_trash_move (reescreve CSVs + apaga originais)
        trash_id, context = app_service.finalize_trash_move(context, entry_final)
        assert not (tmp / "audio" / "A01.mp3").exists(), "original deveria ter sido apagado"
        # Arquivo em trash preservado
        trashed_a01 = trash_dir / "files" / "audio" / "A01.mp3"
        assert trashed_a01.exists()
        assert trashed_a01.read_bytes() == orig_a01
        # Manifest sem A01
        import csv
        with (paths.manifest_dir / "manifest.csv").open("r", encoding="utf-8-sig") as h:
            rows_now = list(csv.DictReader(h))
        assert len(rows_now) == 1 and rows_now[0]["interview_id"] == "A02"
        # Jobs sem A01
        jobs = read_json(paths.output_root / "00_project" / "jobs.json")
        assert "A01" not in jobs
        assert "A02" in jobs
        print("PASS finalize: CSVs reescritas, originais apagados, A02 preservada")

        # Passo 4: restore
        warnings, context = app_service.restore_from_trash(context, trash_id)
        assert warnings == [], f"esperava sem warnings, got {warnings}"
        # A01 de volta
        assert (tmp / "audio" / "A01.mp3").exists()
        assert (tmp / "audio" / "A01.mp3").read_bytes() == orig_a01
        assert (paths.wav_dir / "A01.wav").exists()
        assert (paths.wav_dir / "A01.wav").read_bytes() == orig_a01_wav
        # Trash dir ainda existe (para redo)
        assert trash_dir.exists(), "trash dir deveria persistir apos undo"
        # Manifest restaurado
        with (paths.manifest_dir / "manifest.csv").open("r", encoding="utf-8-sig") as h:
            rows_restored = list(csv.DictReader(h))
        assert len(rows_restored) == 2
        # Jobs restaurados: status reflete artefatos presentes em disco (sync_jobs re-deriva)
        # Nao deve ser "Executando" nem "Na fila" (esses ficariam como zumbis)
        jobs = read_json(paths.output_root / "00_project" / "jobs.json")
        assert "A01" in jobs
        assert jobs["A01"]["status"] not in ("Executando", "Na fila", "Rodando"), jobs["A01"]
        print(f"PASS restore: A01 de volta, trash preservado, job status={jobs['A01']['status']!r} (derivado dos artefatos)")

        # Passo 5: restore COM COLISAO (ja restaurado)
        # Como os arquivos ja existem no lugar original, tentar restore de novo
        # deve levantar CollisionError
        try:
            app_service.restore_from_trash(context, trash_id, overwrite=False)
            assert False, "esperava CollisionError"
        except app_service.CollisionError as exc:
            assert len(exc.conflicts) >= 1
            assert all("size_now" in c for c in exc.conflicts)
            print(f"PASS restore colisao: {len(exc.conflicts)} conflitos detectados com rich info")

        # Passo 6: redo (re-apagar)
        _, context = app_service.redo_trash(context, trash_id)
        assert not (tmp / "audio" / "A01.mp3").exists()
        assert not (paths.wav_dir / "A01.wav").exists()
        # Trash dir ainda existe
        assert trash_dir.exists()
        # Manifest sem A01
        with (paths.manifest_dir / "manifest.csv").open("r", encoding="utf-8-sig") as h:
            rows_after_redo = list(csv.DictReader(h))
        assert len(rows_after_redo) == 1
        print("PASS redo: A01 re-apagado, trash preservado")

        # Passo 7: undo de novo (re-restore)
        _, context = app_service.restore_from_trash(context, trash_id)
        assert (tmp / "audio" / "A01.mp3").exists()
        print("PASS 2o undo: A01 de volta apos redo")

        # Passo 8: purge
        removed = app_service.purge_trash_entries(context, [trash_id])
        assert removed == 1
        assert not trash_dir.exists()
        print("PASS purge: trash_id removido permanentemente")


def test_redo_after_partial_unavailable() -> None:
    """Se undo.json diz status=partial, redo deve falhar."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        context, paths = make_fake_context(tmp)
        # Simular trash parcial
        trash_id = "20260420T999999_abcd"
        trash_dir = project_store.trash_root(paths) / trash_id
        trash_dir.mkdir(parents=True)
        (trash_dir / "files").mkdir()
        entry = project_store._build_undo_entry(
            trash_id=trash_id,
            interview_ids=["A01"],
            csv_mtimes={},
            snapshots={},
            moved_files=[],
            status="partial",
            pending_deletes=["ghost"],
        )
        write_json(trash_dir / project_store.TRASH_MANIFEST, entry)
        try:
            app_service.redo_trash(context, trash_id)
            assert False, "esperava RedoUnavailableError"
        except app_service.RedoUnavailableError as exc:
            assert "parcial" in str(exc).lower()
            print(f"PASS redo partial: {exc}")


def test_collect_trash_files_enumerates() -> None:
    """collect_trash_files deve achar original + wav + wf + derivados."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        context, paths = make_fake_context(tmp)
        files = app_service.collect_trash_files(context, ["A01"])
        paths_found = {f["original"] for f in files}
        expected_substrings = ["A01.mp3", "A01.wav", "A01.wf", "A01.json", "A01.exclusive.json", "A01.md"]
        for sub in expected_substrings:
            assert any(sub in p for p in paths_found), f"faltou {sub} em {paths_found}"
        # Nao deve incluir A02
        assert not any("A02" in p for p in paths_found)
        print(f"PASS collect: {len(files)} arquivos, todos de A01")


if __name__ == "__main__":
    test_collect_trash_files_enumerates()
    test_trash_restore_redo_e2e()
    test_redo_after_partial_unavailable()
    print()
    print("PASS: toy_trash_e2e")
