from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Callable
import json
import os
import re
import threading
import time

import shutil

from . import runtime
from .utils import sanitize_message

HF_ENDPOINT = "https://huggingface.co"
_DOWNLOAD_CHUNK = 1024 * 1024  # 1 MiB streaming chunk

# Retry per-blob no `_manual_snapshot_download`. Conexao flaky (caso da
# Denise em 2026-04-25) deixava UI exibir erro fatal e exigir clique manual
# em "Voltar"+"Proximo" ate completar. Aqui retentamos automatico ate 5
# vezes, total de ~30s extras no pior caso. Cache hit no blob (etag) garante
# que retentativas nao re-baixam arquivos ja completos.
_DOWNLOAD_MAX_ATTEMPTS = 5
_DOWNLOAD_BACKOFF = (0, 2, 4, 8, 16)  # segundos antes de cada tentativa (index = attempt)


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

MINIMUM_DISK_GB = 4  # Piso do fluxo essencial quando nao se sabe o que vai baixar


def check_disk_space(required_gb: float | None = None) -> dict[str, Any]:
    """Espaco livre suficiente para o que sera baixado.

    required_gb vem de quem sabe o que vai baixar (o assistente soma os
    modelos escolhidos) — o limiar fixo tanto barrava instalacoes que
    caberiam quanto aprovava instalacoes que nao cabiam.
    """
    needed = float(required_gb) if required_gb else float(MINIMUM_DISK_GB)
    needed = round(needed * 1.15 + 0.5, 1)  # margem de cache/temporarios
    cache_dir = runtime.model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(str(cache_dir))
    free_gb = usage.free / (1024 ** 3)
    if free_gb >= needed:
        return {"ok": True, "free_gb": round(free_gb, 1),
                "message": f"Espaço disponível: {free_gb:.1f} GB."}
    return {"ok": False, "free_gb": round(free_gb, 1),
            "message": (f"Espaço insuficiente no disco. "
                        f"Disponível: {free_gb:.1f} GB. Necessário: pelo menos {needed:.1f} GB.\n"
                        f"Libere espaço e tente novamente.")}


ProgressCallback = Callable[[dict[str, Any]], None]
ShouldCancel = Callable[[], bool]

LOCAL_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"
# Pinned SHA da revisao conhecida-boa do pyannote (auditada 2026-04-22).
# Compartilhado por _FIXED_MODELS e pela logica de fallback offline.
LOCAL_PYANNOTE_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"


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
    # HF revision pinada. None => segue 'main' (comportamento antigo).
    # Pinar uma SHA especifica (a) evita redirect chains (bug do turbo em
    # 2026-04-22) e (b) fornece reprodutibilidade + defesa supply-chain:
    # se o repo publicar v2 maliciosa, o app continua baixando so o hash
    # conhecido-bom. Upgrade deliberado via commit que altera o SHA.
    revision: str | None = None
    # Repo acompanhante do qual o modelo carrega tokenizer/config em
    # tempo de execucao (o GLiNER usa o encoder base). Baixado junto,
    # SEM os pesos — o modelo tem os proprios. Sem isso, o worker
    # offline quebra com "couldn't connect to huggingface.co".
    companion_repo: str | None = None
    companion_revision: str | None = None
    # Arquivos do repo que NAO precisamos (padroes fnmatch sobre o nome
    # dentro do repo). Alguns repos publicam os pesos em varios formatos
    # (PyTorch + Flax + TF) e extras que o nosso carregador nunca le;
    # baixar tudo custa GBs por instalacao sem beneficio nenhum.
    download_exclude: tuple[str, ...] = ()


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
        # O repo original mobiuslabsgmbh/faster-whisper-large-v3-turbo foi
        # transferido pro org dropbox-dash em 2025-2026. HF API retorna 307
        # pra ca; apontar direto elimina a cadeia de redirect que estava
        # travando snapshot_download no bundle frozen (detectado via
        # download_diagnostic.log do Rogerio em 2026-04-22).
        "repo": "dropbox-dash/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "estimated_gb": 3.1,
        "quality": 8,
        "speed": 8,
        "desc": "Recomendado. Melhor equilibrio entre qualidade e velocidade.",
    },
    "large-v3": {
        "label": "Whisper large-v3",
        "friendly_pt": "Maxima precisao (melhor qualidade, 5,8 GB)",
        "repo": "Systran/faster-whisper-large-v3",
        "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "estimated_gb": 5.8,
        "quality": 10,
        "speed": 4,
        "desc": "Melhor qualidade, mais lento.",
    },
    "medium": {
        "label": "Whisper medium",
        "friendly_pt": "Preciso (alta qualidade, 2,8 GB)",
        "repo": "Systran/faster-whisper-medium",
        "revision": "08e178d48790749d25932bbc082711ddcfdfbc4f",
        "estimated_gb": 2.8,
        "quality": 7,
        "speed": 7,
        "desc": "Boa qualidade, mais rapido.",
    },
    "small": {
        "label": "Whisper small",
        "friendly_pt": "Equilibrado (qualidade boa, 900 MB)",
        "repo": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "estimated_gb": 0.9,
        "quality": 5,
        "speed": 8,
        "desc": "Qualidade razoavel.",
    },
    "base": {
        # demo_only (2026-08-30): qualidade insuficiente para trabalho
        # real — fora do assistente; baixavel so pelo gerenciador, com o
        # aviso no proprio rotulo (o antigo dizia "qualidade boa" e o
        # desc interno "Qualidade fraca" — mentia).
        "label": "Whisper base (demonstração)",
        "friendly_pt": "⚠ Demonstração leve (300 MB) — qualidade insuficiente para trabalho",
        "repo": "Systran/faster-whisper-base",
        "revision": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        "estimated_gb": 0.3,
        "quality": 3,
        "speed": 9,
        "demo_only": True,
        "desc": "Qualidade fraca — apenas demonstração.",
    },
    "tiny": {
        # demo_only (2026-08-30): ~30% de erro em pt, loops de repeticao
        # — serve SO para testar o fluxo em 1 minuto de download.
        "label": "Whisper tiny (demonstração)",
        "friendly_pt": "⚠ Demonstração (150 MB) — ~30% de erros; só para testar o fluxo",
        "repo": "Systran/faster-whisper-tiny",
        "revision": "d90ca5fe260221311c53c58e660288d3deb8d356",
        "estimated_gb": 0.15,
        "quality": 2,
        "speed": 10,
        "demo_only": True,
        "desc": "Qualidade ruim — apenas demonstração.",
    },
}

