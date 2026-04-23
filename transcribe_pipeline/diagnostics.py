"""Forensics + startup probes para facilitar diagnose de falhas.

Tres coisas neste modulo:

1. `log_environment_snapshot()` — grava no download_diagnostic.log uma linha
   por atributo relevante do ambiente (Python, bundle, platform, cache dir,
   env vars HF, etc.). Chamado no startup de CLI e GUI, cada sessao grava
   o contexto no topo.

2. `symlinks_supported()` — testa na primeira chamada se o OS atual
   permite os.symlink via um sentinela em tempdir. Resultado cacheado.
   Evita tentativas caras de symlink_to + fallback em Windows onde
   Developer Mode esta off. Usado por `model_manager._place_blob_in_snapshot`.

3. `enable_faulthandler()` — ativa Python faulthandler escrevendo num
   arquivo separado (`faulthandler.log`). Captura segfault, stack
   overflow em extensoes nativas (torch, ctranslate2, pyannote) que
   sys.excepthook nao pega.
"""
from __future__ import annotations

import faulthandler
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


_SYMLINK_PROBE: Optional[bool] = None
_FAULT_FILE: Optional[object] = None
_ENV_SNAPSHOT_DONE: bool = False


def _log(message: str) -> None:
    """Append to download_diagnostic.log via model_manager helper.

    Importa lazy pra evitar ciclo de import.
    """
    try:
        from .model_manager import _download_diag_log
        _download_diag_log(message)
    except Exception:
        pass


def symlinks_supported() -> bool:
    """True se os.symlink funciona no ambiente atual. Cacheado.

    Windows sem Developer Mode (e sem admin) rejeita os.symlink com
    `OSError: [WinError 1314]`. Esta probe detecta 1 vez, cacheia, e
    permite que callers evitem tentar + cair em fallback.
    """
    global _SYMLINK_PROBE
    if _SYMLINK_PROBE is not None:
        return _SYMLINK_PROBE
    ok = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "probe_src"
            src.write_text("probe", encoding="utf-8")
            link = Path(tmp) / "probe_link"
            try:
                link.symlink_to(src.name)
                ok = link.exists() and link.is_symlink()
            except (OSError, NotImplementedError):
                ok = False
    except OSError:
        ok = False
    _SYMLINK_PROBE = ok
    return ok


def enable_faulthandler() -> None:
    """Ativa Python faulthandler escrevendo em app_data_dir/faulthandler.log.

    Captura crashes de extensoes nativas (torch, ctranslate2, pyannote,
    PySide6) que sys.excepthook nao ve. O arquivo fica aberto pelo ciclo
    de vida do processo propositalmente (pra funcionar quando Python
    crashar e nao tiver mais como abrir arquivos).
    """
    global _FAULT_FILE
    if _FAULT_FILE is not None:
        return
    try:
        from . import runtime
        log_dir = runtime.app_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        fh_path = log_dir / "faulthandler.log"
        # Append mode + unbuffered pra sobreviver crash
        _FAULT_FILE = open(fh_path, "a", buffering=1, encoding="utf-8")
        _FAULT_FILE.write(
            f"\n--- faulthandler ativado {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"pid={os.getpid()} ---\n"
        )
        _FAULT_FILE.flush()
        faulthandler.enable(file=_FAULT_FILE)
    except Exception as exc:
        # Se faulthandler nao puder ativar, nao bloqueia inicializacao
        _log(f"[env] faulthandler enable failed: {exc}")


def log_environment_snapshot() -> None:
    """Grava uma foto do ambiente no download_diagnostic.log.

    Idempotente: so executa uma vez por sessao. Chamado no startup do
    CLI e do GUI. Objetivo: qualquer falha reportada pelo usuario pode
    ser triada a partir da primeira linha do log.
    """
    global _ENV_SNAPSHOT_DONE
    if _ENV_SNAPSHOT_DONE:
        return
    _ENV_SNAPSHOT_DONE = True
    try:
        from . import runtime
    except Exception:
        return

    _log("=" * 72)
    _log(f"[env] session_start {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _log(f"[env] python={sys.version.split()[0]} "
         f"frozen={bool(getattr(sys, 'frozen', False))}")
    _log(f"[env] executable={sys.executable}")
    try:
        _log(f"[env] platform={platform.platform()}")
    except Exception:
        pass
    _log(f"[env] symlinks_supported={symlinks_supported()}")
    try:
        cache = runtime.model_cache_dir()
        _log(f"[env] cache_dir={cache}")
        base = cache if cache.exists() else cache.parent
        try:
            du = shutil.disk_usage(str(base))
            _log(f"[env] free_disk_gb={du.free / (1 << 30):.1f}")
        except OSError:
            pass
    except Exception as exc:
        _log(f"[env] cache_dir query failed: {exc}")
    for mod_name in ("huggingface_hub", "faster_whisper",
                     "transformers", "whisperx", "pyannote.audio", "torch"):
        try:
            mod = __import__(mod_name.replace(".", "_") if "." in mod_name
                             else mod_name)
            ver = getattr(mod, "__version__", "unknown")
            _log(f"[env] {mod_name}={ver}")
        except ImportError:
            _log(f"[env] {mod_name}=NOT_IMPORTED")
        except Exception as exc:
            _log(f"[env] {mod_name} probe failed: {exc}")
    for key in (
        "TRANSCRITORIO_HOME", "TRANSCRITORIO_MODEL_CACHE",
        "HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE",
        "HF_HUB_DOWNLOAD_TIMEOUT", "HF_HUB_ETAG_TIMEOUT",
        "HF_HUB_ENABLE_HF_TRANSFER",
    ):
        val = os.environ.get(key)
        if val:
            # Don't log token values
            if "TOKEN" in key.upper():
                val = f"<set len={len(val)}>"
            _log(f"[env] {key}={val}")
    _log("=" * 72)


def startup_init() -> None:
    """Wrapper: chama tudo na ordem certa. Use no inicio do main()."""
    enable_faulthandler()
    log_environment_snapshot()
