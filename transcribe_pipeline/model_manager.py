from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import os
import threading
import time

import shutil

from . import runtime
from .utils import sanitize_message


def _download_diag_log(message: str) -> None:
    """Append a line to the download diagnostic log. Never raises."""
    try:
        log_dir = runtime.app_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "download_diagnostic.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def _clear_stale_hf_locks(cache_dir: Path) -> int:
    """Remove stale filelock sentinels left by crashed HF downloads.

    Bug symptom (2026-04-22): huggingface_hub creates ``.locks/**/*.lock``
    files via the ``filelock`` package to serialize concurrent downloads.
    When a previous Transcritório process crashed or was killed mid-download
    (or the user force-quit during the 0% freeze), the lock file stayed on
    disk but the owning PID was gone. On the next launch,
    ``snapshot_download`` **deadlocks** waiting to acquire the stale lock —
    the log shows the cache growing to exactly the size of the lockfile
    (e.g. 40 bytes) and then staying there for minutes.

    This helper removes every ``.lock`` file we can unlink. On Windows, a
    file actively held by another process refuses to be deleted
    (``PermissionError``); we silently skip those, so running instances
    aren't affected. Stale locks get cleaned and the next download
    proceeds. Safe on a single-user desktop app.
    """
    locks_dir = cache_dir / ".locks"
    if not locks_dir.exists():
        return 0
    removed = 0
    skipped = 0
    try:
        for lock_file in locks_dir.rglob("*.lock"):
            try:
                lock_file.unlink()
                removed += 1
            except (OSError, PermissionError):
                skipped += 1
                continue
    except OSError as exc:
        _download_diag_log(f"[locks] rglob error in {locks_dir}: {exc}")
    if removed or skipped:
        _download_diag_log(
            f"[locks] cleaned {removed} stale lock(s), skipped {skipped} "
            f"in-use in {locks_dir}"
        )
    return removed

MINIMUM_DISK_GB = 10  # Minimum free disk space required for model downloads


def check_disk_space() -> dict[str, Any]:
    """Check if there is enough free disk space for model downloads."""
    cache_dir = runtime.model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(str(cache_dir))
    free_gb = usage.free / (1024 ** 3)
    if free_gb >= MINIMUM_DISK_GB:
        return {"ok": True, "free_gb": round(free_gb, 1),
                "message": f"Espaço disponível: {free_gb:.1f} GB."}
    return {"ok": False, "free_gb": round(free_gb, 1),
            "message": (f"Espaço insuficiente no disco. "
                        f"Disponível: {free_gb:.1f} GB. Necessário: pelo menos {MINIMUM_DISK_GB} GB.\n"
                        f"Libere espaço e tente novamente.")}


ProgressCallback = Callable[[dict[str, Any]], None]
ShouldCancel = Callable[[], bool]

LOCAL_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


# ---------------------------------------------------------------------------
# Token and gated-model pre-validation
# ---------------------------------------------------------------------------

def validate_token(token: str) -> dict[str, Any]:
    """Validate a HuggingFace token and return user info.

    Returns dict with keys:
      "valid": bool
      "username": str (if valid)
      "error": str (if invalid) — one of "invalid_format", "unauthorized", "network"
      "message": str — user-friendly Portuguese message
    """
    token = token.strip()
    if not token.startswith("hf_") or len(token) < 10:
        return {"valid": False, "error": "invalid_format",
                "message": "A chave deve começar com 'hf_' e ter pelo menos 10 caracteres."}
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        user = api.whoami(token=token)
        return {"valid": True, "username": user.get("name", user.get("fullname", "")),
                "message": f"Chave válida! Conectado como \"{user.get('name', '')}\"."}
    except Exception as exc:
        msg = str(exc).lower()
        if "unauthorized" in msg or "401" in msg or "invalid" in msg:
            return {"valid": False, "error": "unauthorized",
                    "message": "Chave não reconhecida. Verifique se copiou o texto completo."}
        return {"valid": False, "error": "network",
                "message": "Não foi possível conectar ao Hugging Face. Verifique sua internet."}