_FRIENDLY_FIXED_MODELS: dict[str, str] = {
    # 1,4 GB desde o download_exclude de 2026-08-28 (o rotulo antigo
    # "6,9 GB" ficou para tras e mentia no gerenciador).
    "alignment_pt": "Alinhamento de tempo (portugues, 1,4 GB)",
    "diarization": "Identificacao de falantes (70 MB)",
}

DEFAULT_ASR_VARIANT = "large-v3-turbo"

_FIXED_MODELS: tuple[ModelAsset, ...] = (
    ModelAsset(
        "alignment_pt",
        "Alinhamento portugues",
        "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese",
        "timestamps por palavra",
        estimated_gb=1.4,
        revision="634ac655299bcdc46c83bc01da9bab52d2987e4f",
        # O WhisperX carrega este repo com Wav2Vec2Processor + Wav2Vec2ForCTC,
        # que leem SO o pytorch_model.bin e os jsons de config/vocab. O repo
        # ainda publica os mesmos pesos em Flax e um modelo de linguagem para
        # decodificacao com LM (Wav2Vec2ProcessorWithLM), que nunca usamos:
        # 2,4 GB por instalacao, medidos em 2026-08-28.
        download_exclude=(
            "flax_model.msgpack",
            "tf_model.h5",
            "language_model/*",
            "*eval_results*.txt",
            "log_*.txt",
            "eval.py",
            "full_eval.sh",
        ),
    ),
    ModelAsset(
        "diarization",
        "Separacao de falantes",
        LOCAL_PYANNOTE_MODEL,
        "diarizacao local",
        gated=True,
        estimated_gb=0.07,
        revision=LOCAL_PYANNOTE_REVISION,
    ),
)


# Modelos OPCIONAIS da analise local (plano-programa v0.2.0, fase 2):
# conhecidos pelo cache (nunca listados como orfaos pela UI de limpeza),
# mas FORA de get_required_models — baixados sob demanda quando o usuario
# ativa as ferramentas de analise. SHAs validadas no PoC de 2026-08-25
# (Qwen3.5-4B vencedor do julgamento as cegas; GLiNER vencedor da regua
# de nomes com recall 0,96).
_OPTIONAL_MODELS: tuple[ModelAsset, ...] = (
    ModelAsset(
        "llm_qwen",
        "Analise local (Qwen3.5-4B)",
        "Qwen/Qwen3.5-4B",
        "sumarios, codigos tematicos e verificacoes por conteudo",
        estimated_gb=8.7,
        revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
    ),
    ModelAsset(
        "ner_gliner",
        "Extracao de entidades (GLiNER)",
        "urchade/gliner_multi_pii-v1",
        "nomes, lugares e instituicoes citados",
        estimated_gb=1.1,
        revision="1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d",
        # O GLiNER traz os proprios pesos, mas carrega tokenizer e config
        # do encoder base (~4 MB de arquivos pequenos).
        companion_repo="microsoft/mdeberta-v3-base",
        companion_revision="a0484667b22365f84929a935b5e50a51f71f159d",
    ),
    ModelAsset(
        "search_encoder",
        "Busca por sentido (encoder multilingue)",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "encontrar trechos por significado nas transcricoes",
        estimated_gb=0.5,
        revision="86741b4e3f5cb7765a600d3a3d55a0f6a6cb443d",
    ),
)


