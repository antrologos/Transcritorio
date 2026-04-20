from __future__ import annotations

from pathlib import Path
from typing import Any
import csv

from .config import Paths
from .manifest import selected_rows
from .utils import now_utc, read_json, relative_to, write_json


PROJECT_SCHEMA_VERSION = 1
PROJECT_EXTENSION = ".transcritorio"
LEGACY_PROJECT_FILENAME = "projeto.transcricao.json"
METADATA_FILENAME = "metadados.csv"
INTERNAL_PROJECT_DIR = "00_project"
JOBS_FILENAME = "jobs.json"
TRASH_DIRNAME = ".trash"
TRASH_MANIFEST = "undo.json"
RESULTADOS_DIRNAME = "Resultados"
RESULTADOS_README = "LEIA-ME.txt"
RESULTADOS_README_TEXT = """Seus arquivos finais estao aqui
===============================

Esta pasta contem as transcricoes prontas para uso:

  - .docx  Abra no Microsoft Word ou similar. Este e o formato recomendado
           para leitura, revisao e impressao.
  - .md    Texto simples com marcacao, util para pesquisa e versionamento.
  - .srt   Legendas com tempo, para uso em videos.
  - .csv/.tsv  Planilhas com turnos e metadados.

As demais pastas do projeto (00_config, 00_manifest, 00_project,
01_audio_wav16k_mono, 02_asr_raw, ..., 06_qc, logs) guardam arquivos
tecnicos usados pelo programa. Voce nao precisa abri-las.

Duvidas? Use o menu Ajuda > Documentacao dentro do Transcritorio.
"""


def ensure_results_dir(
    project_root: Path,
    exported_paths: list[Path],
    *,
    results_subpath: str = RESULTADOS_DIRNAME,
) -> dict[str, Any]:
    """Mirror exported files into {project_root}/Resultados/ via hardlink,
    fallback to copy (+ read-only on Windows) if hardlink fails.

    LEIA-ME.txt is written once (not overwritten if the user edited it).
    Returns stats dict: {created: int, method: "hardlink"|"copy"|"mixed",
    readme_created: bool}.
    """
    import errno
    import os
    import shutil as _shutil

    project_root = Path(project_root)
    results_dir = project_root / results_subpath
    results_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    methods: set[str] = set()
    for src in exported_paths:
        src_path = Path(src)
        if not src_path.exists():
            continue
        dst = results_dir / src_path.name
        # Se dst ja existe: reusar se ja e hardlink do mesmo arquivo; senao refazer
        if dst.exists():
            try:
                s1 = src_path.stat()
                s2 = dst.stat()
                if s1.st_size == s2.st_size and (
                    os.name == "nt" or (s1.st_ino == s2.st_ino and s1.st_dev == s2.st_dev)
                ) and s1.st_mtime <= s2.st_mtime + 1:
                    continue
            except OSError:
                pass
            try:
                if os.name == "nt":
                    # Pode estar read-only (fallback copy): tirar attribute antes de apagar
                    try:
                        import subprocess as _sp
                        _sp.run(
                            ["attrib", "-R", str(dst)],
                            check=False,
                            capture_output=True,
                            creationflags=0x08000000,  # CREATE_NO_WINDOW
                        )
                    except Exception:
                        pass
                dst.unlink()
            except OSError:
                continue
        # Tenta hardlink
        used_method: str | None = None
        try:
            os.link(str(src_path), str(dst))
            used_method = "hardlink"
        except (OSError, NotImplementedError) as exc:
            # EXDEV (cross-device), EPERM, EACCES, etc. → fallback copy
            try:
                _shutil.copy2(str(src_path), str(dst))
                used_method = "copy"
                # Marcar read-only em Windows para indicar pasta gerenciada
                if os.name == "nt":
                    try:
                        import subprocess as _sp
                        _sp.run(
                            ["attrib", "+R", str(dst)],
                            check=False,
                            capture_output=True,
                            creationflags=0x08000000,
                        )
                    except Exception:
                        pass
            except OSError:
                continue
        if used_method:
            methods.add(used_method)
            created += 1

    # LEIA-ME so escreve se nao existe
    readme_path = results_dir / RESULTADOS_README
    readme_created = False
    if not readme_path.exists():
        try:
            readme_path.write_text(RESULTADOS_README_TEXT, encoding="utf-8")
            readme_created = True
        except OSError:
            pass

    method = "mixed" if len(methods) > 1 else (next(iter(methods)) if methods else "none")
    return {"created": created, "method": method, "readme_created": readme_created}

