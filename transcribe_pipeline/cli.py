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
    # Startup diagnostics: snapshot do ambiente + faulthandler + probe
    # symlinks. Idempotente — primeira linha do download_diagnostic.log
    # documenta a sessao inteira pra debug post-mortem.
    try:
        from . import diagnostics
        diagnostics.startup_init()
    except Exception:
        pass  # nao bloqueia CLI se diag falhar
    parser = argparse.ArgumentParser(description="Local interview transcription pipeline.")
    parser.add_argument("--project", type=Path, default=None, help="Project root directory or .transcritorio file.")
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

    # Smoke test: download real + verify + load + transcribe silence usando
    # um modelo pequeno (Systran/faster-whisper-tiny, 72 MB, nao-gated).
    # Usado como gate no release.yml: se esse comando sai com exit 0 no
    # FROZEN binary, sabemos que o fluxo inteiro funciona. Ver Fase A+B
    # do pacote de defesas em depth (2026-04-23).
    models_smoke_parser = models_subparsers.add_parser(
        "smoke-test",
        help="E2E: download de um modelo pequeno + verify + load + transcribe silencio.",
    )
    models_smoke_parser.add_argument(
        "--cache-dir", type=Path, default=None,
        help="Opcional: onde gravar o cache. Default: diretorio temp novo.",
    )
    models_smoke_parser.add_argument(
        "--skip-transcribe", action="store_true",
        help="Pula o transcribe de silencio (util se faster_whisper indisp.)",
    )
    models_smoke_parser.set_defaults(func=cmd_models_smoke_test)

    st_parser = subparsers.add_parser("self-test", help="Run installation diagnostics.")
    st_parser.set_defaults(func=cmd_self_test)

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


def cmd_models_smoke_test(args: argparse.Namespace) -> int:
    """E2E smoke: download + verify + load + dry-run transcribe.

    Gate critico do release.yml: se o FROZEN binary completa este comando
    com exit 0, sabemos que a cadeia inteira funciona (download HF via
    Xet, layout de cache, symlink/copy, verify, faster_whisper load,
    transcribe call). Cobre as camadas A e B do pacote de defesas de
    2026-04-23.

    Usa `Systran/faster-whisper-tiny` (72 MB, nao-gated) pra rodar em
    ~60s em CI sem precisar de token HF.
    """
    import tempfile
    import time

    from .model_manager import (
        _manual_snapshot_download,
        _snapshot_has_weights,
        cached_snapshot_path,
    )

    TINY_REPO = "Systran/faster-whisper-tiny"
    TINY_REV = "d90ca5fe260221311c53c58e660288d3deb8d356"
    TINY_ESTIMATED = 150 * 1024 * 1024

    if args.cache_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="transcritorio-smoke-")
        cache_dir = Path(tmp.name)
        cleanup = tmp
    else:
        cache_dir = Path(args.cache_dir).resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cleanup = None

    start = time.monotonic()
    try:
        # FASE A.1: download real contra HF
        print(f"[smoke] 1/4 download {TINY_REPO}@{TINY_REV[:8]}... -> {cache_dir}")

        def _tick(msg: str, pct: int) -> None:
            if pct and pct % 25 == 0:
                print(f"[smoke]      {pct}% {msg}")

        snap = _manual_snapshot_download(
            repo_id=TINY_REPO,
            revision=TINY_REV,
            cache_dir=cache_dir,
            token=None,
            label="tiny",
            start_pct=0,
            end_pct=100,
            estimated_bytes=TINY_ESTIMATED,
            progress_callback=lambda d: _tick(d.get("message", ""), int(d.get("progress", 0))),
            should_cancel=None,
        )
        dt_dl = time.monotonic() - start
        print(f"[smoke]      download ok em {dt_dl:.1f}s")

        # FASE A.2: verify via nossas proprias funcoes
        print(f"[smoke] 2/4 verify via cached_snapshot_path + _snapshot_has_weights")
        resolved = cached_snapshot_path(TINY_REPO, cache_dir, revision=TINY_REV)
        if resolved != snap:
            print(f"[smoke] ERRO: cached_snapshot_path={resolved!r} != download path {snap!r}",
                  file=sys.stderr)
            return 1
        if not _snapshot_has_weights(snap):
            print(f"[smoke] ERRO: _snapshot_has_weights=False mesmo com cache completo em {snap}",
                  file=sys.stderr)
            return 2
        print(f"[smoke]      verify ok")

        # FASE B.1: load do modelo com faster_whisper (usando local_files_only)
        print(f"[smoke] 3/4 load faster_whisper.WhisperModel (local_files_only)")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            print(f"[smoke] ERRO: faster_whisper nao importa: {exc}", file=sys.stderr)
            return 3
        t_load = time.monotonic()
        model = WhisperModel(
            TINY_REPO,
            device="cpu",
            compute_type="int8",
            download_root=str(cache_dir),
            local_files_only=True,
        )
        print(f"[smoke]      modelo carregado em {time.monotonic()-t_load:.1f}s")

        # FASE B.2: transcribe dry-run (3s de silencio). Valida que a
        # inferencia roda end-to-end; se segfault ou qualquer erro, pega
        # aqui antes de publicar release.
        if args.skip_transcribe:
            print(f"[smoke] 4/4 SKIP transcribe (por --skip-transcribe)")
        else:
            print(f"[smoke] 4/4 transcribe 3s de silencio (dry-run)")
            try:
                import numpy as np
            except ImportError as exc:
                print(f"[smoke] ERRO: numpy nao importa: {exc}", file=sys.stderr)
                return 4
            silence = np.zeros(16000 * 3, dtype=np.float32)
            t_tx = time.monotonic()
            segments, info = model.transcribe(silence, language="pt", beam_size=1)
            _ = list(segments)  # consumir gerador
            print(f"[smoke]      transcribe ok em {time.monotonic()-t_tx:.1f}s "
                  f"(lang={getattr(info, 'language', '?')})")

        total = time.monotonic() - start
        print(f"[smoke] OK — pipeline end-to-end em {total:.1f}s")
        return 0
    except Exception as exc:
        import traceback
        print(f"[smoke] FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 10
    finally:
        if cleanup is not None:
            cleanup.cleanup()


def cmd_self_test(args: argparse.Namespace) -> int:
    """Run installation health checks."""
    from .runtime import detect_device, resolve_executable
    from . import __version__, __build__
    ok = True

    # Build info
    print(f"  Version: {__version__}, Build: {__build__}")

    # CUDA
    device = detect_device()
    if device == "cuda":
        print("  OK: CUDA available")
    else:
        print("  WARN: CUDA not available, will use CPU")

    # FFmpeg
    ffmpeg = resolve_executable("ffmpeg")
    if Path(ffmpeg).exists():
        print(f"  OK: FFmpeg found at {ffmpeg}")
    else:
        print(f"  FAIL: FFmpeg not found")
        ok = False

    # whisperx
    try:
        from whisperx.__main__ import cli as _wx_cli  # noqa: F401
        print("  OK: whisperx importable")
    except Exception as e:
        print(f"  FAIL: whisperx import: {e}")
        ok = False

    return 0 if ok else 1


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
