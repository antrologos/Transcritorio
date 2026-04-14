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