def check_gated_access(token: str) -> dict[str, Any]:
    """Check if the token has access to the gated pyannote model.

    Returns dict with keys:
      "access": bool
      "error": str | None — "gated", "unauthorized", "network"
      "message": str — user-friendly Portuguese message
    """
    token = token.strip()
    try:
        from huggingface_hub import model_info as hf_model_info
        hf_model_info(LOCAL_PYANNOTE_MODEL, token=token)
        return {"access": True, "error": None,
                "message": "Acesso ao modelo de identificação de falantes confirmado."}
    except Exception as exc:
        msg = str(exc).lower()
        if "gated" in msg or "403" in msg or "access" in msg:
            return {"access": False, "error": "gated",
                    "message": "Você ainda não aceitou os termos do modelo de identificação de falantes.\n"
                               "Volte ao passo anterior e aceite os termos no site."}
        if "401" in msg or "unauthorized" in msg:
            return {"access": False, "error": "unauthorized",
                    "message": "Chave não reconhecida para este modelo."}
        return {"access": False, "error": "network",
                "message": "Não foi possível verificar acesso ao modelo. Verifique sua internet."}
REMOTE_DIARIZATION_MARKERS = ("precision-2", "cloud", "pyannoteai")


@dataclass(frozen=True)
class ModelAsset:
    key: str
    label: str
    repo_id: str
    purpose: str
    gated: bool = False
    estimated_gb: float = 1.0


@dataclass(frozen=True)
class ModelStatus:
    asset: ModelAsset
    cached: bool
    path: Path | None
    message: str = ""


# ---------------------------------------------------------------------------
# ASR model variants — user can choose which to install
# ---------------------------------------------------------------------------

ASR_VARIANTS: dict[str, dict[str, Any]] = {
    "large-v3-turbo": {
        "label": "Whisper large-v3-turbo",
        "friendly_pt": "Preciso rapido (recomendado, 3,1 GB)",
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "estimated_gb": 3.1,
        "quality": 8,
        "speed": 8,
        "desc": "Recomendado. Melhor equilibrio entre qualidade e velocidade.",
    },
    "large-v3": {
        "label": "Whisper large-v3",
        "friendly_pt": "Maxima precisao (melhor qualidade, 5,8 GB)",
        "repo": "Systran/faster-whisper-large-v3",
        "estimated_gb": 5.8,
        "quality": 10,
        "speed": 4,
        "desc": "Melhor qualidade, mais lento.",
    },
    "medium": {
        "label": "Whisper medium",
        "friendly_pt": "Preciso (alta qualidade, 2,8 GB)",
        "repo": "Systran/faster-whisper-medium",
        "estimated_gb": 2.8,
        "quality": 7,
        "speed": 7,
        "desc": "Boa qualidade, mais rapido.",
    },
    "small": {
        "label": "Whisper small",
        "friendly_pt": "Equilibrado (qualidade boa, 900 MB)",
        "repo": "Systran/faster-whisper-small",
        "estimated_gb": 0.9,
        "quality": 5,
        "speed": 8,
        "desc": "Qualidade razoavel.",
    },
    "base": {
        "label": "Whisper base",
        "friendly_pt": "Equilibrado leve (qualidade boa, 300 MB)",
        "repo": "Systran/faster-whisper-base",
        "estimated_gb": 0.3,
        "quality": 3,
        "speed": 9,
        "desc": "Qualidade fraca.",
    },
    "tiny": {
        "label": "Whisper tiny",
        "friendly_pt": "Rapido (qualidade basica, 150 MB)",
        "repo": "Systran/faster-whisper-tiny",
        "estimated_gb": 0.15,
        "quality": 2,
        "speed": 10,
        "desc": "Qualidade ruim.",
    },
}

_FRIENDLY_FIXED_MODELS: dict[str, str] = {
    "alignment_pt": "Alinhamento de tempo (portugues, 6,9 GB)",
    "diarization": "Identificacao de falantes (70 MB)",
}

DEFAULT_ASR_VARIANT = "large-v3-turbo"

_FIXED_MODELS: tuple[ModelAsset, ...] = (
    ModelAsset("alignment_pt", "Alinhamento portugues", "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese", "timestamps por palavra", estimated_gb=6.9),
    ModelAsset("diarization", "Separacao de falantes", LOCAL_PYANNOTE_MODEL, "diarizacao local", gated=True, estimated_gb=0.07),
)