METADATA_COLUMNS = [
    "file_id",
    "title",
    "source_path",
    "person_folder",
    "source_ext",
    "source_kind",
    "source_size_bytes",
    "source_mtime_utc",
    "duration_sec",
    "source_audio_streams",
    "source_audio_codec",
    "source_sample_rate",
    "source_audio_channels",
    "source_channel_layout",
    "source_bit_rate",
    "source_video_streams",
    "source_video_codec",
    "source_video_width",
    "source_video_height",
    "source_video_frame_rate",
    "source_format_name",
    "source_format_long_name",
    "probe_status",
    "language",
    "speaker_mode",
    "speaker_count",
    "min_speakers",
    "max_speakers",
    "speaker_labels",
    "context_mode",
    "context_text",
    "use_context_as_prompt",
    "notes",
    "updated_at",
]

LANGUAGE_LABELS = {
    "auto": "Automático",
    "pt": "Português",
    "en": "Inglês",
    "es": "Espanhol",
    "fr": "Francês",
    "de": "Alemão",
    "it": "Italiano",
}


def safe_project_filename(project_name: str) -> str:
    """Generate a safe filename from the project name (e.g. 'Meu Projeto' -> 'Meu Projeto.transcritorio')."""
    safe = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_"
        for c in project_name
    ).strip(" ._")
    return f"{safe or 'Projeto'}{PROJECT_EXTENSION}"


def find_project_file(project_root: Path) -> Path | None:
    """Find the .transcritorio project file in the project root.

    If no .transcritorio file exists but the legacy projeto.transcricao.json
    does, migrate it automatically (rename to <project_name>.transcritorio).
    Returns None if no project file is found.
    """
    candidates = list(project_root.glob(f"*{PROJECT_EXTENSION}"))
    if candidates:
        return candidates[0]
    legacy = project_root / LEGACY_PROJECT_FILENAME
    if legacy.exists():
        import json as _json
        try:
            data = _json.loads(legacy.read_text(encoding="utf-8"))
            name = data.get("project_name", project_root.name)
        except (ValueError, OSError):
            name = project_root.name
        new_path = project_root / safe_project_filename(name)
        legacy.rename(new_path)
        return new_path
    return None


def project_path(paths: Paths) -> Path:
    found = find_project_file(paths.project_root)
    if found is not None:
        return found
    # Fallback for new projects: use folder name
    return paths.project_root / safe_project_filename(paths.project_root.name)


def metadata_path(paths: Paths) -> Path:
    return paths.project_root / METADATA_FILENAME


def jobs_path(paths: Paths) -> Path:
    return paths.output_root / INTERNAL_PROJECT_DIR / JOBS_FILENAME


def ensure_project(paths: Paths, config: dict[str, Any]) -> dict[str, Any]:
    path = project_path(paths)
    if path.exists():
        project = read_json(path)
    else:
        project = default_project(paths, config)
    normalized = normalize_project(project, paths, config)
    if normalized != project or not path.exists():
        write_json(path, normalized)
    return normalized


def save_project(paths: Paths, project: dict[str, Any]) -> dict[str, Any]:
    updated = dict(project)
    updated["updated_at"] = now_utc()
    write_json(project_path(paths), updated)
    return updated


def default_project(paths: Paths, config: dict[str, Any]) -> dict[str, Any]:
    now = now_utc()
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project_name": paths.project_root.name,
        "created_at": now,
        "updated_at": now,
        "app": "Transcritorio",
        "paths": {
            "metadata_file": METADATA_FILENAME,
            "work_dir": relative_to(paths.output_root, paths.project_root),
            "jobs_file": relative_to(jobs_path(paths), paths.project_root),
        },
        "media_policy": "referenciar",
        "defaults": default_transcription_settings(config),
    }


