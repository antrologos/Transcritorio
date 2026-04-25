from __future__ import annotations

from pathlib import Path
from typing import Mapping
import os
import shutil
import sys


APP_NAME = "Transcritorio"
APP_HOME_ENV = "TRANSCRITORIO_HOME"
APP_RUNTIME_ENV = "TRANSCRITORIO_RUNTIME_DIR"
MODEL_CACHE_ENV = "TRANSCRITORIO_MODEL_CACHE"


def app_data_dir() -> Path:
    configured = os.environ.get(APP_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / APP_NAME


def model_cache_dir() -> Path:
    configured = os.environ.get(MODEL_CACHE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return app_data_dir() / "models" / "huggingface"


def platform_tag() -> str:
    if os.name == "nt":
        return "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if "arm" in os.uname().machine.lower() else "macos-x64"
    machine = getattr(os, "uname", lambda: None)()
    arch = machine.machine.lower() if machine else "x64"
    return "linux-arm64" if "aarch64" in arch or "arm64" in arch else "linux-x64"


def runtime_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get(APP_RUNTIME_ENV)
    if configured:
        roots.append(Path(configured).expanduser())
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: _MEIPASS (onedir == exe parent) and exe dir.
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        roots.append(meipass)
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir != meipass:
            roots.append(exe_dir)
    else:
        package_root = Path(__file__).resolve().parent.parent
        roots.extend(
            [
                package_root / "runtime" / platform_tag(),
                package_root / "runtime",
                Path(sys.executable).resolve().parent,
            ]
        )
    result: list[Path] = []
    for root in roots:
        if root not in result:
            result.append(root)
    return result


def resolve_executable(name: str) -> str:
    executable_name = f"{name}.exe" if os.name == "nt" and not name.lower().endswith(".exe") else name
    candidates: list[Path] = []
    for root in runtime_roots():
        candidates.extend(
            [
                root / executable_name,
                root / "bin" / executable_name,
                root / "Scripts" / executable_name,
                root / "ffmpeg" / "bin" / executable_name,
                root / "vendor" / "ffmpeg" / "bin" / executable_name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    return found or name


def secure_hf_environment(
    *,
    offline: bool = False,
    token: str | None = None,
    token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN",
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base or os.environ)
    cache_dir = model_cache_dir()
    env["TRANSCRITORIO_HOME"] = str(app_data_dir())
    env["HF_HOME"] = str(cache_dir.parent)
    env["HF_HUB_CACHE"] = str(cache_dir)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    # Network timeouts: bound each HTTP request so a hung TLS handshake or
    # stalled response stream fails fast (30s) instead of deadlocking the
    # wizard. Set at env level so hub library picks up at import time.
    # Confirmed hang symptom: refs/main written (40B) but .incomplete blobs
    # stay 0B for 6+ minutes. curl on same machine downloads in <1s.
    env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    env.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
    # Disable hf_transfer Rust-based downloader: seen hanging on Windows
    # bundled builds (not available / DLL issues). Fallback to pure Python
    # requests client which is proven on the user's actual network.
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    env["DO_NOT_TRACK"] = "1"
    env["PYANNOTE_METRICS_ENABLED"] = "0"
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
        env.pop(token_env, None)
        env.pop("HF" + "_TOKEN", None)
    else:
        env.pop("HF_HUB_OFFLINE", None)
        if token_env != "HF" + "_TOKEN":
            env.pop("HF" + "_TOKEN", None)
        if token is not None:
            env[token_env] = token
    return env


def apply_secure_hf_environment(*, offline: bool = False, token: str | None = None, token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN") -> None:
    env = secure_hf_environment(offline=offline, token=token, token_env=token_env)
    if token_env not in env:
        os.environ.pop(token_env, None)
    if token_env != "HF" + "_TOKEN":
        os.environ.pop("HF" + "_TOKEN", None)
    if "HF_HUB_OFFLINE" not in env:
        os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.update(env)


def redacted_token_env(env: Mapping[str, str], token_env: str = "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN") -> dict[str, str]:
    redacted = dict(env)
    if redacted.get(token_env):
        redacted[token_env] = "<TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN>"
    return redacted


# ---------------------------------------------------------------------------
# GPU / device detection (lazy: torch is only imported on first call)
# ---------------------------------------------------------------------------

_detected_device: str | None = None
_cuda_libs_detected: bool | None = None
_nvidia_gpu_detected: bool | None = None


def has_nvidia_gpu() -> bool:
    """Return True when nvidia-smi reports at least one NVIDIA GPU.

    Used by the first-run GUI dialog to offer the CUDA pack install on
    Windows when the base installer was chosen. Cached. Windows-only in
    practice (Mac doesn't have NVIDIA; Linux bundles are CPU by default).
    """
    global _nvidia_gpu_detected
    if _nvidia_gpu_detected is not None:
        return _nvidia_gpu_detected
    try:
        import subprocess
        # No-window flag for Windows so nvidia-smi doesn't flash a console
        kwargs: dict = {"capture_output": True, "timeout": 5}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        result = subprocess.run(["nvidia-smi"], **kwargs)
        _nvidia_gpu_detected = result.returncode == 0
    except Exception:
        _nvidia_gpu_detected = False
    return _nvidia_gpu_detected


def cuda_libs_present() -> bool:
    """Check if the cuda_pack (GPU acceleration pack) is installed.

    Uses `cudnn_ops64_9.dll` (Windows) as a canary: it is one of the 14
    CUDA DLLs that the 'cpu' bundle variant strips and the cuda_pack
    ships back. Presence = cuda_pack was extracted over the bundle;
    absence = base bundle only, GPU inference will crash on Conv/LSTM.

    2026-04-23: switched from torch_cuda.dll to cudnn_ops64_9.dll because
    torch_cuda.dll IS an IAT dependency of torch core and must stay in
    the base bundle (removing it breaks `import torch`).

    Returns False if torch is not importable or torch.__file__ is missing.
    Result is cached on the first call.
    """
    global _cuda_libs_detected
    if _cuda_libs_detected is not None:
        return _cuda_libs_detected
    try:
        import torch
        torch_file = getattr(torch, "__file__", None)
        if not torch_file:
            _cuda_libs_detected = False
            return _cuda_libs_detected
        lib_dir = Path(torch_file).parent / "lib"
        if sys.platform == "win32":
            candidates = [lib_dir / "cudnn_ops64_9.dll"]
        elif sys.platform == "darwin":
            candidates = [lib_dir / "libcudnn_ops.dylib"]
        else:
            candidates = [lib_dir / "libcudnn_ops.so"]
        _cuda_libs_detected = any(c.exists() for c in candidates)
    except Exception:
        _cuda_libs_detected = False
    return _cuda_libs_detected


def detect_device() -> str:
    """Return 'cuda' | 'mps' | 'cpu' based on available accelerator.

    MPS (Apple Silicon) is detected but not accepted by CTranslate2/faster-whisper
    for ASR — resolve_device() will fall back mps -> cpu for the ASR path.
    pyannote supports MPS partially.

    2026-04-23: Windows guard — torch.cuda.is_available() can return True on
    the base bundle (11 CUDA IAT DLLs present, torch_cuda.dll loadable) even
    when cuda_pack is NOT installed. In that state Conv/LSTM crash with
    "Could not locate cudnn_graph64_9.dll". Require cuda_libs_present() on
    Windows to actually choose cuda; otherwise fall through to cpu so the
    GUI first-run flow offers cuda_pack and meanwhile CPU inference works.
    """
    global _detected_device
    if _detected_device is not None:
        return _detected_device
    try:
        import torch
        if torch.cuda.is_available():
            if sys.platform == "win32" and not cuda_libs_present():
                # Base bundle without cuda_pack — cudnn engines missing.
                # Fall through to mps/cpu branches.
                pass
            else:
                _detected_device = "cuda"
                return _detected_device
    except Exception:
        pass
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            _detected_device = "mps"
            return _detected_device
    except Exception:
        pass
    _detected_device = "cpu"
    return _detected_device


def resolve_device(configured: str | None) -> tuple[str, bool]:
    """Resolve effective device for the ASR path. Returns (device, fell_back).

    CT2/faster-whisper does not support MPS, so mps is coerced to cpu here.
    Callers that want the raw detection should use detect_device() instead.
    """
    wanted = (configured or "cuda").lower()
    if wanted == "cpu":
        return "cpu", False
    detected = detect_device()
    if detected == "cuda":
        return "cuda", False
    return "cpu", True


def describe_backend(configured_device: str | None = None) -> str:
    """Short human-readable label of the active ASR backend.

    Used by the GUI header so the user can confirm at a glance whether GPU
    acceleration is in use.

    When ``configured_device == "cpu"`` (user forced CPU explicitly via
    "Configurar transcricao"), returns "CPU" regardless of detected hardware.
    Other values ("auto"/"cuda"/"mps"/None/"") fall through to ``detect_device()``,
    preserving auto-detection (including MLX on Apple Silicon). Avoids passing
    the value through ``resolve_device()`` because ``resolve_device("mps")``
    coerces to "cpu" (CT2 incompatibility) and would erase the MLX label.
    """
    cfg = (configured_device or "").strip().lower()
    if cfg == "cpu":
        return "CPU"
    device = detect_device()
    if device == "cuda":
        return "CUDA (NVIDIA)"
    if device == "mps":
        try:
            from . import mlx_whisper_runner
            if mlx_whisper_runner.is_available():
                return "MLX (Metal)"
        except Exception:
            pass
        return "MPS (sem MLX - usando CPU)"
    return "CPU"
