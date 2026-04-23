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
from .utils import read_json, write_json
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
    # Ternario CRITICO: sem isto a mensagem seria "Modelos prontos..."
    # mesmo em falha, causando UI mostrar sucesso como erro. Bug visto
    # pelo Rogerio em 2026-04-22 depois do download completar mas verify
    # retornar > 0 no frozen bundle.
    return JobResult(
        "models",
        verify_failures,
        "Modelos prontos para uso local." if verify_failures == 0
        else "Modelos ausentes ou incompletos apos o download.",
    )


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
    exported = export_review_outputs(context.paths, interview_id, formats=formats)
    # Mirror user-facing formats into {project_root}/Resultados/ (hardlink or copy fallback).
    if context.config.get("use_resultados_dir", True) and exported:
        # Filtrar por SUBPASTA (nao suffix): NVivo tem .tsv mas vive em final/nvivo/ — tecnico.
        _USER_FACING = {"docx", "md", "srt", "vtt", "csv", "tsv"}
        user_facing = [p for p in exported if p.parent.name in _USER_FACING]
        if user_facing:
            try:
                project_store.ensure_results_dir(context.paths.project_root, user_facing)
            except Exception as exc:
                import logging
                logging.getLogger("transcritorio.gui").warning("ensure_results_dir falhou: %s", exc)
    return exported


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


class InterviewBusyError(Exception):
    """Raised when an operation is attempted on an interview with an active job."""