def _find_collisions(moved_files: list[dict]) -> list[dict]:
    """Check which `original` paths already exist on disk. Returns rich info."""
    import os
    conflicts: list[dict] = []
    for mf in moved_files:
        original = mf.get("original") or ""
        if not original:
            continue
        p = Path(original)
        if p.exists():
            try:
                st = p.stat()
                conflicts.append({
                    "original": original,
                    "size_now": int(st.st_size),
                    "size_was": int(mf.get("size") or 0),
                    "mtime_now": float(st.st_mtime),
                    "mtime_was": float(mf.get("mtime") or 0.0),
                })
            except OSError:
                conflicts.append({
                    "original": original,
                    "size_now": -1,
                    "size_was": int(mf.get("size") or 0),
                    "mtime_now": 0.0,
                    "mtime_was": float(mf.get("mtime") or 0.0),
                })
    return conflicts


def _build_undo_entry(
    trash_id: str,
    interview_ids: list[str],
    csv_mtimes: dict[str, float],
    snapshots: dict[str, Any],
    moved_files: list[dict],
    status: str = "complete",
    pending_deletes: list[str] | None = None,
) -> dict[str, Any]:
    """Canonical structure for .trash/<trash_id>/undo.json."""
    entry: dict[str, Any] = {
        "trash_id": trash_id,
        "created_at": now_utc(),
        "interview_ids": list(interview_ids),
        "csv_mtimes": dict(csv_mtimes),
        "snapshots": dict(snapshots),
        "moved_files": list(moved_files),
        "status": status,
    }
    if pending_deletes is not None:
        entry["pending_deletes"] = list(pending_deletes)
    return entry


def trash_root(paths: Paths) -> Path:
    return paths.output_root / INTERNAL_PROJECT_DIR / TRASH_DIRNAME


def generate_trash_id() -> str:
    import secrets
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{secrets.token_hex(2)}"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def manifest_csv_path(paths: Paths) -> Path:
    return paths.manifest_dir / "manifest.csv"


def speakers_map_csv_path(paths: Paths) -> Path:
    return paths.manifest_dir / "speakers_map.csv"


def snapshot_interview_state(paths: Paths, interview_ids: list[str]) -> dict[str, Any]:
    """Return CSV/JSON rows belonging to these ids, for trash undo.json."""
    ids_set = set(interview_ids)
    manifest_rows = [r for r in _read_csv_rows(manifest_csv_path(paths)) if r.get("interview_id") in ids_set]
    meta_path = metadata_path(paths)
    metadata_rows = [r for r in _read_csv_rows(meta_path) if r.get("file_id") in ids_set]
    speakers_rows = [r for r in _read_csv_rows(speakers_map_csv_path(paths)) if r.get("interview_id") in ids_set]
    jobs_entries: dict[str, Any] = {}
    jpath = jobs_path(paths)
    if jpath.exists():
        current = read_json(jpath) or {}
        for iid in interview_ids:
            if iid in current:
                jobs_entries[iid] = current[iid]
    return {
        "manifest_rows": manifest_rows,
        "metadata_rows": metadata_rows,
        "speakers_rows": speakers_rows,
        "jobs_entries": jobs_entries,
    }


def csv_mtimes_snapshot(paths: Paths) -> dict[str, float]:
    """Capture mtimes of the 3 CSVs for conflict detection at restore time."""
    result: dict[str, float] = {}
    for label, p in [
        ("manifest.csv", manifest_csv_path(paths)),
        ("metadados.csv", metadata_path(paths)),
        ("speakers_map.csv", speakers_map_csv_path(paths)),
    ]:
        try:
            result[label] = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            result[label] = 0.0
    return result