def optional_model(key: str) -> ModelAsset:
    for asset in _OPTIONAL_MODELS:
        if asset.key == key:
            return asset
    raise KeyError(f"Modelo opcional desconhecido: {key}")


def asset_by_key(key: str) -> ModelAsset:
    """Asset por chave, FIXO ou opcional (F4).

    Permite ao gerenciador baixar por item tambem os fixos ungated
    (ex.: o alinhador pendente numa instalacao essencial — antes nao
    havia rota nenhuma para instala-lo)."""
    for asset in _OPTIONAL_MODELS + _FIXED_MODELS:
        if asset.key == key:
            return asset
    raise KeyError(f"Modelo desconhecido: {key}")


def download_optional_model(
    key: str,
    progress_callback: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
) -> int:
    """Baixa um modelo UNGATED (opcional ou fixo) sob demanda; 0 = sucesso.

    Reutiliza o downloader manual (Xet-proof, SHA-pinada). Checagem de
    disco POR modelo — a global de 10 GB nao cobre downloads grandes.
    Modelos gated (pyannote) nao passam por aqui: exigem token, e o
    caminho deles e o ModelSetupDialog.
    """
    asset = asset_by_key(key)
    if asset.gated:
        print(f"{asset.label} exige conta/token: use o preparador de modelos.")
        return 1
    cache_dir = runtime.model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if optional_model_cached(asset, cache_dir):
        return 0
    needed_gb = asset.estimated_gb * 1.2 + 1.0
    free_gb = _free_disk_gb(cache_dir)
    if free_gb is not None and free_gb < needed_gb:
        print(f"Espaco em disco insuficiente para {asset.label}: "
              f"{free_gb:.1f} GB livres, ~{needed_gb:.1f} GB necessarios.")
        return 1
    runtime.apply_secure_hf_environment(offline=False, token=None)
    main_end = 95 if asset.companion_repo else 100
    try:
        if cached_snapshot_path(asset.repo_id, cache_dir, revision=asset.revision) is None:
            _manual_snapshot_download(
                repo_id=asset.repo_id,
                revision=asset.revision or "main",
                cache_dir=cache_dir,
                token=None,
                label=asset.label,
                start_pct=0,
                end_pct=main_end,
                estimated_bytes=int(asset.estimated_gb * (1024 ** 3)),
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                exclude=asset.download_exclude,
            )
        if asset.companion_repo:
            _manual_snapshot_download(
                repo_id=asset.companion_repo,
                revision=asset.companion_revision or "main",
                cache_dir=cache_dir,
                token=None,
                label=f"{asset.label} (tokenizador)",
                start_pct=main_end,
                end_pct=100,
                estimated_bytes=8 * 1024 * 1024,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                skip_weights=True,
            )
    except Exception as exc:  # noqa: BLE001 - download e opcional, nunca crash
        print(f"Falha ao baixar {asset.label}: {exc}")
        return 1
    return 0


def optional_model_cached(asset: ModelAsset, cache_dir: Path | None = None) -> bool:
    """Modelo pronto para uso OFFLINE: pesos e, quando houver, o repo
    acompanhante de onde ele carrega o tokenizador."""
    cache = cache_dir or runtime.model_cache_dir()
    if cached_snapshot_path(asset.repo_id, cache, revision=asset.revision) is None:
        return False
    if asset.companion_repo and cached_snapshot_path(
            asset.companion_repo, cache, revision=asset.companion_revision) is None:
        return False
    return True


def _free_disk_gb(path: Path) -> float | None:
    try:
        import shutil as _shutil

        return _shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return None


