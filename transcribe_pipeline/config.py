from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json


DEFAULT_CONFIG: dict[str, Any] = {
    "project_root": ".",
    "output_root": "Transcricoes",
    "audio_globs": [],
    "audio_files": [],
    "audio_roots": [],
    "recursive_audio_scan": True,
    "media_extensions": [".mp3", ".m4a", ".mov", ".mp4", ".wav", ".flac"],
    "prefer_source_kinds": ["A", "audio", "unknown", "V"],
    "wav_sample_rate": 16000,
    "wav_channels": 1,
    "asr_model": "large-v3-turbo",
    "asr_language": "pt",
    "asr_device": "cuda",
    "asr_compute_type": "float16",
    "asr_batch_size": 8,
    "asr_beam_size": 5,
    "asr_initial_prompt": None,
    "asr_initial_prompt_file": None,
    "asr_hotwords": None,
    "asr_vad_method": None,
    "asr_vad_onset": None,
    "asr_vad_offset": None,
    "asr_chunk_size": None,
    "asr_align_model": None,
    "asr_variant": None,
    "model_cache_dir": None,
    "asr_model_cache_only": True,
    "diarize": True,
    "diarize_model": "pyannote/speaker-diarization-community-1",
    "diarization_num_speakers": 2,
    "diarization_source": "pyannote_exclusive",
    "min_speakers": 2,
    "max_speakers": 2,
    "diarization_clustering_threshold": None,
    "diarization_min_duration_off": None,
    "model_download_token_env": "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN",
    "pyannote_metrics_enabled": "0",
    "tcle_globs": [],
    "manifest_probe_audio": True,
    "turn_gap_seconds": 1.8,
    "max_turn_seconds": 90.0,
}


@dataclass(frozen=True)
class Paths:
    project_root: Path
    output_root: Path
    config_dir: Path
    manifest_dir: Path
    wav_dir: Path
    asr_dir: Path
    asr_variants_dir: Path
    diarization_dir: Path
    canonical_dir: Path
    review_dir: Path
    qc_dir: Path
    logs_dir: Path


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path is None or not path.exists():
        return dict(DEFAULT_CONFIG)

    text = path.read_text(encoding="utf-8")
    user_config = json.loads(text) if path.suffix.lower() == ".json" else _load_simple_yaml(text)
    merged = dict(DEFAULT_CONFIG)
    merged.update(user_config)
    return merged


def config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def make_paths(config: dict[str, Any], base_dir: Path | None = None) -> Paths:
    project_root_value = Path(str(config.get("project_root", ".")))
    if project_root_value.is_absolute():
        project_root = project_root_value.resolve()
    else:
        project_root = ((base_dir or Path.cwd()) / project_root_value).resolve()
    output_root = (project_root / config["output_root"]).resolve()
    return Paths(
        project_root=project_root,
        output_root=output_root,
        config_dir=output_root / "00_config",
        manifest_dir=output_root / "00_manifest",
        wav_dir=output_root / "01_audio_wav16k_mono",
        asr_dir=output_root / "02_asr_raw",
        asr_variants_dir=output_root / "02_asr_variants",
        diarization_dir=output_root / "03_diarization",
        canonical_dir=output_root / "04_canonical",
        review_dir=output_root / "05_transcripts_review",
        qc_dir=output_root / "06_qc",
        logs_dir=output_root / "logs",
    )


def ensure_directories(paths: Paths) -> None:
    for directory in [
        paths.config_dir,
        paths.manifest_dir,
        paths.wav_dir,
        paths.asr_dir / "json",
        paths.asr_dir / "srt",
        paths.asr_dir / "vtt",
        paths.asr_dir / "txt",
        paths.asr_dir / "tsv",
        paths.asr_variants_dir,
        paths.diarization_dir / "rttm",
        paths.diarization_dir / "json",
        paths.canonical_dir / "json",
        paths.canonical_dir / "jsonl",
        paths.review_dir / "md",
        paths.review_dir / "docx",
        paths.qc_dir / "samples",
        paths.logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def write_default_config(path: Path) -> None:
    if path.exists():
        return
    write_config(path, DEFAULT_CONFIG, header=["# Local transcription pipeline configuration.", "# Edit values after the pilot if needed."])


def write_config(path: Path, config: dict[str, Any], header: list[str] | None = None) -> None:
    lines = [
        *(header or []),
    ]
    for key, value in config.items():
        lines.extend(_yaml_lines(key, value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _yaml_lines(key: str, value: Any) -> list[str]:
    if value is None:
        return [f"{key}: null"]
    if isinstance(value, bool):
        return [f"{key}: {'true' if value else 'false'}"]
    if isinstance(value, (int, float)):
        return [f"{key}: {value}"]
    if isinstance(value, list):
        return [f"{key}:"] + [f"  - {item}" for item in value]
    return [f"{key}: {value}"]


def _load_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_key:
            result.setdefault(current_key, []).append(_parse_scalar(line[4:].strip()))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        result[key] = [] if value == "" else _parse_scalar(value)
    return result


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value.strip("\"'")
