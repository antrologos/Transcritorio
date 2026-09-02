from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json
import re

from .config import Paths
from .runtime import resolve_executable
from .utils import relative_to, run_command, sha256_file

# Cache ffprobe results keyed by (path, mtime, size) to avoid re-probing unchanged files.
_probe_cache: dict[tuple[str, float, int], dict[str, str]] = {}


MEDIA_ID_RE = re.compile(r"^(?P<interview_id>[A-Z]\d{2}[RP]_\d{4})(?:_(?P<source_kind>A|V))?", re.IGNORECASE)
TCLE_ID_RE = re.compile(r"^(?P<code>[A-Z]\d{2}[RP])_(?P<date>\d{4})", re.IGNORECASE)

MANIFEST_COLUMNS = [
    "interview_id",
    "person_folder",
    "source_path",
    "source_ext",
    "source_kind",
    "selected",
    "duplicate_of",
    "source_size_bytes",
    "source_mtime_utc",
    "source_sha256",
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
    "wav_path",
    "has_tcle",
    "tcle_path",
    "status",
    "notes",
]


@dataclass
class MediaFile:
    interview_id: str
    person_folder: str
    path: Path
    source_ext: str
    source_kind: str


def _media_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def disambiguate_free_ids(media: list[MediaFile], known: dict[str, str] | None = None) -> list[MediaFile]:
    """Nomes livres iguais em PASTAS diferentes sao entrevistas diferentes.

    Auditoria 2026-09-02: "Maria/Entrevista.m4a" e "Jose/Entrevista.m4a"
    viravam UMA entrevista (a segunda sumia da lista como "duplicate"), e
    ids que diferem so na caixa colidiam no WAV/JSON em NTFS/APFS. Regra:
    agrupa por stem sem caixa; UMA pasta fica com o stem; as demais ganham
    " (nome da pasta)" e, se ainda colidir, " (2)", " (3)". Mesmo stem na
    MESMA pasta (.mp3 + .m4a) continua sendo a mesma gravacao (duplicata).
    Pares codificados (A01P_0608_A/_V) nao passam por aqui.

    ESTABILIDADE: `known` = {caminho resolvido sem caixa: interview_id ja
    atribuido no manifest anterior}. Quem ja tem id mantem o id (todos os
    derivados sao chaveados por ele); a pasta que ja tinha o stem nu continua
    dona dele mesmo que uma pasta nova ordene antes; so o recem-descoberto
    recebe sufixo. Sem `known`, a primeira pasta na ordem do caminho fica com
    o stem (determinista). Pura; altera os MediaFile no lugar.
    """
    known = {str(k).casefold(): str(v) for k, v in (known or {}).items() if v}
    grupos: dict[str, list[MediaFile]] = {}
    for item in media:
        if MEDIA_ID_RE.match(item.path.stem):
            continue
        grupos.setdefault(item.interview_id.casefold(), []).append(item)
    usados = {item.interview_id.casefold() for item in media} | {v.casefold() for v in known.values()}

    def _id_conhecido(pasta_itens: list[MediaFile]) -> str | None:
        return next((known[_media_key(m.path)] for m in pasta_itens if _media_key(m.path) in known), None)

    for chave, itens in grupos.items():
        pastas: dict[str, list[MediaFile]] = {}
        for item in sorted(itens, key=lambda m: str(m.path).casefold()):
            pastas.setdefault(str(item.path.resolve().parent).casefold(), []).append(item)
        if len(pastas) <= 1 and len({m.interview_id for m in itens}) <= 1:
            continue
        # Dona do stem nu: a pasta que ja o tinha; senao a primeira do caminho.
        ordem = sorted(pastas.values(), key=lambda pi: (
            0 if (_id_conhecido(pi) or "").casefold() == chave else 1,
            str(pi[0].path).casefold()))
        for pos, pasta_itens in enumerate(ordem):
            conhecido = _id_conhecido(pasta_itens)
            base = pasta_itens[0].interview_id
            if pos == 0:
                novo = conhecido if conhecido is not None else base
            elif conhecido is not None and conhecido.casefold() != chave:
                novo = conhecido  # ja tinha sufixo: mantem
            else:
                raiz = f"{base} ({pasta_itens[0].path.parent.name})"
                novo, n = raiz, 2
                while novo.casefold() in usados:
                    novo = f"{raiz} ({n})"
                    n += 1
            usados.add(novo.casefold())
            for item in pasta_itens:
                item.interview_id = novo
    return media


