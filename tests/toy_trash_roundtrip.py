"""Integration toy: trash + restore round-trip em tmpdir.

Valida que snapshot_interview_state + remove_ids_from_csvs + restore_ids_to_csvs
formam um par reversivel: CSV apos remove + restore == CSV original.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import project_store
from transcribe_pipeline.config import DEFAULT_CONFIG, Paths, ensure_directories, make_paths


def make_test_paths(tmp: Path) -> Paths:
    config = dict(DEFAULT_CONFIG)
    config["project_root"] = str(tmp)
    paths = make_paths(config, base_dir=tmp)
    ensure_directories(paths)
    return paths


def write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as h:
        w = csv.DictWriter(h, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_csv(path: Path) -> list[dict]:
    import csv
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as h:
        return [dict(r) for r in csv.DictReader(h)]


def test_snapshot_remove_restore_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        paths = make_test_paths(tmp)
        # Setup manifest.csv
        manifest_path = paths.manifest_dir / "manifest.csv"
        write_csv(
            manifest_path,
            ["interview_id", "source_path", "selected"],
            [
                {"interview_id": "A01", "source_path": "a.mp3", "selected": "true"},
                {"interview_id": "A02", "source_path": "b.mp3", "selected": "true"},
                {"interview_id": "A03", "source_path": "c.mp3", "selected": "true"},
            ],
        )
        # Setup metadados.csv
        meta_items = {
            "A01": {c: ("A01" if c == "file_id" else "") for c in project_store.METADATA_COLUMNS},
            "A02": {c: ("A02" if c == "file_id" else "") for c in project_store.METADATA_COLUMNS},
            "A03": {c: ("A03" if c == "file_id" else "") for c in project_store.METADATA_COLUMNS},
        }
        meta_items["A01"]["title"] = "Entrevista A01"
        project_store.write_file_metadata(project_store.metadata_path(paths), meta_items)
        # Setup speakers_map.csv
        speakers_path = paths.manifest_dir / "speakers_map.csv"
        write_csv(
            speakers_path,
            ["interview_id", "speaker_id", "role"],
            [
                {"interview_id": "A01", "speaker_id": "S01", "role": "Entrevistado"},
                {"interview_id": "A02", "speaker_id": "S01", "role": "Entrevistado"},
            ],
        )
        # Setup jobs.json
        jobs_path_ = project_store.jobs_path(paths)
        jobs_path_.parent.mkdir(parents=True, exist_ok=True)
        from transcribe_pipeline.utils import write_json, read_json
        write_json(jobs_path_, {
            "A01": {"status": "Pendente", "progress": 0},
            "A02": {"status": "Concluido", "progress": 100},
        })

        # Capturar estados originais
        orig_manifest = read_csv(manifest_path)
        orig_meta = project_store.read_file_metadata(project_store.metadata_path(paths))
        orig_speakers = read_csv(speakers_path)
        orig_jobs = read_json(jobs_path_)

        # Snapshot + remove
        snap = project_store.snapshot_interview_state(paths, ["A01", "A02"])
        assert len(snap["manifest_rows"]) == 2, snap
        assert len(snap["metadata_rows"]) == 2, snap
        assert len(snap["speakers_rows"]) == 2, snap
        assert set(snap["jobs_entries"].keys()) == {"A01", "A02"}, snap
        print("PASS snapshot: capturou 2 entrevistas")

        project_store.remove_ids_from_csvs(paths, ["A01", "A02"])
        # Apos remove, so A03 deve restar
        after_manifest = read_csv(manifest_path)
        assert len(after_manifest) == 1 and after_manifest[0]["interview_id"] == "A03", after_manifest
        after_meta = project_store.read_file_metadata(project_store.metadata_path(paths))
        assert list(after_meta.keys()) == ["A03"], after_meta
        after_speakers = read_csv(speakers_path)
        assert len(after_speakers) == 0, after_speakers
        after_jobs = read_json(jobs_path_)
        assert list(after_jobs.keys()) == [], after_jobs
        print("PASS remove: A01 e A02 removidos, A03 preservado")

        # Restore
        project_store.restore_ids_to_csvs(paths, snap)
        restored_manifest = read_csv(manifest_path)
        assert len(restored_manifest) == 3, restored_manifest
        ids_manifest = {r["interview_id"] for r in restored_manifest}
        assert ids_manifest == {"A01", "A02", "A03"}, ids_manifest
        restored_meta = project_store.read_file_metadata(project_store.metadata_path(paths))
        assert set(restored_meta.keys()) == {"A01", "A02", "A03"}, restored_meta
        # Title de A01 deve ser restaurado
        assert restored_meta["A01"]["title"] == "Entrevista A01", restored_meta["A01"]
        restored_speakers = read_csv(speakers_path)
        assert len(restored_speakers) == 2, restored_speakers
        restored_jobs = read_json(jobs_path_)
        # Jobs devem ser restaurados mas com status=Pendente (reset)
        assert restored_jobs["A01"]["status"] == "Pendente"
        assert restored_jobs["A02"]["status"] == "Pendente", restored_jobs
        print("PASS restore: tudo restaurado, jobs resetados para Pendente")


def test_restore_idempotent_no_duplicates() -> None:
    """Chamar restore duas vezes nao deve duplicar linhas."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        paths = make_test_paths(tmp)
        snap = {
            "manifest_rows": [{"interview_id": "A01", "source_path": "a.mp3"}],
            "metadata_rows": [{c: ("A01" if c == "file_id" else "") for c in project_store.METADATA_COLUMNS}],
            "speakers_rows": [{"interview_id": "A01", "speaker_id": "S01", "role": "X"}],
            "jobs_entries": {"A01": {"status": "Pendente"}},
        }
        # Precisa ter CSVs ja criadas com schema compativel
        from transcribe_pipeline.config import make_paths as _mp
        write_csv(project_store.manifest_csv_path(paths), ["interview_id", "source_path"], [])
        write_csv(project_store.speakers_map_csv_path(paths), ["interview_id", "speaker_id", "role"], [])
        project_store.write_file_metadata(project_store.metadata_path(paths), {})

        project_store.restore_ids_to_csvs(paths, snap)
        project_store.restore_ids_to_csvs(paths, snap)  # segunda vez nao deve duplicar
        assert len(read_csv(project_store.manifest_csv_path(paths))) == 1
        assert len(read_csv(project_store.speakers_map_csv_path(paths))) == 1
        print("PASS restore: idempotente (nao duplica ao chamar 2x)")


if __name__ == "__main__":
    test_snapshot_remove_restore_roundtrip()
    test_restore_idempotent_no_duplicates()
    print()
    print("PASS: toy_trash_roundtrip")