def remove_ids_from_csvs(paths: Paths, interview_ids: list[str]) -> None:
    """Rewrite all project CSVs and jobs.json without the given ids."""
    ids_set = set(interview_ids)
    mpath = manifest_csv_path(paths)
    mrows = _read_csv_rows(mpath)
    if mrows:
        fieldnames = list(mrows[0].keys())
        _write_csv_rows(mpath, [r for r in mrows if r.get("interview_id") not in ids_set], fieldnames)
    metapath = metadata_path(paths)
    mdata = read_file_metadata(metapath)
    for iid in interview_ids:
        mdata.pop(iid, None)
    write_file_metadata(metapath, mdata)
    spath = speakers_map_csv_path(paths)
    srows = _read_csv_rows(spath)
    if srows:
        fieldnames = list(srows[0].keys())
        _write_csv_rows(spath, [r for r in srows if r.get("interview_id") not in ids_set], fieldnames)
    jpath = jobs_path(paths)
    if jpath.exists():
        current = read_json(jpath) or {}
        for iid in interview_ids:
            current.pop(iid, None)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        write_json(jpath, current)


def restore_ids_to_csvs(paths: Paths, snapshots: dict[str, Any]) -> None:
    """Reinject snapshotted rows. If csv doesn't exist yet, create it."""
    manifest_rows = snapshots.get("manifest_rows") or []
    if manifest_rows:
        mpath = manifest_csv_path(paths)
        current = _read_csv_rows(mpath)
        fieldnames = list(current[0].keys()) if current else list(manifest_rows[0].keys())
        existing_ids = {r.get("interview_id") for r in current}
        merged = current + [r for r in manifest_rows if r.get("interview_id") not in existing_ids]
        _write_csv_rows(mpath, merged, fieldnames)
    metadata_rows = snapshots.get("metadata_rows") or []
    if metadata_rows:
        metapath = metadata_path(paths)
        mdata = read_file_metadata(metapath)
        for row in metadata_rows:
            fid = row.get("file_id")
            if fid and fid not in mdata:
                mdata[fid] = {k: row.get(k, "") for k in METADATA_COLUMNS}
        write_file_metadata(metapath, mdata)
    speakers_rows = snapshots.get("speakers_rows") or []
    if speakers_rows:
        spath = speakers_map_csv_path(paths)
        current = _read_csv_rows(spath)
        fieldnames = list(current[0].keys()) if current else list(speakers_rows[0].keys())
        key = "interview_id"
        existing_keys = {(r.get(key), r.get("speaker_id"), r.get("role")) for r in current}
        merged = current + [r for r in speakers_rows if (r.get(key), r.get("speaker_id"), r.get("role")) not in existing_keys]
        _write_csv_rows(spath, merged, fieldnames)
    jobs_entries = snapshots.get("jobs_entries") or {}
    if jobs_entries:
        jpath = jobs_path(paths)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        current = read_json(jpath) if jpath.exists() else {}
        for iid, entry in jobs_entries.items():
            if iid not in current:
                # Reset status to Pendente (nao ressuscitar jobs Executando)
                clean = dict(entry)
                clean["status"] = "Pendente"
                clean["stage"] = ""
                clean["progress"] = 0
                clean["started_at"] = ""
                clean["finished_at"] = ""
                current[iid] = clean
        write_json(jpath, current)


def _reorder_move(
    ordered_ids: list[str],
    moving_id: str,
    direction: int,
    hidden_ids: set[str] | None = None,
) -> list[str]:
    """Move moving_id up (-1) or down (+1) by one position among visible ids."""
    if direction not in (-1, 1):
        raise ValueError(f"direction must be -1 or +1, got {direction}")
    hidden = hidden_ids or set()
    if moving_id not in ordered_ids or moving_id in hidden:
        return list(ordered_ids)
    visible = [iid for iid in ordered_ids if iid not in hidden]
    if moving_id not in visible:
        return list(ordered_ids)
    v_idx = visible.index(moving_id)
    target_v_idx = v_idx + direction
    if target_v_idx < 0 or target_v_idx >= len(visible):
        return list(ordered_ids)
    target_neighbor = visible[target_v_idx]
    new = list(ordered_ids)
    i_move = new.index(moving_id)
    i_target = new.index(target_neighbor)
    new[i_move], new[i_target] = new[i_target], new[i_move]
    return new