def get_required_models(asr_variants: list[str] | None = None) -> tuple[ModelAsset, ...]:
    """Build the list of required models based on selected ASR variants.

    Always includes alignment and diarization models.
    """
    if asr_variants is None:
        asr_variants = [DEFAULT_ASR_VARIANT]
    if not asr_variants:
        raise ValueError("Selecione ao menos um modelo ASR.")
    assets: list[ModelAsset] = []
    for variant in asr_variants:
        if variant not in ASR_VARIANTS:
            raise ValueError(f"Variante ASR desconhecida: {variant}")
        info = ASR_VARIANTS[variant]
        assets.append(ModelAsset(
            key=f"asr_{variant}",
            label=info["label"],
            repo_id=info["repo"],
            purpose="transcricao",
            estimated_gb=info["estimated_gb"],
        ))
    assets.extend(_FIXED_MODELS)
    return tuple(assets)


# Legacy constant — used by code that doesn't yet support variant selection
REQUIRED_MODELS: tuple[ModelAsset, ...] = get_required_models([DEFAULT_ASR_VARIANT])


def validate_local_diarization_model(model_name: str | os.PathLike[str] | None) -> str:
    value = str(model_name or LOCAL_PYANNOTE_MODEL).strip()
    if not value:
        return LOCAL_PYANNOTE_MODEL
    if Path(value).exists():
        return value
    lowered = value.lower()
    if any(marker in lowered for marker in REMOTE_DIARIZATION_MARKERS):
        raise ValueError(
            "Modelo de diarizacao remoto/cloud bloqueado. Use apenas "
            f"{LOCAL_PYANNOTE_MODEL} ou um caminho local ja baixado."
        )
    if lowered != LOCAL_PYANNOTE_MODEL:
        raise ValueError(
            "Modelo de diarizacao nao permitido no modo standalone. Use apenas "
            f"{LOCAL_PYANNOTE_MODEL} ou um caminho local ja baixado."
        )
    return LOCAL_PYANNOTE_MODEL


def hf_cache_path(repo_id: str, cache_dir: Path | None = None) -> Path:
    root = cache_dir or runtime.model_cache_dir()
    return root / ("models--" + repo_id.replace("/", "--"))


