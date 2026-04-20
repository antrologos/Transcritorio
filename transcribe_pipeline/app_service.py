from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .audio import prepare_audio
from .config import DEFAULT_CONFIG, Paths, ensure_directories, load_config, make_paths, write_config
from .diarization import run_pyannote_diarization
from .manifest import build_manifest, read_manifest, write_manifest
from . import model_manager
from . import project_store
from .qc import run_qc
from .render import render_outputs, write_empty_speaker_map
from .review_store import create_review_from_canonical, export_review_outputs, load_review_transcript, save_review_transcript
from .status import InterviewStatus, collect_status
from .whisperx_runner import run_whisperx


CONFIG_REL_PATH = Path("Transcricoes/00_config/run_config.yaml")
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class ProjectContext:
    config_path: Path
    config: dict[str, Any]
    paths: Paths
    rows: list[dict[str, str]]
    project: dict[str, Any]
    metadata: dict[str, dict[str, str]]
    jobs: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class JobResult:
    stage: str
    failures: int
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.failures == 0


def resolve_config_path(project_root: Path | None = None) -> Path | None:
    """Resolve the config path for a project root. Returns None if no project found."""
    if project_root is not None:
        return Path(project_root).resolve() / CONFIG_REL_PATH
    # Fallback: check CWD for a project
    cwd = Path.cwd().resolve()
    if project_store.find_project_file(cwd) is not None or (cwd / CONFIG_REL_PATH).exists():
        return cwd / CONFIG_REL_PATH
    return None


def load_project(config_path: Path | None = None, project_root: Path | None = None) -> ProjectContext:
    if config_path is None:
        config_path = resolve_config_path(project_root)
    if config_path is None:
        raise FileNotFoundError("Nenhum projeto encontrado. Use --project ou abra a partir da pasta do projeto.")
    config = load_config(config_path)
    config_path = Path(config_path)
    paths = make_paths(config, base_dir=infer_project_root_from_config_path(config_path))
    ensure_directories(paths)
    if not config_path.exists():
        write_config(config_path, config, header=["# Local transcription pipeline configuration."])
    write_empty_speaker_map(paths.manifest_dir / "speakers_map.csv")
    manifest_path = paths.manifest_dir / "manifest.csv"
    rows = read_manifest(manifest_path) if manifest_path.exists() else []
    return build_context(config_path, config, paths, rows)


def infer_project_root_from_config_path(config_path: Path) -> Path:
    path = Path(config_path).resolve()
    if path.parent.name == "00_config" and path.parent.parent.name == "Transcricoes":
        return path.parent.parent.parent
    return Path.cwd().resolve()


def config_path_for_project_root(project_root: Path) -> Path:
    return project_root / CONFIG_REL_PATH