def get_required_models(
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> tuple[ModelAsset, ...]:
    """Build the list of required models based on selected ASR variants.

    O modelo de diarizacao (pyannote, gated — exige conta HF + token) so
    entra quando ``include_diarization=True``: quem so quer transcrever nao
    pode ser obrigado a criar conta/token (v0.2, diarizacao opcional).
    ``include_alignment=False`` (perfil essencial): sem tempos por palavra,
    a transcricao roda com ``--no_align`` — e 1,4 GB a menos de download.
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
            revision=info.get("revision"),
        ))
    skip = set()
    if not include_diarization:
        skip.add("diarization")
    if not include_alignment:
        skip.add("alignment_pt")
    assets.extend(a for a in _FIXED_MODELS if a.key not in skip)
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


def cached_snapshot_path(
    repo_id: str,
    cache_dir: Path | None = None,
    revision: str | None = None,
) -> Path | None:
    """Return the snapshot dir for a cached model, or None if absent.

    If *revision* is provided (pinned SHA), resolve that exact snapshot
    first — the HF hub writes pinned downloads straight to
    ``snapshots/<sha>/`` without needing a ``refs/main`` pointer. Falls
    back to ``refs/main`` and most-recently-modified lookup for repos
    that don't have pinned revisions configured.
    """
    repo_cache = hf_cache_path(repo_id, cache_dir)
    snapshots = repo_cache / "snapshots"
    if revision:
        candidate = snapshots / revision
        if candidate.exists():
            return candidate
        # Pinned revision not yet downloaded — don't silently fall through
        # to the "main" branch of the cache; caller expects the exact SHA.
        if not snapshots.exists():
            return None
    refs_main = repo_cache / "refs" / "main"
    if refs_main.exists():
        ref_rev = refs_main.read_text(encoding="utf-8").strip()
        # refs/main vazio: sem o guard, snapshots/"" == snapshots/ e o proprio
        # diretorio-pai seria retornado como "snapshot" (estado parcial eterno).
        if ref_rev:
            candidate = snapshots / ref_rev
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
        path = cached_snapshot_path(info["repo"], cache_dir, revision=info.get("revision"))
        # _snapshot_has_weights, nao any(iterdir()): cache parcial (so config/
        # tokenizer) nao pode contar como variante instalada.
        if path and _snapshot_has_weights(path):
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
        info = ASR_VARIANTS[configured]
        path = cached_snapshot_path(info["repo"], cache_dir, revision=info.get("revision"))
        if path and _snapshot_has_weights(path):
            return configured
    else:
        # Unknown variant (e.g. custom model path) — pass through
        return configured

    # Configured model not installed — find an alternative
    installed = installed_asr_variants(cache_dir)
    if installed:
        return installed[0]
    return configured


def resolve_asr_repo(configured: str) -> str:
    """Return the full HF repo_id for an ASR variant.

    Used by whisperx_runner to pass the correct repo to faster_whisper.
    Necessary because faster_whisper hardcodes its own shortcut table
    (e.g. faster_whisper.utils._MODELS maps "large-v3-turbo" to
    "mobiuslabsgmbh/faster-whisper-large-v3-turbo"), but Transcritorio
    downloads turbo from "dropbox-dash/faster-whisper-large-v3-turbo"
    (different SHA, no redirect chain). Passing the shortcut would cause
    faster_whisper to look in the wrong cache directory and raise
    LocalEntryNotFoundError under --model_cache_only True.

    Returns the variant's repo_id when configured is a known variant
    name; otherwise returns configured unchanged (already a repo_id or
    a path to a local model).
    """
    if configured in ASR_VARIANTS:
        return ASR_VARIANTS[configured]["repo"]
    return configured


# Piso para considerar um blob como PESO de modelo. Empiricamente (cache real,
# 2026-08-23): o maior arquivo nao-peso e o tokenizer.json do whisper (~2.4 MB);
# o menor peso real e o do pyannote community-1 (~25 MB). 4 MB separa os dois
# com folga. O piso antigo de 100 KB aceitava tokenizer.json e blobs parciais
# como "peso", marcando modelo quebrado como pronto (desabilitava a retomada).
_WEIGHT_BLOB_MIN_BYTES = 4 * 1024 * 1024


def _snapshot_has_weights(path: Path) -> bool:
    """True iff the repo has at least one blob >= 100 KB on disk.

    Checa diretamente `{repo}/blobs/`, que sao arquivos regulares (nunca
    symlinks), em vez de `{repo}/snapshots/{sha}/` que usa symlinks. No
    Windows bundled com PyInstaller, `Path.rglob` aparentemente nao
    enumera/segue symlinks Windows nativos corretamente: com os
    multi-GB blobs na pasta blobs/, o rglob sobre snapshots/{sha}/ nao
    retornava nenhum arquivo, fazendo has_weights virar False e UI
    mostrar 'Modelos ausentes ou incompletos'.

    Blobs/ nunca tem symlinks — HF hub escreve arquivos raw la, com
    nome = hash do conteudo. Enumerar direto resolve o problema.

    `path` eh o snapshot dir (`.../models--org--repo/snapshots/<sha>/`);
    os blobs vivem no diretorio irmao `.../models--org--repo/blobs/`.
    """
    if path is None:
        return False
    blobs_dir = path.parent.parent / "blobs"
    if not blobs_dir.is_dir():
        return False
    try:
        for entry in blobs_dir.iterdir():
            try:
                # *.incomplete = download parcial do hub; nunca contar como peso.
                if entry.name.endswith(".incomplete"):
                    continue
                if entry.is_file() and entry.stat().st_size >= _WEIGHT_BLOB_MIN_BYTES:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def has_partial_cache(
    cache_dir: Path | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> bool:
    """True iff at least one required model has *any* file on disk but no full weight.

    Used by the GUI to distinguish 'never started' (show 'Preparar modelos agora?')
    from 'interrupted' (show 'Retomar download inconcluso?').
    """
    for asset in get_required_models(asr_variants, include_diarization=include_diarization,
                                     include_alignment=include_alignment):
        path = cached_snapshot_path(asset.repo_id, cache_dir, revision=asset.revision)
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


def status(
    cache_dir: Path | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> list[ModelStatus]:
    models = get_required_models(asr_variants, include_diarization=include_diarization,
                                 include_alignment=include_alignment)
    result: list[ModelStatus] = []
    for asset in models:
        path = cached_snapshot_path(asset.repo_id, cache_dir, revision=asset.revision)
        cached = bool(path and _snapshot_has_weights(path))
        result.append(ModelStatus(asset=asset, cached=cached, path=path if cached else None))
    return result


def all_required_models_cached(
    cache_dir: Path | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> bool:
    return all(item.cached for item in status(
        cache_dir, asr_variants=asr_variants, include_diarization=include_diarization,
        include_alignment=include_alignment))


def status_as_dict(
    cache_dir: Path | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> dict[str, Any]:
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
            for item in status(cache, asr_variants=asr_variants,
                               include_diarization=include_diarization,
                               include_alignment=include_alignment)
        ],
    }


def status_text(
    cache_dir: Path | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> str:
    data = status_as_dict(cache_dir, asr_variants=asr_variants,
                          include_diarization=include_diarization,
                          include_alignment=include_alignment)
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
    """Conjunto de repo_ids conhecidos (ASR + fixos + opcionais)."""
    known: set[str] = set()
    for info in ASR_VARIANTS.values():
        repo = info.get("repo")
        if repo:
            known.add(str(repo))
    for asset in _FIXED_MODELS + _OPTIONAL_MODELS:
        if asset.repo_id:
            known.add(str(asset.repo_id))
        # O repo do tokenizador tambem e legitimo: sem isto a limpeza de
        # orfaos o apagaria e o modelo pararia de carregar offline.
        if asset.companion_repo:
            known.add(str(asset.companion_repo))
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


def _etag_from_headers(headers) -> str | None:
    """Extract the blob identifier HF hub uses as on-disk filename.

    For LFS/Xet-backed files, X-Linked-ETag is the SHA256 of the blob
    content (64 hex). For small inline files, plain ETag is the git
    blob SHA1 (40 hex). Both are stripped of W/ weak marker and quotes.
    """
    for key in ("X-Linked-ETag", "ETag"):
        raw = headers.get(key)
        if not raw:
            continue
        value = raw.strip()
        if value.startswith("W/"):
            value = value[2:]
        return value.strip('"')
    return None


def _place_blob_in_snapshot(blob_path: Path, snapshot_path: Path) -> None:
    """Mirror blob into snapshot dir.

    Usa `diagnostics.symlinks_supported()` (cached probe at startup) pra
    decidir symlink vs copy. Evita tentativa + fallback a cada arquivo
    no Windows sem Dev Mode (antes gerava 1 OSError por arquivo baixado).
    Layout resultante carregado identicamente via from_pretrained.
    """
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot_path.exists() or snapshot_path.is_symlink():
        try:
            snapshot_path.unlink()
        except OSError:
            pass
    from . import diagnostics
    if diagnostics.symlinks_supported():
        try:
            rel = os.path.relpath(blob_path, snapshot_path.parent)
            snapshot_path.symlink_to(rel)
            return
        except (OSError, NotImplementedError):
            pass
    shutil.copy2(blob_path, snapshot_path)


def _manual_snapshot_download(
    *,
    repo_id: str,
    revision: str,
    cache_dir: Path,
    token: str | None,
    label: str,
    start_pct: int,
    end_pct: int,
    estimated_bytes: int,
    progress_callback: ProgressCallback | None,
    should_cancel: ShouldCancel | None,
    skip_weights: bool = False,
    exclude: tuple[str, ...] = (),
) -> Path:
    """Baixar um repo HF usando requests direto, bypassando huggingface_hub.

    skip_weights=True baixa so os arquivos pequenos (config, tokenizer):
    usado nos repos acompanhantes, de onde queremos apenas o tokenizador
    — baixar os pesos deles seria GB jogado fora.

    Motivo da existencia: huggingface_hub 0.36.2 (versao que cabia nas outras
    deps do projeto em abr/2026) nao suporta Xet Storage, o novo backend que
    a HF migrou grandes blobs em 2025. Ao receber o 302 redirect pro
    cas-bridge.xethub.hf.co, a lib velha trava indefinidamente. Confirmado
    empiricamente em maquina Windows do Rogerio via download_diagnostic.log.

    A URL redirect da HF pro CAS ja vem com presigned signature (query
    param X-Amz-Signature), entao qualquer cliente HTTP que siga redirect
    baixa o blob direto — nao precisa de cliente Xet-aware. Usamos o
    proprio requests da stdlib-transitive, que ja e dep do hub.

    Layout do cache gerado e identico ao HF hub (blobs/ + snapshots/ + refs/),
    para que faster_whisper / transformers consigam carregar sem saber da
    diferenca.
    """
    import requests  # transitive dep de huggingface_hub, sempre presente

    session = requests.Session()
    session.headers.update({"User-Agent": "transcritorio/hf-manual-downloader"})
    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    def _emit_bytes(done: int) -> None:
        if progress_callback is None:
            return
        if estimated_bytes > 0:
            model_pct = min(100, int(done * 100 / estimated_bytes))
        else:
            model_pct = 0
        overall = start_pct + int((end_pct - start_pct) * model_pct / 100)
        progress_callback({
            "event": "model_download_bytes",
            "progress": overall,
            "message": f"Baixando {label}: {_format_size(done)}/{_format_size(estimated_bytes)}",
        })

    def _emit_event(event: str, message: str, pct: int) -> None:
        if progress_callback is None:
            return
        progress_callback({"event": event, "progress": pct, "message": message})

    def _cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    # 1. Metadata: lista de arquivos desta revision. Se o repo foi
    #    transferido de org, API responde 307 e requests segue.
    meta_url = f"{HF_ENDPOINT}/api/models/{repo_id}/revision/{revision}"
    _download_diag_log(f"[manual:{label}] GET {meta_url}")
    resp = session.get(meta_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    meta = resp.json()
    siblings = meta.get("siblings", [])
    effective_rev = meta.get("sha", revision)
    if not siblings:
        raise RuntimeError(f"API retornou 0 arquivos pra {repo_id}@{revision}")

    cache_model_dir = cache_dir / ("models--" + repo_id.replace("/", "--"))
    blobs_dir = cache_model_dir / "blobs"
    snap_dir = cache_model_dir / "snapshots" / effective_rev
    blobs_dir.mkdir(parents=True, exist_ok=True)
    snap_dir.mkdir(parents=True, exist_ok=True)
    _download_diag_log(
        f"[manual:{label}] {len(siblings)} arquivo(s), effective_rev={effective_rev}"
    )

    total_downloaded = 0
    for sibling in siblings:
        if _cancelled():
            raise RuntimeError("Cancelado pelo usuario")
        rfilename = sibling["rfilename"]
        if skip_weights and rfilename.endswith(
                (".bin", ".safetensors", ".h5", ".msgpack", ".onnx", ".ot", ".pt")):
            continue
        if exclude and any(fnmatch(rfilename, pattern) for pattern in exclude):
            _download_diag_log(f"[manual:{label}] ignorado por filtro: {rfilename}")
            continue
        # rfilename e ETag entram em caminhos locais: rejeitar path traversal
        # de resposta adulterada (defesa em profundidade junto ao pin de SHA).
        _rf = PurePosixPath(rfilename)
        if _rf.is_absolute() or ".." in _rf.parts:
            raise RuntimeError(f"rfilename suspeito rejeitado: {rfilename!r}")
        resolve_url = f"{HF_ENDPOINT}/{repo_id}/resolve/{effective_rev}/{rfilename}"
        head = session.head(resolve_url, timeout=30, allow_redirects=False)
        if head.status_code not in (200, 302, 307):
            raise RuntimeError(
                f"HEAD {rfilename}: status {head.status_code}"
            )
        etag = _etag_from_headers(head.headers)
        if not etag:
            raise RuntimeError(f"{rfilename}: resposta sem ETag")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", etag):
            raise RuntimeError(f"{rfilename}: ETag suspeito rejeitado: {etag!r}")
        expected_size = int(
            head.headers.get("X-Linked-Size")
            or head.headers.get("Content-Length")
            or 0
        )
        blob_path = blobs_dir / etag
        snapshot_path = snap_dir / rfilename
        # Trust a existencia do blob: o nome dele E o hash do conteudo
        # (git sha1 pra inline, sha256 pra LFS/Xet). Checar tamanho nao
        # funciona pra arquivos pequenos — HF devolve X-Linked-Size=300
        # (tamanho do pointer LFS) em vez do tamanho real, o que fazia
        # o cache-hit check dar falso-negativo e re-baixar a cada sessao.
        if blob_path.exists():
            _download_diag_log(
                f"[manual:{label}] cache hit: {rfilename} blob={etag[:12]}..."
            )
            _place_blob_in_snapshot(blob_path, snapshot_path)
            continue
        _download_diag_log(
            f"[manual:{label}] download: {rfilename} etag={etag[:12]}... "
            f"size~={expected_size}"
        )
        tmp_path = blob_path.with_suffix(".incomplete")
        # Retry per-file com backoff exponencial. Em conexao flaky (caso da
        # Denise em 2026-04-25, log no feedback), o download de um blob de
        # ~3 GB falha aos ~40% e a UI atual exige clique manual em "Voltar".
        # Aqui retentamos automaticamente ate 5 vezes (delays 0/2/4/8/16s);
        # se 5 falhas, levanta a ultima excecao pra UI mostrar erro fatal.
        last_exc: Exception | None = None
        for _attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
            if _attempt > 0:
                wait = _DOWNLOAD_BACKOFF[_attempt]
                _emit_event(
                    "model_download_retry",
                    f"Reconectando em {wait}s ({_attempt + 1}/{_DOWNLOAD_MAX_ATTEMPTS}) "
                    f"— falha: {type(last_exc).__name__ if last_exc else '?'}",
                    start_pct + int((end_pct - start_pct) * total_downloaded / max(estimated_bytes, 1)),
                )
                _download_diag_log(
                    f"[manual:{label}] retry {_attempt + 1}/{_DOWNLOAD_MAX_ATTEMPTS} "
                    f"after {type(last_exc).__name__}: {last_exc}"
                )
                time.sleep(wait)
                if _cancelled():
                    raise RuntimeError("Cancelado pelo usuario")
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
            try:
                with session.get(resolve_url, stream=True, timeout=60, allow_redirects=True) as dl:
                    dl.raise_for_status()
                    bytes_this_file = 0
                    with tmp_path.open("wb") as fh:
                        for chunk in dl.iter_content(chunk_size=_DOWNLOAD_CHUNK):
                            if _cancelled():
                                raise RuntimeError("Cancelado pelo usuario")
                            if not chunk:
                                continue
                            fh.write(chunk)
                            bytes_this_file += len(chunk)
                            _emit_bytes(total_downloaded + bytes_this_file)
                    total_downloaded += bytes_this_file
                break  # sucesso, sai do retry loop
            except (requests.RequestException, OSError) as exc:
                last_exc = exc
                if _attempt == _DOWNLOAD_MAX_ATTEMPTS - 1:
                    raise
        tmp_path.replace(blob_path)
        _place_blob_in_snapshot(blob_path, snapshot_path)

    refs_main = cache_model_dir / "refs" / "main"
    refs_main.parent.mkdir(parents=True, exist_ok=True)
    refs_main.write_text(effective_rev, encoding="utf-8")
    _download_diag_log(
        f"[manual:{label}] done, total_downloaded={total_downloaded}B"
    )
    return snap_dir


def download_required_models(
    *,
    token: str | None = None,
    token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN",
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
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
    models = get_required_models(asr_variants, include_diarization=include_diarization,
                                 include_alignment=include_alignment)
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
        # `_manual_snapshot_download` ja emite model_download_bytes via
        # progress_callback; nao precisa do poller de disk polling.
        try:
            # Exige revision pinada: sem SHA nao temos como garantir que o
            # /api/models/.../revision/{rev} responde com a lista certa de
            # arquivos. O pin tambem e defesa supply-chain. Todos os assets
            # configurados no projeto tem revision.
            if not asset.revision:
                raise RuntimeError(
                    f"Modelo {asset.label} ({asset.repo_id}) sem revision "
                    "pinada; abortando download. Atualize ASR_VARIANTS / "
                    "_FIXED_MODELS com a SHA do revision."
                )
            _manual_snapshot_download(
                repo_id=asset.repo_id,
                revision=asset.revision,
                cache_dir=cache_dir,
                token=token_value,
                label=asset.label,
                start_pct=start_pct,
                end_pct=end_pct,
                estimated_bytes=estimated_bytes,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
                exclude=asset.download_exclude,
            )
        except Exception as exc:  # noqa: BLE001 - keep batch progress visible.
            failures += 1
            # Logar o traceback completo no download_diagnostic.log. Sem isso
            # o crash do Round 2 do Rogerio (2026-04-22) ficou invisivel —
            # progress_callback so emite uma mensagem truncada que nao da
            # pra diagnosticar retrospectivamente.
            import traceback
            _download_diag_log(
                f"[manual:{asset.label}] EXCEPTION: {type(exc).__name__}: "
                f"{sanitize_message(str(exc))}"
            )
            for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
                for subline in line.rstrip().splitlines():
                    _download_diag_log(f"  {sanitize_message(subline)}")
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "model_download_error",
                        "progress": end_pct,
                        "message": f"Falha ao baixar {asset.label}: {sanitize_message(str(exc))}",
                    }
                )
            continue
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


def verify_required_models(
    progress_callback: ProgressCallback | None = None,
    asr_variants: list[str] | None = None,
    include_diarization: bool = True,
    include_alignment: bool = True,
) -> int:
    """Verifica se os modelos obrigatorios estao no cache local.

    Antes chamava snapshot_download(local_files_only=True) — caminho que
    envolve huggingface_hub e pode falhar no bundle PyInstaller frozen
    (detectado via Xet migration). Agora usa nosso proprio layout-check
    (cached_snapshot_path + _snapshot_has_weights), identico ao usado em
    required_models_ready(). Zero dep de hub pra verificar.
    """
    cache_dir = runtime.model_cache_dir()
    failures = 0
    # get_required_models(asr_variants): verificar os variants que o chamador
    # baixou/configurou — verificar sempre REQUIRED_MODELS (default) gerava
    # falha falsa apos download bem-sucedido de outra variante.
    assets = get_required_models(asr_variants, include_diarization=include_diarization,
                                 include_alignment=include_alignment)
    total = max(1, len(assets))
    _download_diag_log(f"[verify] start cache_dir={cache_dir} assets={total}")
    for index, asset in enumerate(assets, start=1):
        path = cached_snapshot_path(asset.repo_id, cache_dir, revision=asset.revision)
        path_exists = path is not None and path.exists()
        has_weights = False
        if path is not None:
            has_weights = _snapshot_has_weights(path)
        if path_exists and has_weights:
            ok = True
            message = f"{asset.label} pronto para uso local."
        else:
            failures += 1
            ok = False
            message = f"{asset.label} ausente ou incompleto no cache local."
        _download_diag_log(
            f"[verify] {asset.repo_id}@{(asset.revision or 'main')[:12]}: "
            f"path={path} exists={path_exists} has_weights={has_weights} ok={ok}"
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "model_verify",
                    "progress": int((index / total) * 100),
                    "message": message,
                    "ok": ok,
                }
            )
    _download_diag_log(f"[verify] done failures={failures}/{total}")
    return failures


def local_pyannote_checkpoint() -> str:
    validate_local_diarization_model(LOCAL_PYANNOTE_MODEL)
    cache_dir = runtime.model_cache_dir()
    runtime.apply_secure_hf_environment(offline=True)
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(
            repo_id=LOCAL_PYANNOTE_MODEL,
            revision=LOCAL_PYANNOTE_REVISION,
            repo_type="model",
            cache_dir=str(cache_dir),
            local_files_only=True,
            token=None,
        )
    except Exception as exc:  # noqa: BLE001 - fallback preserves a clearer domain error.
        fallback = cached_snapshot_path(
            LOCAL_PYANNOTE_MODEL, cache_dir, revision=LOCAL_PYANNOTE_REVISION
        )
        if fallback is not None:
            return str(fallback)
        raise RuntimeError(
            "Modelo de diarizacao local nao encontrado. Rode `transcribe_pipeline models download` "
            "com o token Hugging Face do usuario antes de diarizar."
        ) from exc


def write_status_json(path: Path) -> None:
    path.write_text(json.dumps(status_as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