class CollisionError(Exception):
    """Raised when restore_from_trash would overwrite existing files."""
    def __init__(self, conflicts: list[dict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} conflito(s) ao restaurar")


class RedoUnavailableError(Exception):
    """Raised when redo_trash cannot proceed (mtime drift, missing files)."""


def collect_trash_files(context: ProjectContext, ids: list[str]) -> list[dict]:
    """Enumerate every file on disk belonging to these interview ids.

    Returns list of {original, size, mtime} dicts. Includes:
    - Original source audio/video (from metadata.source_path)
    - 01_audio_wav16k_mono/{id}.wav
    - 00_project/waveforms/{id}.wf
    - All derived files in 02-06 (rglob by {id}*)
    """
    import os
    from pathlib import Path as _Path
    paths = context.paths
    dirs_to_scan = [
        paths.asr_dir,
        paths.asr_variants_dir,
        paths.diarization_dir,
        paths.canonical_dir,
        paths.review_dir,
        paths.qc_dir,
    ]
    out: list[dict] = []
    def add(p: _Path) -> None:
        if not p.exists():
            return
        try:
            st = p.stat()
            out.append({
                "original": str(p.resolve()),
                "size": int(st.st_size),
                "mtime": float(st.st_mtime),
            })
        except OSError:
            pass
    waveform_dir = paths.output_root / "00_project" / "waveforms"
    for iid in ids:
        metadata = context.metadata.get(iid, {}) or {}
        source_path = metadata.get("source_path") or ""
        if source_path:
            sp = _Path(source_path)
            if not sp.is_absolute():
                sp = paths.project_root / sp
            add(sp)
        add(paths.wav_dir / f"{iid}.wav")
        add(waveform_dir / f"{iid}.wf")
        for base in dirs_to_scan:
            if not base.exists():
                continue
            for f in base.rglob(f"{iid}*"):
                if f.is_file():
                    add(f)
    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for item in out:
        if item["original"] in seen:
            continue
        seen.add(item["original"])
        unique.append(item)
    return unique


def prepare_trash_move(context: ProjectContext, ids: list[str]) -> dict:
    """Gather snapshot + files + mtimes BEFORE the worker runs. Main thread only."""
    busy = _ids_with_active_jobs(context, ids)
    if busy:
        raise InterviewBusyError(", ".join(busy))
    trash_id = project_store.generate_trash_id()
    trash_dir = project_store.trash_root(context.paths) / trash_id
    files = collect_trash_files(context, ids)
    total_bytes = sum(f["size"] for f in files)
    snapshots = project_store.snapshot_interview_state(context.paths, ids)
    csv_mtimes = project_store.csv_mtimes_snapshot(context.paths)
    return {
        "trash_id": trash_id,
        "trash_dir": str(trash_dir),
        "project_root": str(context.paths.project_root),
        "interview_ids": list(ids),
        "files_to_move": files,
        "snapshots": snapshots,
        "csv_mtimes": csv_mtimes,
        "total_bytes": total_bytes,
    }


def finalize_trash_move(context: ProjectContext, trash_entry: dict) -> tuple[str, ProjectContext]:
    """Apos o worker terminar staging+copy+rename: main thread reescreve CSVs e
    deleta originais com retry-backoff. Retorna (trash_id, new_context)."""
    import os
    import time
    trash_id = trash_entry["trash_id"]
    ids = trash_entry["interview_ids"]
    moved_files = trash_entry.get("moved_files") or []
    # 1. Reescrever CSVs (sem os ids)
    project_store.remove_ids_from_csvs(context.paths, ids)
    # 2. Unlink originais com retry-backoff (Dropbox pode ter file handle)
    pending: list[str] = []
    for mf in moved_files:
        original = mf.get("original") or ""
        if not original:
            continue
        p = Path(original)
        if not p.exists():
            continue
        last_err = None
        for delay_ms in (0, 200, 500, 1500):
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            try:
                p.unlink()
                last_err = None
                break
            except (PermissionError, OSError) as exc:
                last_err = exc
        if last_err is not None:
            pending.append(original)
    # 3. Marcar partial no undo.json se houver pending
    trash_dir = Path(trash_entry["trash_dir"])
    if pending:
        manifest = read_json(trash_dir / project_store.TRASH_MANIFEST) or {}
        manifest["status"] = "partial"
        manifest["pending_deletes"] = pending
        write_json(trash_dir / project_store.TRASH_MANIFEST, manifest)
    # 4. Reconstruir context a partir do manifesto em disco (ja atualizado)
    new_rows = read_manifest(context.paths.manifest_dir / "manifest.csv")
    new_context = build_context(
        context.config_path, context.config, context.paths, new_rows,
    )
    return trash_id, new_context


def restore_from_trash(
    context: ProjectContext,
    trash_id: str,
    overwrite: bool = False,
) -> tuple[list[str], ProjectContext]:
    """Restaura um trash_id. Retorna (warnings, new_context) onde warnings lista
    mensagens apresentaveis ao usuario (ex. mtime divergente)."""
    import shutil
    trash_dir = project_store.trash_root(context.paths) / trash_id
    manifest_path = trash_dir / project_store.TRASH_MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(f"undo.json nao encontrado em {trash_dir}")
    manifest = read_json(manifest_path) or {}
    moved_files = manifest.get("moved_files") or []
    # 1. Precheck colisoes
    conflicts = project_store._find_collisions(moved_files)
    if conflicts and not overwrite:
        raise CollisionError(conflicts)
    # 2. Precheck CSV mtime (multi-device warning)
    warnings: list[str] = []
    snap_mtimes = manifest.get("csv_mtimes") or {}
    current_mtimes = project_store.csv_mtimes_snapshot(context.paths)
    for csv_name, snap_mt in snap_mtimes.items():
        cur = current_mtimes.get(csv_name, 0.0)
        if cur > snap_mt + 1.0:  # tolerancia 1s
            warnings.append(f"{csv_name} foi modificado desde a exclusao")
    # 3. Copy files back (nao mover; preserva .trash para redo)
    restored: list[Path] = []
    try:
        for mf in moved_files:
            original = mf.get("original") or ""
            trashed_rel = mf.get("trashed") or ""
            if not original or not trashed_rel:
                continue
            trashed_abs = trash_dir / trashed_rel
            if not trashed_abs.exists():
                continue
            target = Path(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(trashed_abs), str(target))
            restored.append(target)
    except Exception:
        # Rollback: apagar o que ja foi restaurado
        for p in restored:
            try:
                p.unlink()
            except OSError:
                pass
        raise
    # 4. Reinjetar CSV rows (reset jobs para Pendente)
    snapshots = manifest.get("snapshots") or {}
    project_store.restore_ids_to_csvs(context.paths, snapshots)
    # 5. Rebuild context a partir do manifesto em disco (ja restaurado)
    new_rows = read_manifest(context.paths.manifest_dir / "manifest.csv")
    new_context = build_context(
        context.config_path, context.config, context.paths, new_rows,
    )
    return warnings, new_context


def redo_trash(context: ProjectContext, trash_id: str) -> tuple[str, ProjectContext]:
    """Re-aplica um move_to_trash previamente desfeito. Valida que os arquivos
    ainda estao no estado esperado (mtime dentro da tolerancia) antes de mover.
    NAO refaz copy — os arquivos ja estao em .trash/<trash_id>/files/, apenas
    remove das CSVs e dos originais."""
    import time
    trash_dir = project_store.trash_root(context.paths) / trash_id
    manifest_path = trash_dir / project_store.TRASH_MANIFEST
    if not manifest_path.exists():
        raise RedoUnavailableError(f"undo.json nao encontrado em {trash_dir}")
    manifest = read_json(manifest_path) or {}
    if manifest.get("status") == "partial":
        raise RedoUnavailableError("exclusao original foi parcial")
    moved_files = manifest.get("moved_files") or []
    ids = manifest.get("interview_ids") or []
    # Validar: todos os originais existem (foi feito undo antes)
    for mf in moved_files:
        original = mf.get("original") or ""
        if not original:
            continue
        p = Path(original)
        if not p.exists():
            raise RedoUnavailableError(f"arquivo ausente: {original}")
    # Reaplicar: unlink originais + remove CSV rows
    pending: list[str] = []
    for mf in moved_files:
        original = mf.get("original") or ""
        if not original:
            continue
        p = Path(original)
        if not p.exists():
            continue
        last_err = None
        for delay_ms in (0, 200, 500, 1500):
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            try:
                p.unlink()
                last_err = None
                break
            except (PermissionError, OSError) as exc:
                last_err = exc
        if last_err is not None:
            pending.append(original)
    project_store.remove_ids_from_csvs(context.paths, ids)
    if pending:
        manifest["status"] = "partial"
        manifest["pending_deletes"] = pending
        write_json(manifest_path, manifest)
    new_rows = read_manifest(context.paths.manifest_dir / "manifest.csv")
    new_context = build_context(
        context.config_path, context.config, context.paths, new_rows,
    )
    return trash_id, new_context


def purge_trash_entries(context: ProjectContext, trash_ids: list[str]) -> int:
    """Apagar permanentemente pastas .trash/<id>/ listadas. Retorna count removed."""
    import shutil
    root = project_store.trash_root(context.paths)
    removed = 0
    for tid in trash_ids:
        d = root / tid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def _ids_with_active_jobs(context: ProjectContext, ids: list[str]) -> list[str]:
    busy = []
    for iid in ids:
        status = (context.jobs.get(iid) or {}).get("status") or ""
        if status in ("Executando", "Na fila"):
            busy.append(iid)
    return busy


def rename_interview(context: ProjectContext, interview_id: str, new_title: str) -> ProjectContext:
    """Update the display label for a single interview. Empty title resets to default."""
    busy = _ids_with_active_jobs(context, [interview_id])
    if busy:
        raise InterviewBusyError(interview_id)
    metadata = project_store.update_metadata_for_ids(context.paths, [interview_id], {"title": new_title})
    return ProjectContext(
        config_path=context.config_path,
        config=context.config,
        paths=context.paths,
        rows=context.rows,
        project=context.project,
        metadata=metadata,
        jobs=context.jobs,
    )


def set_interview_order(
    context: ProjectContext,
    ordered_ids: list[str],
    manual_active: bool = True,
) -> ProjectContext:
    """Persist a new interview_order in project.json with the given flag."""
    project = dict(context.project)
    project["interview_order"] = list(ordered_ids)
    project["manual_order_active"] = bool(manual_active)
    saved = project_store.save_project(context.paths, project)
    return ProjectContext(
        config_path=context.config_path,
        config=context.config,
        paths=context.paths,
        rows=context.rows,
        project=saved,
        metadata=context.metadata,
        jobs=context.jobs,
    )


def move_interviews(
    context: ProjectContext,
    ids: list[str],
    direction: int,
    hidden_ids: list[str] | None = None,
) -> ProjectContext:
    """Move a single interview up (-1) or down (+1). Multi-item reorder is rejected."""
    if len(ids) != 1:
        raise ValueError("move_interviews expects exactly one interview id")
    moving_id = ids[0]
    busy = _ids_with_active_jobs(context, [moving_id])
    if busy:
        raise InterviewBusyError(moving_id)
    current_ids = [row.get("interview_id", "") for row in context.rows if row.get("interview_id")]
    existing_order = list(context.project.get("interview_order") or [])
    base_order = project_store._merge_interview_order(existing_order, current_ids)
    new_order = project_store._reorder_move(base_order, moving_id, direction, set(hidden_ids or []))
    return set_interview_order(context, new_order, manual_active=True)


def ensure_interview_order_up_to_date(context: ProjectContext) -> ProjectContext:
    """When manual_order_active, append new ids and drop removed ones. No-op otherwise."""
    if not context.project.get("manual_order_active"):
        return context
    current_ids = [row.get("interview_id", "") for row in context.rows if row.get("interview_id")]
    existing_order = list(context.project.get("interview_order") or [])
    merged = project_store._merge_interview_order(existing_order, current_ids)
    if merged == existing_order:
        return context
    return set_interview_order(context, merged, manual_active=True)


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