def _merge_interview_order(
    existing_order: list[str],
    current_ids: list[str],
) -> list[str]:
    """Keep ordering for ids still present; append new ids in their original order."""
    current_set = set(current_ids)
    kept = [iid for iid in existing_order if iid in current_set]
    kept_set = set(kept)
    appended = [iid for iid in current_ids if iid not in kept_set]
    return kept + appended


def normalize_project(project: dict[str, Any], paths: Paths, config: dict[str, Any]) -> dict[str, Any]:
    result = dict(project)
    result["schema_version"] = PROJECT_SCHEMA_VERSION
    result.setdefault("project_name", paths.project_root.name)
    result.setdefault("created_at", now_utc())
    result.setdefault("updated_at", result.get("created_at") or now_utc())
    if result.get("app") in {None, ""}:
        result["app"] = "Transcritorio"
    result.setdefault("media_policy", "referenciar")
    result["paths"] = {
        **{
            "metadata_file": METADATA_FILENAME,
            "work_dir": relative_to(paths.output_root, paths.project_root),
            "jobs_file": relative_to(jobs_path(paths), paths.project_root),
        },
        **dict(result.get("paths") or {}),
    }
    defaults = default_transcription_settings(config)
    defaults.update(dict(result.get("defaults") or {}))
    result["defaults"] = defaults
    result.setdefault("interview_order", [])
    result.setdefault("manual_order_active", False)
    return result


def default_transcription_settings(config: dict[str, Any]) -> dict[str, Any]:
    language = config.get("asr_language") or "auto"
    count = config.get("diarization_num_speakers") or config.get("min_speakers") or 2
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        count_int = 2
    labels = ["Entrevistador", "Entrevistado"] if count_int == 2 else [f"Falante {index}" for index in range(1, count_int + 1)]
    return {
        "language": str(language),
        "speaker_mode": "exact",
        "speaker_count": count_int,
        "min_speakers": config.get("min_speakers"),
        "max_speakers": config.get("max_speakers"),
        "speaker_labels": labels,
        "context_mode": "empty",
        "context_text": "",
        "use_context_as_prompt": False,
    }


def sync_file_metadata(paths: Paths, config: dict[str, Any], rows: list[dict[str, str]], project: dict[str, Any]) -> dict[str, dict[str, str]]:
    path = metadata_path(paths)
    existing = read_file_metadata(path)
    synced: dict[str, dict[str, str]] = {}
    for row in selected_rows(rows):
        file_id = row.get("interview_id", "")
        if not file_id:
            continue
        previous = existing.get(file_id, {})
        item = default_file_metadata(row, project)
        item.update({key: value for key, value in previous.items() if key in METADATA_COLUMNS and value not in {None, ""}})
        item["file_id"] = file_id
        for source_key in [
            "source_path",
            "person_folder",
            "source_ext",
            "source_kind",
            "source_size_bytes",
            "source_mtime_utc",
            "duration_sec",
            "source_audio_streams",
            "source_audio_codec",
            "source_sample_rate",
            "source_audio_channels",
            "source_channel_layout",
            "source_bit_rate",
            "source_video_streams",
            "source_video_codec",
            "source_video_width",
            "source_video_height",
            "source_video_frame_rate",
            "source_format_name",
            "source_format_long_name",
            "probe_status",
        ]:
            item[source_key] = row.get(source_key, item.get(source_key, ""))
        synced[file_id] = item
    write_file_metadata(path, synced)
    return synced