def cached_snapshot_path(repo_id: str, cache_dir: Path | None = None) -> Path | None:
    repo_cache = hf_cache_path(repo_id, cache_dir)
    snapshots = repo_cache / "snapshots"
    refs_main = repo_cache / "refs" / "main"
    if refs_main.exists():
        revision = refs_main.read_text(encoding="utf-8").strip()
        candidate = snapshots / revision
        if candidate.exists():
            return candidate
    if not snapshots.exists():
        return None
    candidates = [path for path in snapshots.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def installed_asr_variants(cache_dir: Path | None = None) -> list[str]:
    """Return list of ASR variant keys that are cached locally."""
    result: list[str] = []
    for key, info in ASR_VARIANTS.items():
        path = cached_snapshot_path(info["repo"], cache_dir)
        if path and any(path.iterdir()):
            result.append(key)
    return result


def resolve_asr_model(configured: str, cache_dir: Path | None = None) -> str:
    """Resolve the effective ASR model to use.

    If the configured model is installed, use it.
    Otherwise, fall back to the first installed model.
    If nothing is installed, return the configured value (will fail later
    with a clear error from WhisperX).
    """
    # Check if configured model is a known variant with a specific repo
    if configured in ASR_VARIANTS:
        repo = ASR_VARIANTS[configured]["repo"]
        path = cached_snapshot_path(repo, cache_dir)
        if path and any(path.iterdir()):
            return configured
    else:
        # Unknown variant (e.g. custom model path) — pass through
        return configured

    # Configured model not installed — find an alternative
    installed = installed_asr_variants(cache_dir)
    if installed:
        return installed[0]
    return configured


_WEIGHT_BLOB_MIN_BYTES = 100 * 1024  # 100 KB floor: below this, only JSON/txt metadata is there


def _snapshot_has_weights(path: Path) -> bool:
    """True iff the snapshot dir has at least one file >= 100 KB.

    `any(path.iterdir())` returned True as soon as HF wrote `config.json`,
    even if the multi-GB `model.safetensors` never finished. That falsely
    flagged a partial cache as 'cached' and confused users when
    transcription failed later. 100 KB is below every real weight blob
    (smallest whisper layer is MB-scale) and above every config/tokenizer
    file (<20 KB), so it discriminates cleanly.
    """
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    if entry.stat().st_size >= _WEIGHT_BLOB_MIN_BYTES:
                        return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def has_partial_cache(cache_dir: Path | None = None, asr_variants: list[str] | None = None) -> bool:
    """True iff at least one required model has *any* file on disk but no full weight.

    Used by the GUI to distinguish 'never started' (show 'Preparar modelos agora?')
    from 'interrupted' (show 'Retomar download inconcluso?').
    """
    for asset in get_required_models(asr_variants):
        path = cached_snapshot_path(asset.repo_id, cache_dir)
        if path is None:
            continue
        try:
            if any(path.iterdir()):
                # Files present — check if they include actual weights
                if not _snapshot_has_weights(path):
                    return True
        except OSError:
            continue
    return False


def status(cache_dir: Path | None = None, asr_variants: list[str] | None = None) -> list[ModelStatus]:
    models = get_required_models(asr_variants)
    result: list[ModelStatus] = []
    for asset in models:
        path = cached_snapshot_path(asset.repo_id, cache_dir)
        cached = bool(path and _snapshot_has_weights(path))
        result.append(ModelStatus(asset=asset, cached=cached, path=path if cached else None))
    return result


def all_required_models_cached(cache_dir: Path | None = None, asr_variants: list[str] | None = None) -> bool:
    return all(item.cached for item in status(cache_dir, asr_variants=asr_variants))


def status_as_dict(cache_dir: Path | None = None) -> dict[str, Any]:
    cache = cache_dir or runtime.model_cache_dir()
    return {
        "cache_dir": str(cache),
        "models": [
            {
                "key": item.asset.key,
                "label": item.asset.label,
                "repo_id": item.asset.repo_id,
                "purpose": item.asset.purpose,
                "gated": item.asset.gated,
                "cached": item.cached,
                "path": str(item.path) if item.path else "",
                "message": item.message,
            }
            for item in status(cache)
        ],
    }


def status_text(cache_dir: Path | None = None) -> str:
    data = status_as_dict(cache_dir)
    lines = [f"Cache de modelos: {data['cache_dir']}"]
    for item in data["models"]:
        state = "baixado" if item["cached"] else "pendente"
        gated = " - requer aceite/token do Hugging Face" if item["gated"] else ""
        lines.append(f"- {item['label']}: {state} ({item['repo_id']}){gated}")
    return "\n".join(lines)


def _format_size(nbytes: int) -> str:
    if nbytes >= 1_073_741_824:
        return f"{nbytes / 1_073_741_824:.1f} GB"
    if nbytes >= 1_048_576:
        return f"{nbytes / 1_048_576:.0f} MB"
    return f"{nbytes / 1024:.0f} KB"


def _dir_size(path: Path) -> int:
    """Total bytes of all files under *path* (non-recursive would miss blobs)."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError as exc:
        _download_diag_log(f"[_dir_size] rglob error on {path}: {exc}")
    return total


def friendly_name(key: str) -> str:
    """Nome amigavel em pt-BR para exibicao ao usuario final.

    Aceita chaves de ASR_VARIANTS (ex.: 'tiny') ou ids de _FIXED_MODELS
    ('alignment_pt', 'diarization'). Para chaves desconhecidas retorna a
    propria chave como fallback."""
    info = ASR_VARIANTS.get(key)
    if info and info.get("friendly_pt"):
        return str(info["friendly_pt"])
    fixed = _FRIENDLY_FIXED_MODELS.get(key)
    if fixed:
        return fixed
    return key


def _known_repos() -> set[str]:
    """Conjunto de repo_ids conhecidos (ASR + fixos)."""
    known: set[str] = set()
    for info in ASR_VARIANTS.values():
        repo = info.get("repo")
        if repo:
            known.add(str(repo))
    for asset in _FIXED_MODELS:
        if asset.repo_id:
            known.add(str(asset.repo_id))
    return known


def orphan_repos(cache_dir: Path | None = None) -> list[str]:
    """Retorna repo_ids (no formato 'org/repo') de pastas models--* nao
    listadas em ASR_VARIANTS + _FIXED_MODELS."""
    root = cache_dir or runtime.model_cache_dir()
    if not root.exists():
        return []
    known = _known_repos()
    orphans: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("models--"):
            continue
        # models--org--repo -> org/repo (primeiro "--" apos "models-" e separador)
        parts = name[len("models--"):].split("--")
        if len(parts) < 2:
            continue
        repo_id = parts[0] + "/" + "--".join(parts[1:])
        if repo_id not in known:
            orphans.append(repo_id)
    return orphans


def model_install_date(repo_id: str, cache_dir: Path | None = None) -> float | None:
    """Retorna o ctime minimo (unix) dos blobs do snapshot ativo. None se nao
    houver snapshot. Usa blobs/ (arquivos reais) — mais preciso que mtime
    do snapshot/ que pode ter sido atualizado ao re-abrir."""
    snapshot = cached_snapshot_path(repo_id, cache_dir)
    if snapshot is None:
        return None
    repo_cache = hf_cache_path(repo_id, cache_dir)
    blobs_dir = repo_cache / "blobs"
    candidates: list[float] = []
    if blobs_dir.exists():
        for f in blobs_dir.iterdir():
            if f.is_file():
                try:
                    candidates.append(f.stat().st_ctime)
                except OSError:
                    pass
    if not candidates:
        # Fallback para mtime do snapshot
        try:
            return snapshot.stat().st_mtime
        except OSError:
            return None
    return min(candidates)


def scan_cache(cache_dir: Path | None = None) -> list[dict[str, Any]]:
    """Lista repos no cache com tamanho real em disco (dedup de blobs).

    Tenta usar huggingface_hub.scan_cache_dir() para dedup correta. Fallback
    para _dir_size (pode overcountar blobs compartilhados entre revisoes)."""
    root = cache_dir or runtime.model_cache_dir()
    entries: list[dict[str, Any]] = []
    try:
        from huggingface_hub import scan_cache_dir
        scan = scan_cache_dir(cache_dir=str(root))
        for repo in scan.repos:
            entries.append({
                "repo_id": repo.repo_id,
                "repo_type": repo.repo_type,
                "size_on_disk": int(repo.size_on_disk),
                "nb_files": int(repo.nb_files),
                "last_accessed": float(repo.last_accessed) if repo.last_accessed else 0.0,
                "last_modified": float(repo.last_modified) if repo.last_modified else 0.0,
            })
        return entries
    except Exception:
        pass
    # Fallback: iterar diretorio
    if not root.exists():
        return entries
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("models--"):
            continue
        parts = child.name[len("models--"):].split("--")
        if len(parts) < 2:
            continue
        repo_id = parts[0] + "/" + "--".join(parts[1:])
        size = _dir_size(child)
        entries.append({
            "repo_id": repo_id,
            "repo_type": "model",
            "size_on_disk": size,
            "nb_files": sum(1 for _ in child.rglob("*") if _.is_file()),
            "last_accessed": 0.0,
            "last_modified": 0.0,
        })
    return entries


def delete_model(repo_id: str, cache_dir: Path | None = None, max_retries: int = 3) -> dict[str, Any]:
    """Remove um modelo do cache. Usa scan_cache_dir().delete_revisions().execute()
    para dedup correta dos blobs. Retry com backoff para lock (Windows mmap).

    Retorna: {success: bool, bytes_freed: int, error: str | None}.
    """
    import time as _time
    try:
        from huggingface_hub import scan_cache_dir
    except Exception as exc:
        return {"success": False, "bytes_freed": 0, "error": f"huggingface_hub ausente: {exc}"}
    root = cache_dir or runtime.model_cache_dir()
    last_err: str | None = None
    for attempt, delay in enumerate([0.0, 0.2, 0.5, 1.5]):
        if attempt >= max_retries + 1:
            break
        if delay:
            _time.sleep(delay)
        try:
            scan = scan_cache_dir(cache_dir=str(root))
            target_repo = next((r for r in scan.repos if r.repo_id == repo_id), None)
            if target_repo is None:
                return {"success": True, "bytes_freed": 0, "error": "modelo ja nao estava em cache"}
            revisions = [rev.commit_hash for rev in target_repo.revisions]
            if not revisions:
                return {"success": True, "bytes_freed": 0, "error": "sem revisoes a remover"}
            strategy = scan.delete_revisions(*revisions)
            bytes_freed = int(strategy.expected_freed_size)
            strategy.execute()
            return {"success": True, "bytes_freed": bytes_freed, "error": None}
        except (PermissionError, OSError) as exc:
            last_err = str(exc)
            continue
        except Exception as exc:  # pragma: no cover (api surface do hub)
            return {"success": False, "bytes_freed": 0, "error": str(exc)}
    return {"success": False, "bytes_freed": 0, "error": f"falhou apos {max_retries} tentativas: {last_err}"}


def _poll_download_progress(
    cache_dir: Path,
    estimated_bytes: int,
    start_pct: int,
    end_pct: int,
    label: str,
    progress_callback: ProgressCallback,
    stop_event: "threading.Event",
    interval: float = 1.0,
) -> None:
    """Background thread that polls *cache_dir* size and reports progress.

    ``huggingface_hub.snapshot_download`` does **not** forward its
    ``tqdm_class`` to per-file downloads (confirmed in v0.36).  Polling
    file sizes on disk is the only reliable way to get granular progress
    that works across all versions.
    """
    peak_pct = 0
    baseline = _dir_size(cache_dir)
    _download_diag_log(
        f"[poll:{label}] start cache_dir={cache_dir} baseline={baseline}B "
        f"estimated={estimated_bytes}B range=[{start_pct},{end_pct}]"
    )
    iteration = 0
    last_logged_current = -1
    while not stop_event.is_set():
        iteration += 1
        current = _dir_size(cache_dir) - baseline
        if estimated_bytes > 0:
            model_pct = min(100, int((current / estimated_bytes) * 100))
        else:
            model_pct = 0
        overall = start_pct + int((end_pct - start_pct) * model_pct / 100)
        overall = max(overall, peak_pct)
        peak_pct = overall
        # Log first 3 iterations always, then only when current changes
        # by >= 10 MB or at 10s ticks.
        if iteration <= 3 or (
            abs(current - last_logged_current) >= 10 * 1024 * 1024
        ) or iteration % 10 == 0:
            try:
                subdirs = sorted(
                    p.name for p in cache_dir.iterdir() if p.is_dir()
                )[:8]
            except OSError as exc:
                subdirs = [f"<iterdir error: {exc}>"]
            _download_diag_log(
                f"[poll:{label}] iter={iteration} current={current}B "
                f"model_pct={model_pct} overall={overall} subdirs={subdirs}"
            )
            last_logged_current = current
        size_str = _format_size(current)
        total_str = _format_size(estimated_bytes)
        progress_callback(
            {
                "event": "model_download_bytes",
                "progress": overall,
                "message": f"Baixando {label}: {size_str}/{total_str}",
            }
        )
        stop_event.wait(interval)
    _download_diag_log(
        f"[poll:{label}] stopped after iter={iteration} peak_pct={peak_pct}"
    )


def download_required_models(
    *,
    token: str | None = None,
    token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN",
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    asr_variants: list[str] | None = None,
) -> int:
    cache_dir = runtime.model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    runtime.apply_secure_hf_environment(offline=False, token=token, token_env=token_env)
    # Bound HF hub network timeouts so a hung request fails fast instead of
    # deadlocking the whole wizard. 30s per-request is generous for a
    # healthy connection; a chunk that takes longer indicates a real stall.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
    # Remove stale filelock sentinels from previous crashed downloads so
    # snapshot_download doesn't deadlock. See _clear_stale_hf_locks.
    _clear_stale_hf_locks(cache_dir)
    token_value = token if token is not None else os.environ.get(token_env)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(f"Dependencia ausente: huggingface_hub ({exc})") from exc

    models = get_required_models(asr_variants)
    failures = 0
    total = max(1, len(models))
    # Compute per-model progress ranges weighted by estimated size
    total_gb = sum(a.estimated_gb for a in models) or 1.0
    cumulative_gb = 0.0
    model_ranges: list[tuple[int, int]] = []
    for asset in models:
        start_pct = int(cumulative_gb / total_gb * 100)
        cumulative_gb += asset.estimated_gb
        end_pct = int(cumulative_gb / total_gb * 100)
        model_ranges.append((start_pct, end_pct))
    for index, asset in enumerate(models, start=1):
        if should_cancel is not None and should_cancel():
            failures += 1
            break
        start_pct, end_pct = model_ranges[index - 1]
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_download_start",
                    "progress": start_pct,
                    "message": f"Baixando {asset.label} ({index}/{total})...",
                }
            )
        estimated_bytes = int(asset.estimated_gb * 1_073_741_824)
        # Start a background poller to track download progress on disk
        stop_event: threading.Event | None = None
        poller: threading.Thread | None = None
        if progress_callback is not None:
            stop_event = threading.Event()
            poller = threading.Thread(
                target=_poll_download_progress,
                args=(cache_dir, estimated_bytes, start_pct, end_pct,
                      asset.label, progress_callback, stop_event),
                daemon=True,
            )
            poller.start()
        try:
            kwargs: dict[str, object] = dict(
                repo_id=asset.repo_id,
                repo_type="model",
                cache_dir=str(cache_dir),
                token=token_value,
                force_download=force,
                local_files_only=False,
            )
            snapshot_download(**kwargs)
        except Exception as exc:  # noqa: BLE001 - keep batch progress visible.
            failures += 1
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "model_download_error",
                        "progress": end_pct,
                        "message": f"Falha ao baixar {asset.label}: {sanitize_message(str(exc))}",
                    }
                )
            continue
        finally:
            if stop_event is not None:
                stop_event.set()
            if poller is not None:
                poller.join(timeout=2)
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_download_done",
                    "progress": end_pct,
                    "message": f"{asset.label} baixado ({index}/{total}).",
                }
            )
    # Clean up token from environment after download session
    os.environ.pop(token_env, None)
    os.environ.pop("HF" + "_TOKEN", None)
    return failures


def verify_required_models(progress_callback: ProgressCallback | None = None) -> int:
    cache_dir = runtime.model_cache_dir()
    runtime.apply_secure_hf_environment(offline=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(f"Dependencia ausente: huggingface_hub ({exc})") from exc

    failures = 0
    total = max(1, len(REQUIRED_MODELS))
    for index, asset in enumerate(REQUIRED_MODELS, start=1):
        try:
            snapshot_download(
                repo_id=asset.repo_id,
                repo_type="model",
                cache_dir=str(cache_dir),
                local_files_only=True,
                token=None,
            )
            ok = True
            message = f"{asset.label} pronto para uso local."
        except Exception as exc:  # noqa: BLE001 - report missing/offline failures as actionable status.
            failures += 1
            ok = False
            message = f"{asset.label} ausente ou incompleto: {exc}"
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_verify",
                    "progress": int((index / total) * 100),
                    "message": message,
                    "ok": ok,
                }
            )
    return failures


def local_pyannote_checkpoint() -> str:
    validate_local_diarization_model(LOCAL_PYANNOTE_MODEL)
    cache_dir = runtime.model_cache_dir()
    runtime.apply_secure_hf_environment(offline=True)
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(
            repo_id=LOCAL_PYANNOTE_MODEL,
            repo_type="model",
            cache_dir=str(cache_dir),
            local_files_only=True,
            token=None,
        )
    except Exception as exc:  # noqa: BLE001 - fallback preserves a clearer domain error.
        fallback = cached_snapshot_path(LOCAL_PYANNOTE_MODEL, cache_dir)
        if fallback is not None:
            return str(fallback)
        raise RuntimeError(
            "Modelo de diarizacao local nao encontrado. Rode `transcribe_pipeline models download` "
            "com o token Hugging Face do usuario antes de diarizar."
        ) from exc


def write_status_json(path: Path) -> None:
    path.write_text(json.dumps(status_as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
