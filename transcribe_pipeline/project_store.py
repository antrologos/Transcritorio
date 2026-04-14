from __future__ import annotations

from pathlib import Path
from typing import Any
import csv

from .config import Paths
from .manifest import selected_rows
from .utils import now_utc, read_json, relative_to, write_json


PROJECT_SCHEMA_VERSION = 1
PROJECT_FILENAME = "projeto.transcricao.json"
METADATA_FILENAME = "metadados.csv"
INTERNAL_PROJECT_DIR = "00_project"
JOBS_FILENAME = "jobs.json"

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


def project_path(paths: Paths) -> Path:
    return paths.project_root / PROJECT_FILENAME


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
        if previous.get("status") in {"Na fila", "Rodando", "Cancelando", "Falha"}:
            jobs[file_id] = previous
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