def default_file_metadata(row: dict[str, str], project: dict[str, Any]) -> dict[str, str]:
    defaults = dict(project.get("defaults") or {})
    labels = defaults.get("speaker_labels") or ["Falante 1", "Falante 2"]
    return {
        "file_id": row.get("interview_id", ""),
        "title": row.get("interview_id", "") or Path(row.get("source_path", "")).stem,
        "source_path": row.get("source_path", ""),
        "person_folder": row.get("person_folder", ""),
        "source_ext": row.get("source_ext", ""),
        "source_kind": row.get("source_kind", ""),
        "source_size_bytes": row.get("source_size_bytes", ""),
        "source_mtime_utc": row.get("source_mtime_utc", ""),
        "duration_sec": row.get("duration_sec", ""),
        "source_audio_streams": row.get("source_audio_streams", ""),
        "source_audio_codec": row.get("source_audio_codec", ""),
        "source_sample_rate": row.get("source_sample_rate", ""),
        "source_audio_channels": row.get("source_audio_channels", ""),
        "source_channel_layout": row.get("source_channel_layout", ""),
        "source_bit_rate": row.get("source_bit_rate", ""),
        "source_video_streams": row.get("source_video_streams", ""),
        "source_video_codec": row.get("source_video_codec", ""),
        "source_video_width": row.get("source_video_width", ""),
        "source_video_height": row.get("source_video_height", ""),
        "source_video_frame_rate": row.get("source_video_frame_rate", ""),
        "source_format_name": row.get("source_format_name", ""),
        "source_format_long_name": row.get("source_format_long_name", ""),
        "probe_status": row.get("probe_status", ""),
        "language": str(defaults.get("language") or "auto"),
        "speaker_mode": str(defaults.get("speaker_mode") or "auto"),
        "speaker_count": str(defaults.get("speaker_count") or ""),
        "min_speakers": str(defaults.get("min_speakers") or ""),
        "max_speakers": str(defaults.get("max_speakers") or ""),
        "speaker_labels": "|".join(str(label) for label in labels),
        "context_mode": str(defaults.get("context_mode") or "empty"),
        "context_text": str(defaults.get("context_text") or ""),
        "use_context_as_prompt": "true" if defaults.get("use_context_as_prompt") else "false",
        "notes": "",
        "updated_at": now_utc(),
    }


