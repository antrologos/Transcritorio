from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import json
import os

from . import runtime
from .utils import sanitize_message


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


@dataclass(frozen=True)
class ModelStatus:
    asset: ModelAsset
    cached: bool
    path: Path | None
    message: str = ""


REQUIRED_MODELS: tuple[ModelAsset, ...] = (
    ModelAsset("asr", "Whisper large-v3", "Systran/faster-whisper-large-v3", "transcricao"),
    ModelAsset("alignment_pt", "Alinhamento portugues", "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese", "timestamps por palavra"),
    ModelAsset("diarization", "Separacao de falantes", LOCAL_PYANNOTE_MODEL, "diarizacao local", gated=True),
)


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


def status(cache_dir: Path | None = None) -> list[ModelStatus]:
    result: list[ModelStatus] = []
    for asset in REQUIRED_MODELS:
        path = cached_snapshot_path(asset.repo_id, cache_dir)
        cached = bool(path and any(path.iterdir()))
        result.append(ModelStatus(asset=asset, cached=cached, path=path if cached else None))
    return result


def all_required_models_cached(cache_dir: Path | None = None) -> bool:
    return all(item.cached for item in status(cache_dir))


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


def _make_tqdm_class(
    asset_label: str,
    model_index: int,
    total_models: int,
    progress_callback: ProgressCallback | None,
) -> type | None:
    """Create a tqdm-compatible class that forwards byte progress to the GUI."""
    if progress_callback is None:
        return None

    try:
        from tqdm import tqdm as _tqdm_base
    except ImportError:
        return None

    class _DownloadProgress(_tqdm_base):  # type: ignore[misc]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs.setdefault("unit", "B")
            kwargs.setdefault("unit_scale", True)
            super().__init__(*args, **kwargs)

        def update(self, n: int = 1) -> bool | None:  # type: ignore[override]
            result = super().update(n)
            total_bytes = self.total or 1
            done_bytes = self.n
            file_pct = min(100, int((done_bytes / total_bytes) * 100))
            model_start = int(((model_index - 1) / total_models) * 100)
            model_end = int((model_index / total_models) * 100)
            overall = model_start + int((model_end - model_start) * file_pct / 100)
            size_info = f"{_format_size(done_bytes)}/{_format_size(total_bytes)}"
            progress_callback(
                {
                    "event": "model_download_bytes",
                    "progress": overall,
                    "message": f"Baixando {asset_label}: {size_info} ({file_pct}%)",
                }
            )
            return result

    return _DownloadProgress


def download_required_models(
    *,
    token: str | None = None,
    token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN",
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
) -> int:
    cache_dir = runtime.model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    runtime.apply_secure_hf_environment(offline=False, token=token, token_env=token_env)
    token_value = token if token is not None else os.environ.get(token_env)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(f"Dependencia ausente: huggingface_hub ({exc})") from exc

    failures = 0
    total = max(1, len(REQUIRED_MODELS))
    for index, asset in enumerate(REQUIRED_MODELS, start=1):
        if should_cancel is not None and should_cancel():
            failures += 1
            break
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_download_start",
                    "progress": int(((index - 1) / total) * 100),
                    "message": f"Baixando {asset.label} ({index}/{total})...",
                }
            )
        tqdm_cls = _make_tqdm_class(asset.label, index, total, progress_callback)
        try:
            kwargs: dict[str, object] = dict(
                repo_id=asset.repo_id,
                repo_type="model",
                cache_dir=str(cache_dir),
                token=token_value if asset.gated else None,
                force_download=force,
                local_files_only=False,
            )
            if tqdm_cls is not None:
                kwargs["tqdm_class"] = tqdm_cls
            snapshot_download(**kwargs)
        except Exception as exc:  # noqa: BLE001 - keep batch progress visible.
            failures += 1
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "model_download_error",
                        "progress": int((index / total) * 100),
                        "message": f"Falha ao baixar {asset.label}: {sanitize_message(str(exc))}",
                    }
                )
            continue
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_download_done",
                    "progress": int((index / total) * 100),
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