def create_project(project_root: Path, project_name: str | None = None) -> ProjectContext:
    project_root.mkdir(parents=True, exist_ok=True)
    config_path = config_path_for_project_root(project_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = dict(DEFAULT_CONFIG)
    config["project_root"] = "."
    config["audio_globs"] = []
    config["audio_files"] = []
    config["audio_roots"] = []
    write_config(config_path, config, header=["# Local transcription pipeline configuration."])
    paths = make_paths(config, base_dir=project_root)
    ensure_directories(paths)
    context = build_context(config_path, config, paths, [])
    if project_name:
        context.project["project_name"] = project_name
        context = save_project_metadata(context)
    return context


def open_project(project_reference: Path) -> ProjectContext:
    reference = project_reference.resolve()
    if reference.is_file():
        if reference.suffix == project_store.PROJECT_EXTENSION:
            return load_project(config_path_for_project_root(reference.parent))
        return load_project(reference)
    config_path = config_path_for_project_root(reference)
    if config_path.exists():
        return load_project(config_path)
    return create_project(reference, project_name=reference.name)


def build_context(config_path: Path, config: dict[str, Any], paths: Paths, rows: list[dict[str, str]]) -> ProjectContext:
    project = project_store.ensure_project(paths, config)
    metadata = project_store.sync_file_metadata(paths, config, rows, project)
    jobs = project_store.sync_jobs(paths, rows)
    return ProjectContext(config_path=config_path, config=config, paths=paths, rows=rows, project=project, metadata=metadata, jobs=jobs)


def refresh_manifest(context: ProjectContext, hash_files: bool = False) -> ProjectContext:
    rows = build_manifest(context.config, context.paths, hash_files=hash_files)
    write_manifest(rows, context.paths.manifest_dir / "manifest.csv")
    return build_context(context.config_path, context.config, context.paths, rows)


def add_audio_root(context: ProjectContext, folder: Path) -> ProjectContext:
    config = dict(context.config)
    roots = [str(item) for item in config.get("audio_roots", [])]
    folder_text = str(folder.resolve())
    if folder_text not in roots:
        roots.append(folder_text)
    config["audio_roots"] = roots
    paths = make_paths(config, base_dir=context.paths.project_root)
    ensure_directories(paths)
    new_context = refresh_manifest(build_context(context.config_path, config, paths, context.rows))
    write_config(
        context.config_path,
        config,
        header=[
            "# Local transcription pipeline configuration.",
            "# audio_roots entries can point to additional folders selected in the Review Studio.",
        ],
    )
    return new_context


def add_audio_files(context: ProjectContext, files: list[Path]) -> ProjectContext:
    config = dict(context.config)
    configured = [str(item) for item in config.get("audio_files", [])]
    for file_path in files:
        file_text = str(file_path.resolve())
        if file_text not in configured:
            configured.append(file_text)
    config["audio_files"] = configured
    paths = make_paths(config, base_dir=context.paths.project_root)
    ensure_directories(paths)
    new_context = refresh_manifest(build_context(context.config_path, config, paths, context.rows))
    write_config(
        context.config_path,
        config,
        header=[
            "# Local transcription pipeline configuration.",
            "# audio_files entries point to individual media files selected in the Review Studio.",
        ],
    )
    return new_context


def list_interviews(context: ProjectContext, ids: list[str] | None = None) -> list[InterviewStatus]:
    return collect_status(context.rows, context.paths, ids=ids)


def get_interview_row(context: ProjectContext, interview_id: str) -> dict[str, str]:
    for row in context.rows:
        if row.get("selected") == "true" and row.get("interview_id") == interview_id:
            return row
    raise KeyError(f"Interview not found in manifest: {interview_id}")


def get_media_path(context: ProjectContext, interview_id: str) -> Path:
    candidates = get_media_candidates(context, interview_id)
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"Media file not found for {interview_id}")


def get_media_candidates(context: ProjectContext, interview_id: str) -> list[Path]:
    row = get_interview_row(context, interview_id)
    candidates: list[Path] = []
    source_path = context.paths.project_root / row.get("source_path", "")
    if source_path.exists():
        candidates.append(source_path)
    wav_path = context.paths.project_root / row.get("wav_path", "")
    if wav_path.exists() and wav_path not in candidates:
        candidates.append(wav_path)
    return candidates


def prepare_interviews(context: ProjectContext, ids: list[str] | None = None, force: bool = False) -> JobResult:
    failures = prepare_audio(context.rows, context.config, context.paths, ids=ids, force=force, dry_run=False)
    return JobResult("prepare-audio", failures)