def read_file_metadata(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        result = {}
        for row in csv.DictReader(handle):
            file_id = row.get("file_id", "")
            if file_id:
                result[file_id] = {column: row.get(column, "") for column in METADATA_COLUMNS}
        return result


def write_file_metadata(path: Path, items: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for file_id in sorted(items):
            row = {column: items[file_id].get(column, "") for column in METADATA_COLUMNS}
            writer.writerow(row)


def update_metadata_for_ids(paths: Paths, file_ids: list[str], updates: dict[str, str]) -> dict[str, dict[str, str]]:
    items = read_file_metadata(metadata_path(paths))
    now = now_utc()
    for file_id in file_ids:
        item = items.setdefault(file_id, {"file_id": file_id})
        for key, value in updates.items():
            if key in METADATA_COLUMNS:
                item[key] = value
        item["updated_at"] = now
    write_file_metadata(metadata_path(paths), items)
    return items


def speaker_labels_for_metadata(metadata: dict[str, str] | None) -> list[str]:
    labels = [label.strip() for label in str((metadata or {}).get("speaker_labels") or "").split("|") if label.strip()]
    return labels or ["Entrevistador", "Entrevistado"]


def metadata_display(metadata: dict[str, str] | None) -> dict[str, str]:
    metadata = metadata or {}
    language = metadata.get("language") or "auto"
    speaker_mode = metadata.get("speaker_mode") or "auto"
    if speaker_mode == "exact":
        speakers = metadata.get("speaker_count") or "exato"
    elif speaker_mode == "range":
        speakers = f"{metadata.get('min_speakers') or '?'}-{metadata.get('max_speakers') or '?'}"
    else:
        speakers = "Automático"
    context_mode = metadata.get("context_mode") or "empty"
    if context_mode == "custom" and metadata.get("context_text"):
        context = "Personalizado"
    elif context_mode == "project":
        context = "Projeto"
    else:
        context = "Vazio"
    return {
        "language": LANGUAGE_LABELS.get(language, language),
        "speakers": speakers,
        "speaker_labels": ", ".join(speaker_labels_for_metadata(metadata)),
        "context": context,
    }


def config_with_file_metadata(base_config: dict[str, Any], metadata: dict[str, str] | None) -> dict[str, Any]:
    config = dict(base_config)
    metadata = metadata or {}
    language = (metadata.get("language") or "").strip()
    if language and language != "project":
        config["asr_language"] = None if language == "auto" else language

    speaker_mode = (metadata.get("speaker_mode") or "").strip()
    if speaker_mode == "auto":
        config["diarization_num_speakers"] = None
        config["min_speakers"] = None
        config["max_speakers"] = None
    elif speaker_mode == "exact":
        count = int(metadata.get("speaker_count") or config.get("diarization_num_speakers") or 2)
        config["diarization_num_speakers"] = count
        config["min_speakers"] = count
        config["max_speakers"] = count
    elif speaker_mode == "range":
        config["diarization_num_speakers"] = None
        config["min_speakers"] = int(metadata.get("min_speakers") or config.get("min_speakers") or 1)
        config["max_speakers"] = int(metadata.get("max_speakers") or config.get("max_speakers") or config["min_speakers"])

    if metadata.get("use_context_as_prompt") == "true" and metadata.get("context_text"):
        config["asr_initial_prompt"] = " ".join(str(metadata["context_text"]).split())
    config["speaker_labels"] = speaker_labels_for_metadata(metadata)
    return config


def sync_jobs(paths: Paths, rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    path = jobs_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_json(path) if path.exists() else {}
    if not isinstance(existing, dict):
        existing = {}
    jobs = dict(existing)
    for row in selected_rows(rows):
        file_id = row.get("interview_id", "")
        if not file_id:
            continue
        previous = jobs.get(file_id) if isinstance(jobs.get(file_id), dict) else {}
        if previous.get("status") in {"Na fila", "Cancelando", "Falha"}:
            jobs[file_id] = previous
        elif previous.get("status") == "Rodando":
            # Stale "Rodando" job from a crash — reset to artifact-based status
            jobs[file_id] = {
                **job_from_artifacts(paths, row),
                "last_error": "Tarefa interrompida (provável crash ou fechamento forçado).",
                **{key: previous[key] for key in ("queued_at", "started_at") if key in previous},
            }
        else:
            jobs[file_id] = {
                **job_from_artifacts(paths, row),
                **{
                    key: value
                    for key, value in previous.items()
                    if key in {"last_error", "updated_at", "queued_at", "started_at", "finished_at"}
                },
            }
    write_json(path, jobs)
    return jobs


def update_job(paths: Paths, file_id: str, updates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = jobs_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = read_json(path) if path.exists() else {}
    if not isinstance(jobs, dict):
        jobs = {}
    previous = jobs.get(file_id) if isinstance(jobs.get(file_id), dict) else {"file_id": file_id}
    item = dict(previous)
    item["file_id"] = file_id
    item.update(updates)
    item["updated_at"] = now_utc()
    jobs[file_id] = item
    write_json(path, jobs)
    return jobs


def job_from_artifacts(paths: Paths, row: dict[str, str]) -> dict[str, Any]:
    file_id = row["interview_id"]
    wav_exists = (paths.project_root / row.get("wav_path", "")).exists()
    asr_exists = any(
        path.exists()
        for path in [
            paths.asr_dir / f"{file_id}.json",
            paths.asr_dir / "json" / f"{file_id}.json",
            paths.asr_dir / "json" / f"{file_id}.whisperx.json",
        ]
    )
    diarization_exists = any(
        path.exists()
        for path in [
            paths.diarization_dir / "json" / f"{file_id}.regular.json",
            paths.diarization_dir / "json" / f"{file_id}.exclusive.json",
        ]
    )
    canonical_exists = (paths.canonical_dir / "json" / f"{file_id}.canonical.json").exists()
    review_exists = (paths.review_dir / "edits" / f"{file_id}.review.json").exists()
    if review_exists or canonical_exists:
        status, stage, progress = "Pronto para revisar", "transcrição montada", 100
    elif diarization_exists:
        status, stage, progress = "Falantes identificados", "montar transcrição", 82
    elif asr_exists:
        status, stage, progress = "Transcrito", "identificar falantes", 65
    elif wav_exists:
        status, stage, progress = "Áudio preparado", "transcrever", 20
    else:
        status, stage, progress = "Pendente", "preparar áudio", 0
    return {
        "file_id": file_id,
        "status": status,
        "stage": stage,
        "progress": progress,
        "duration_sec": row.get("duration_sec", ""),
        "updated_at": now_utc(),
        "last_error": "",
    }