def known_ids_from_rows(rows: list[dict[str, str]], project_root: Path) -> dict[str, str]:
    """{caminho resolvido sem caixa: interview_id} das linhas SELECIONADAS de
    nome livre do manifest anterior (pura). Linhas 'duplicate' nao contam."""
    known: dict[str, str] = {}
    for row in rows:
        if row.get("selected") != "true":
            continue
        source = str(row.get("source_path") or "")
        interview_id = str(row.get("interview_id") or "")
        if not source or not interview_id:
            continue
        path = Path(source)
        if not path.is_absolute():
            path = project_root / path
        if MEDIA_ID_RE.match(path.stem):
            continue
        known[_media_key(path)] = interview_id
    return known


def build_manifest(config: dict, paths: Paths, hash_files: bool = False,
                   previous_rows: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    if previous_rows is None:
        manifest_path = paths.manifest_dir / "manifest.csv"
        previous_rows = read_manifest(manifest_path) if manifest_path.exists() else []
    known = known_ids_from_rows(previous_rows, paths.project_root)
    media = disambiguate_free_ids(discover_media(config, paths.project_root), known)
    tcles = discover_tcles(paths.project_root, config.get("tcle_globs"))
    grouped: dict[str, list[MediaFile]] = {}
    for item in media:
        grouped.setdefault(item.interview_id, []).append(item)

    rows: list[dict[str, str]] = []
    for interview_id in sorted(grouped):
        items = sorted(grouped[interview_id], key=lambda item: _selection_rank(item, config))
        selected = items[0]
        for item in items:
            is_selected = item.path == selected.path
            stat = item.path.stat()
            tcle_path = tcles.get(_code_part(interview_id), "")
            probe = probe_audio_metadata(item.path) if config.get("manifest_probe_audio", True) else {}
            rows.append(
                {
                    "interview_id": interview_id,
                    "person_folder": item.person_folder,
                    "source_path": relative_to(item.path, paths.project_root),
                    "source_ext": item.source_ext,
                    "source_kind": item.source_kind,
                    "selected": "true" if is_selected else "false",
                    "duplicate_of": "" if is_selected else relative_to(selected.path, paths.project_root),
                    "source_size_bytes": str(stat.st_size),
                    "source_mtime_utc": _mtime_utc(stat.st_mtime),
                    "source_sha256": sha256_file(item.path) if hash_files and is_selected else "",
                    "duration_sec": probe.get("duration_sec", ""),
                    "source_audio_streams": probe.get("source_audio_streams", ""),
                    "source_audio_codec": probe.get("source_audio_codec", ""),
                    "source_sample_rate": probe.get("source_sample_rate", ""),
                    "source_audio_channels": probe.get("source_audio_channels", ""),
                    "source_channel_layout": probe.get("source_channel_layout", ""),
                    "source_bit_rate": probe.get("source_bit_rate", ""),
                    "source_video_streams": probe.get("source_video_streams", ""),
                    "source_video_codec": probe.get("source_video_codec", ""),
                    "source_video_width": probe.get("source_video_width", ""),
                    "source_video_height": probe.get("source_video_height", ""),
                    "source_video_frame_rate": probe.get("source_video_frame_rate", ""),
                    "source_format_name": probe.get("source_format_name", ""),
                    "source_format_long_name": probe.get("source_format_long_name", ""),
                    "probe_status": probe.get("probe_status", ""),
                    "wav_path": relative_to(paths.wav_dir / f"{interview_id}.wav", paths.project_root) if is_selected else "",
                    "has_tcle": "true" if tcle_path else "false",
                    "tcle_path": relative_to(Path(tcle_path), paths.project_root) if tcle_path else "",
                    "status": "pending" if is_selected else "duplicate",
                    "notes": "",
                }
            )
    return rows


def probe_audio_metadata(path: Path) -> dict[str, str]:
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime, stat.st_size)
        if cache_key in _probe_cache:
            return _probe_cache[cache_key]
    except OSError:
        cache_key = None
    result = run_command(
        [
            resolve_executable("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=format_name,format_long_name,duration,bit_rate:stream=index,codec_type,codec_name,sample_rate,channels,channel_layout,bit_rate,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        return {"probe_status": "error"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"probe_status": "invalid_json"}

    streams = payload.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    first_audio = audio_streams[0] if audio_streams else {}
    first_video = video_streams[0] if video_streams else {}
    format_info = payload.get("format", {})
    duration = payload.get("format", {}).get("duration", "")
    probe_result = {
        "duration_sec": _format_float(duration, digits=3),
        "source_audio_streams": str(len(audio_streams)),
        "source_audio_codec": str(first_audio.get("codec_name", "")),
        "source_sample_rate": str(first_audio.get("sample_rate", "")),
        "source_audio_channels": str(first_audio.get("channels", "")),
        "source_channel_layout": str(first_audio.get("channel_layout", "")),
        "source_bit_rate": str(first_audio.get("bit_rate") or format_info.get("bit_rate", "")),
        "source_video_streams": str(len(video_streams)),
        "source_video_codec": str(first_video.get("codec_name", "")),
        "source_video_width": str(first_video.get("width", "")),
        "source_video_height": str(first_video.get("height", "")),
        "source_video_frame_rate": _format_frame_rate(first_video.get("avg_frame_rate", "")),
        "source_format_name": str(format_info.get("format_name", "")),
        "source_format_long_name": str(format_info.get("format_long_name", "")),
        "probe_status": "ok",
    }
    if cache_key is not None:
        _probe_cache[cache_key] = probe_result
    return probe_result


def write_manifest(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def selected_rows(rows: list[dict[str, str]], ids: list[str] | None = None) -> list[dict[str, str]]:
    selected = [row for row in rows if row.get("selected") == "true"]
    if ids:
        # Sem caixa: `--ids a01p_0608` casa "A01P_0608" (o id codificado e
        # forcado a maiusculas) e "entrevista maria" casa "Entrevista Maria".
        wanted = {str(i).casefold() for i in ids}
        selected = [row for row in selected if row["interview_id"].casefold() in wanted]
    return selected


def discover_media(config: dict, project_root: Path) -> list[MediaFile]:
    extensions = {ext.lower() for ext in config["media_extensions"]}
    items: list[MediaFile] = []
    seen_paths: set[Path] = set()
    for file_value in config.get("audio_files", []):
        path = Path(str(file_value))
        if not path.is_absolute():
            path = project_root / path
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        add_media_path(items, seen_paths, path)
    for glob in config["audio_globs"]:
        for directory in project_root.glob(glob):
            if not directory.is_dir():
                continue
            person_folder = directory.parent.name
            for path in directory.iterdir():
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                add_media_path(items, seen_paths, path, person_folder=person_folder)
    for root_value in config.get("audio_roots", []):
        root = Path(str(root_value))
        if not root.is_absolute():
            root = project_root / root
        if not root.is_dir():
            continue
        paths = root.rglob("*") if config.get("recursive_audio_scan", True) else root.glob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            add_media_path(items, seen_paths, path, person_folder=path.parent.name)
    return items


def add_media_path(items: list[MediaFile], seen_paths: set[Path], path: Path, person_folder: str | None = None) -> None:
    resolved = path.resolve()
    if resolved in seen_paths:
        return
    seen_paths.add(resolved)
    match = MEDIA_ID_RE.match(path.stem)
    if not match:
        interview_id = path.stem
        source_kind = _kind_from_extension(path.suffix).upper()
    else:
        interview_id = match.group("interview_id").upper()
        source_kind = (match.group("source_kind") or _kind_from_extension(path.suffix)).upper()
    items.append(MediaFile(interview_id, person_folder or path.parent.name, path, path.suffix.lower(), source_kind))


def discover_tcles(project_root: Path, tcle_globs: list[str] | None = None) -> dict[str, str]:
    tcles: dict[str, str] = {}
    for glob in (tcle_globs or []):
        for path in project_root.glob(glob):
            if not path.is_file():
                continue
            match = TCLE_ID_RE.match(path.name)
            if match:
                tcles.setdefault(match.group("code").upper(), str(path))
    return tcles


def _selection_rank(item: MediaFile, config: dict) -> tuple[int, int, str]:
    preferences = [str(value).upper() for value in config["prefer_source_kinds"]]
    try:
        kind_rank = preferences.index(item.source_kind.upper())
    except ValueError:
        kind_rank = len(preferences)
    ext_rank = {".m4a": 0, ".mp3": 1, ".wav": 2, ".flac": 3, ".mov": 4, ".mp4": 5}.get(item.source_ext, 9)
    return (kind_rank, ext_rank, item.path.name.lower())


def _kind_from_extension(extension: str) -> str:
    return "V" if extension.lower() in {".mov", ".mp4"} else "AUDIO"


def _code_part(interview_id: str) -> str:
    return interview_id.split("_", 1)[0].upper()


def _mtime_utc(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _format_float(value: object, digits: int) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def _format_frame_rate(value: object) -> str:
    text = str(value or "")
    if "/" not in text:
        return _format_float(text, digits=3) if text else ""
    numerator, denominator = text.split("/", 1)
    try:
        denominator_float = float(denominator)
        if denominator_float == 0:
            return ""
        return f"{float(numerator) / denominator_float:.3f}"
    except ValueError:
        return ""
