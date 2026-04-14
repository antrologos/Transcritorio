from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

# Patterns that should never appear in logs or user-facing messages.
_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{8,}")


def sanitize_message(text: str) -> str:
    """Remove HuggingFace tokens and auth headers from a string."""
    return _TOKEN_RE.sub("<REDACTED>", text)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido em {path.name}: {exc.msg} (linha {exc.lineno})") from exc


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _no_window_flags() -> dict[str, int]:
    """Return creationflags to suppress console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_no_window_flags(),
    )


def secure_subprocess_env() -> dict[str, str]:
    """Return a copy of os.environ with sensitive variables removed."""
    env = dict(os.environ)
    for key in list(env):
        if key.upper() in {"HF_TOKEN", "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN"}:
            del env[key]
    return env


def run_command_stream(
    args: list[str],
    cwd: Path | None = None,
    on_output: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=secure_subprocess_env(),
        **_no_window_flags(),
    )
    stdout_parts: list[str] = []
    output_queue: queue.Queue[str] = queue.Queue()
    cancelled = False

    def read_output() -> None:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            output_queue.put(chunk)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    while process.poll() is None or reader.is_alive() or not output_queue.empty():
        try:
            chunk = output_queue.get(timeout=0.1)
        except queue.Empty:
            chunk = ""
        if chunk:
            stdout_parts.append(chunk)
            if on_output is not None:
                on_output(chunk)
        if process.poll() is None and should_cancel is not None and should_cancel():
            cancelled = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.poll() is not None and not reader.is_alive() and output_queue.empty():
            break
        time.sleep(0.01)

    reader.join(timeout=1)
    return_code = process.wait()
    if cancelled and return_code == 0:
        return_code = 130
    return subprocess.CompletedProcess(args, return_code, "".join(stdout_parts), "")


def format_timestamp(seconds: float | int | None, millis: bool = False) -> str:
    if seconds is None:
        seconds = 0
    total_ms = max(0, int(round(float(seconds) * 1000)))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    minute = total_minutes % 60
    hour = total_minutes // 60
    if millis:
        return f"{hour:02d}:{minute:02d}:{sec:02d}.{ms:03d}"
    return f"{hour:02d}:{minute:02d}:{sec:02d}"


def relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())
