from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import model_manager
from .audio import prepare_audio
from .config import ensure_directories, load_config, make_paths, write_default_config
from .diarization import run_pyannote_diarization
from .manifest import build_manifest, read_manifest, write_manifest
from .qc import run_qc
from .render import render_outputs, write_empty_speaker_map
from .whisperx_runner import run_whisperx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local interview transcription pipeline.")
    parser.add_argument("--project", type=Path, default=None, help="Project root directory (contains projeto.transcricao.json).")
    parser.add_argument("--config", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create directory structure and default config.")
    init_parser.set_defaults(func=cmd_init)

    manifest_parser = subparsers.add_parser("manifest", help="Build manifest.csv from Audios_* folders.")
    manifest_parser.add_argument("--hash", action="store_true", help="Hash selected source files with SHA-256.")
    manifest_parser.set_defaults(func=cmd_manifest)

    audio_parser = subparsers.add_parser("prepare-audio", help="Convert selected sources to 16 kHz mono WAV.")
    add_ids_arg(audio_parser)
    audio_parser.add_argument("--force", action="store_true")
    audio_parser.add_argument("--dry-run", action="store_true")
    audio_parser.set_defaults(func=cmd_prepare_audio)

    transcribe_parser = subparsers.add_parser("transcribe", help="Run WhisperX on prepared WAV files.")
    add_ids_arg(transcribe_parser)
    transcribe_parser.add_argument("--dry-run", action="store_true")
    add_transcribe_overrides(transcribe_parser)
    transcribe_parser.set_defaults(func=cmd_transcribe)

    diarize_parser = subparsers.add_parser("diarize", help="Run local pyannote diarization and save regular/exclusive outputs.")
    add_ids_arg(diarize_parser)
    diarize_parser.add_argument("--dry-run", action="store_true")
    diarize_parser.add_argument("--num-speakers", type=int, dest="diarization_num_speakers")
    diarize_parser.add_argument("--min-speakers", type=int, dest="min_speakers")
    diarize_parser.add_argument("--max-speakers", type=int, dest="max_speakers")
    diarize_parser.add_argument("--diarize-model", dest="diarize_model")
    diarize_parser.set_defaults(func=cmd_diarize)

    render_parser = subparsers.add_parser("render", help="Render canonical JSON, Markdown, DOCX and TSV.")
    add_ids_arg(render_parser)
    render_parser.set_defaults(func=cmd_render)

    qc_parser = subparsers.add_parser("qc", help="Run basic QC over canonical JSON outputs.")
    add_ids_arg(qc_parser)
    qc_parser.set_defaults(func=cmd_qc)

    models_parser = subparsers.add_parser("models", help="Manage local ASR/diarization models.")
    models_subparsers = models_parser.add_subparsers(dest="models_command", required=True)
    models_status_parser = models_subparsers.add_parser("status", help="Show required model cache status.")
    models_status_parser.add_argument("--json", action="store_true", dest="as_json")
    models_status_parser.set_defaults(func=cmd_models_status)

    models_download_parser = models_subparsers.add_parser("download", help="Download required models using the user's Hugging Face token.")
    models_download_parser.add_argument("--token-env", default="TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN", help="Environment variable that holds the user's Hugging Face token.")
    models_download_parser.add_argument("--force", action="store_true", help="Force Hugging Face snapshot download.")
    models_download_parser.set_defaults(func=cmd_models_download)

    models_verify_parser = models_subparsers.add_parser("verify", help="Verify that required models load from the local cache only.")
    models_verify_parser.add_argument("--json", action="store_true", dest="as_json")
    models_verify_parser.set_defaults(func=cmd_models_verify)

    args = parser.parse_args(argv)
    return args.func(args)


def add_ids_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ids", nargs="*", help="Optional interview IDs, e.g. A01P_0608 G01R_0718.")


def add_transcribe_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", dest="asr_model")
    parser.add_argument("--language", dest="asr_language")
    parser.add_argument("--device", dest="asr_device")
    parser.add_argument("--compute-type", choices=["default", "float16", "float32", "int8"], dest="asr_compute_type")
    parser.add_argument("--batch-size", type=int, dest="asr_batch_size")
    parser.add_argument("--beam-size", type=int, dest="asr_beam_size")
    parser.add_argument("--initial-prompt", dest="asr_initial_prompt")
    parser.add_argument("--initial-prompt-file", type=Path, dest="asr_initial_prompt_file")
    parser.add_argument("--hotwords", dest="asr_hotwords")
    parser.add_argument("--vad-method", choices=["pyannote", "silero"], dest="asr_vad_method")
    parser.add_argument("--vad-onset", type=float, dest="asr_vad_onset")
    parser.add_argument("--vad-offset", type=float, dest="asr_vad_offset")
    parser.add_argument("--chunk-size", type=int, dest="asr_chunk_size")
    parser.add_argument("--align-model", dest="asr_align_model")
    parser.add_argument("--variant", dest="asr_variant", help="Write ASR outputs under Transcricoes/02_asr_variants/NAME.")
    parser.add_argument("--diarize-model", dest="diarize_model")
    parser.add_argument("--min-speakers", type=int, dest="min_speakers")
    parser.add_argument("--max-speakers", type=int, dest="max_speakers")
    diarize_group = parser.add_mutually_exclusive_group()
    diarize_group.add_argument("--diarize", action="store_true", dest="diarize_override")
    diarize_group.add_argument("--no-diarize", action="store_false", dest="diarize_override")
    parser.set_defaults(diarize_override=None)


def apply_overrides(config: dict, args: argparse.Namespace, keys: list[str]) -> None:
    for key in keys:
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    if getattr(args, "diarize_override", None) is not None:
        config["diarize"] = args.diarize_override


def resolve_config(args: argparse.Namespace) -> Path:
    if args.config is not None:
        return args.config
    from .app_service import resolve_config_path, CONFIG_REL_PATH
    resolved = resolve_config_path(args.project)
    if resolved is not None:
        return resolved
    # Last resort: CWD-relative default
    return Path.cwd().resolve() / CONFIG_REL_PATH


def load_context(args: argparse.Namespace):
    config_path = resolve_config(args)
    config = load_config(config_path)
    from .app_service import infer_project_root_from_config_path
    base_dir = infer_project_root_from_config_path(config_path)
    paths = make_paths(config, base_dir=base_dir)
    ensure_directories(paths)
    return config, paths


def cmd_init(args: argparse.Namespace) -> int:
    project_root = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    config = load_config(None)
    paths = make_paths(config, base_dir=project_root)
    ensure_directories(paths)
    write_default_config(paths.config_dir / "run_config.yaml")
    write_empty_speaker_map(paths.manifest_dir / "speakers_map.csv")
    print(f"Initialized {paths.output_root}")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    write_default_config(paths.config_dir / "run_config.yaml")
    write_empty_speaker_map(paths.manifest_dir / "speakers_map.csv")
    rows = build_manifest(config, paths, hash_files=args.hash)
    output_path = paths.manifest_dir / "manifest.csv"
    write_manifest(rows, output_path)
    selected = sum(1 for row in rows if row["selected"] == "true")
    duplicates = sum(1 for row in rows if row["selected"] != "true")
    print(f"Wrote {output_path}")
    print(f"Rows: {len(rows)}; selected interviews: {selected}; duplicates: {duplicates}")
    return 0


def cmd_prepare_audio(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    rows = load_manifest_or_exit(paths)
    return prepare_audio(rows, config, paths, ids=args.ids, force=args.force, dry_run=args.dry_run)


def cmd_transcribe(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    apply_overrides(
        config,
        args,
        [
            "asr_model",
            "asr_language",
            "asr_device",
            "asr_compute_type",
            "asr_batch_size",
            "asr_beam_size",
            "asr_initial_prompt",
            "asr_initial_prompt_file",
            "asr_hotwords",
            "asr_vad_method",
            "asr_vad_onset",
            "asr_vad_offset",
            "asr_chunk_size",
            "asr_align_model",
            "asr_variant",
            "diarize_model",
            "min_speakers",
            "max_speakers",
        ],
    )
    apply_initial_prompt_file(config, paths)
    rows = load_manifest_or_exit(paths)
    return run_whisperx(rows, config, paths, ids=args.ids, dry_run=args.dry_run)


def cmd_diarize(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    apply_overrides(config, args, ["diarization_num_speakers", "diarize_model", "min_speakers", "max_speakers"])
    rows = load_manifest_or_exit(paths)
    return run_pyannote_diarization(rows, config, paths, ids=args.ids, dry_run=args.dry_run)


def cmd_render(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    rows = load_manifest_or_exit(paths)
    return render_outputs(rows, config, paths, ids=args.ids)


def cmd_qc(args: argparse.Namespace) -> int:
    config, paths = load_context(args)
    rows = load_manifest_or_exit(paths)
    return run_qc(rows, config, paths, ids=args.ids)


def cmd_models_status(args: argparse.Namespace) -> int:
    if args.as_json:
        print(json.dumps(model_manager.status_as_dict(), ensure_ascii=False, indent=2))
    else:
        print(model_manager.status_text())
    return 0


def cmd_models_download(args: argparse.Namespace) -> int:
    failures = model_manager.download_required_models(token_env=args.token_env, force=args.force, progress_callback=_print_model_progress)
    if failures:
        print(f"Model download finished with {failures} failure(s).", file=sys.stderr)
        return 1
    verify_failures = model_manager.verify_required_models(progress_callback=_print_model_progress)
    if verify_failures:
        print(f"Model verification finished with {verify_failures} failure(s).", file=sys.stderr)
        return 1
    print("Modelos prontos para uso local/offline.")
    return 0


def cmd_models_verify(args: argparse.Namespace) -> int:
    failures = model_manager.verify_required_models(progress_callback=None if args.as_json else _print_model_progress)
    if args.as_json:
        payload = model_manager.status_as_dict()
        payload["verified"] = failures == 0
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if failures == 0 else 1


def _print_model_progress(detail: dict) -> None:
    message = str(detail.get("message") or "")
    progress = detail.get("progress")
    suffix = f" ({progress}%)" if progress is not None else ""
    print(f"{message}{suffix}")


def load_manifest_or_exit(paths):
    manifest_path = paths.manifest_dir / "manifest.csv"
    if not manifest_path.exists():
        print(f"Missing manifest: {manifest_path}. Run `python -m transcribe_pipeline manifest` first.", file=sys.stderr)
        raise SystemExit(2)
    return read_manifest(manifest_path)


def apply_initial_prompt_file(config: dict, paths) -> None:
    prompt_file = config.get("asr_initial_prompt_file")
    if prompt_file in {None, ""}:
        return
    if config.get("asr_initial_prompt"):
        print("Use either --initial-prompt or --initial-prompt-file, not both.", file=sys.stderr)
        raise SystemExit(2)

    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = paths.project_root / prompt_path
    if not prompt_path.exists():
        print(f"Missing prompt file: {prompt_path}", file=sys.stderr)
        raise SystemExit(2)

    prompt = " ".join(line.strip() for line in prompt_path.read_text(encoding="utf-8").splitlines() if line.strip())
    if not prompt:
        print(f"Empty prompt file: {prompt_path}", file=sys.stderr)
        raise SystemExit(2)
    config["asr_initial_prompt"] = prompt