def transcribe_interviews(
    context: ProjectContext,
    ids: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> JobResult:
    failures = 0
    for interview_id in selected_ids(context, ids):
        config = project_store.config_with_file_metadata(merged_config(context.config, overrides), context.metadata.get(interview_id))
        failures += run_whisperx(
            context.rows,
            config,
            context.paths,
            ids=[interview_id],
            dry_run=False,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
    return JobResult("transcribe", failures)


def diarize_interviews(
    context: ProjectContext,
    ids: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> JobResult:
    failures = 0
    for interview_id in selected_ids(context, ids):
        config = project_store.config_with_file_metadata(merged_config(context.config, overrides), context.metadata.get(interview_id))
        failures += run_pyannote_diarization(
            context.rows, config, context.paths, ids=[interview_id], dry_run=False,
            progress_callback=progress_callback, should_cancel=should_cancel,
        )
    return JobResult("diarize", failures)


def render_interviews(context: ProjectContext, ids: list[str] | None = None, overrides: dict[str, Any] | None = None) -> JobResult:
    failures = 0
    for interview_id in selected_ids(context, ids):
        config = project_store.config_with_file_metadata(merged_config(context.config, overrides), context.metadata.get(interview_id))
        failures += render_outputs(context.rows, config, context.paths, ids=[interview_id])
    return JobResult("render", failures)


def qc_interviews(context: ProjectContext, ids: list[str] | None = None) -> JobResult:
    failures = run_qc(context.rows, context.config, context.paths, ids=ids)
    return JobResult("qc", failures)


def models_status_text() -> str:
    return model_manager.status_text()


def required_models_ready() -> bool:
    return model_manager.all_required_models_cached()


def download_models(
    token: str | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    asr_variants: list[str] | None = None,
) -> JobResult:
    failures = model_manager.download_required_models(token=token, progress_callback=progress_callback, should_cancel=should_cancel, asr_variants=asr_variants)
    if failures:
        return JobResult("models", failures, "Falha ao baixar um ou mais modelos.")
    verify_failures = model_manager.verify_required_models(progress_callback=progress_callback)
    return JobResult("models", verify_failures, "Modelos prontos para uso local.")


def verify_models(progress_callback: ProgressCallback | None = None) -> JobResult:
    failures = model_manager.verify_required_models(progress_callback=progress_callback)
    return JobResult("models", failures, "Modelos prontos para uso local." if failures == 0 else "Modelos ausentes ou incompletos.")


def load_review(context: ProjectContext, interview_id: str, create: bool = True) -> dict[str, Any]:
    return load_review_transcript(context.paths, interview_id, create=create)


def save_review(context: ProjectContext, interview_id: str, review: dict[str, Any]) -> None:
    save_review_transcript(context.paths, interview_id, review)


def rebuild_review(context: ProjectContext, interview_id: str) -> JobResult:
    create_review_from_canonical(context.paths, interview_id)
    return JobResult("review", 0)


def export_review(context: ProjectContext, interview_id: str, formats: list[str] | None = None) -> list[Path]:
    return export_review_outputs(context.paths, interview_id, formats=formats)


def update_file_metadata(context: ProjectContext, ids: list[str], updates: dict[str, str]) -> ProjectContext:
    metadata = project_store.update_metadata_for_ids(context.paths, ids, updates)
    return ProjectContext(
        config_path=context.config_path,
        config=context.config,
        paths=context.paths,
        rows=context.rows,
        project=context.project,
        metadata=metadata,
        jobs=context.jobs,
    )


def update_job(context: ProjectContext, interview_id: str, updates: dict[str, Any]) -> ProjectContext:
    jobs = project_store.update_job(context.paths, interview_id, updates)
    return ProjectContext(
        config_path=context.config_path,
        config=context.config,
        paths=context.paths,
        rows=context.rows,
        project=context.project,
        metadata=context.metadata,
        jobs=jobs,
    )


def update_engine_config(context: ProjectContext, updates: dict[str, Any]) -> ProjectContext:
    config = dict(context.config)
    for key, value in updates.items():
        if value is not None:
            config[key] = value
    paths = make_paths(config, base_dir=context.paths.project_root)
    ensure_directories(paths)
    new_context = build_context(context.config_path, config, paths, context.rows)
    write_config(context.config_path, config, header=["# Local transcription pipeline configuration."])
    return new_context


def delete_transcription_outputs(context: ProjectContext, ids: list[str]) -> tuple[int, ProjectContext]:
    """Delete all derived transcription files for given interview IDs.

    Removes outputs in 02_asr_raw through 06_qc. Never touches original
    source files or 00_config/00_manifest/00_project/01_audio directories.
    Resets job status to 'Pendente'.
    """
    deleted = 0
    paths = context.paths
    dirs_to_clean = [
        paths.asr_dir,
        paths.asr_variants_dir,
        paths.diarization_dir,
        paths.canonical_dir,
        paths.review_dir,
        paths.qc_dir,
    ]
    for interview_id in ids:
        for base_dir in dirs_to_clean:
            if not base_dir.exists():
                continue
            for f in base_dir.rglob(f"{interview_id}*"):
                if f.is_file():
                    f.unlink()
                    deleted += 1
        context = update_job(context, interview_id, {
            "status": "Pendente",
            "stage": "",
            "progress": 0,
            "started_at": "",
            "finished_at": "",
            "last_error": "",
            "estimated_finish_at": "",
        })
    return deleted, context


def save_project_metadata(context: ProjectContext) -> ProjectContext:
    project = project_store.save_project(context.paths, context.project)
    metadata = project_store.sync_file_metadata(context.paths, context.config, context.rows, project)
    jobs = project_store.sync_jobs(context.paths, context.rows)
    return ProjectContext(
        config_path=context.config_path,
        config=context.config,
        paths=context.paths,
        rows=context.rows,
        project=project,
        metadata=metadata,
        jobs=jobs,
    )


def selected_ids(context: ProjectContext, ids: list[str] | None = None) -> list[str]:
    wanted = set(ids or [])
    result = []
    for row in context.rows:
        interview_id = row.get("interview_id", "")
        if row.get("selected") == "true" and interview_id and (not wanted or interview_id in wanted):
            result.append(interview_id)
    return result


def merged_config(config: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(config)
    for key, value in (overrides or {}).items():
        if value is not None:
            merged[key] = value
    return merged
