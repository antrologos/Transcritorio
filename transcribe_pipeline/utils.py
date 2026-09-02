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


_OUTPUT_ID_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_output_id(raw_id: str) -> str:
    """Nome de arquivo de saida derivado do interview_id.

    O id e o stem do arquivo de midia e TODO o pipeline (WAV, pyannote,
    render.find_whisperx_json, canonical, review) o usa cru — a saida do
    ASR tem de nascer com o nome IDENTICO, com espacos, acentos e o que
    mais o sistema operacional aceitar. So separadores de caminho e
    caracteres proibidos em nome de arquivo viram "_" (defesa contra
    manifest editado a mao: "../../../evil" fica dentro da pasta). Vazio
    ou so pontos/espacos devolve "" (invalido).
    """
    safe = _OUTPUT_ID_UNSAFE_RE.sub("_", str(raw_id or ""))
    return safe if safe.strip("._ ") else ""


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
        # Mesmo env sanitizado de run_command_stream: ffmpeg/ffprobe nao
        # precisam (nem devem) herdar o token HF do processo pai.
        env=secure_subprocess_env(),
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
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # env: sobrescreve o ambiente do filho (ex.: worker GPU do Parakeet
    # com PYTHONPATH/PATH proprios). Quem passa e responsavel por partir
    # de secure_subprocess_env() — nunca de os.environ cru.
    process = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env if env is not None else secure_subprocess_env(),
        **_no_window_flags(),
    )
    stdout_parts: list[str] = []
    output_queue: queue.Queue[str] = queue.Queue()
    cancelled = False

    def read_output() -> None:
        if process.stdout is None:
            return
        buf = ""
        while True:
            ch = process.stdout.read(1)
            if not ch:
                if buf:
                    output_queue.put(buf)
                break
            buf += ch
            if ch == "\n" or ch == "\r":
                output_queue.put(buf)
                buf = ""

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    while process.poll() is None or reader.is_alive() or not output_queue.empty():
        try:
            chunk = output_queue.get(timeout=2.0)
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
        if not chunk:
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


PROGRESS_JSON_PREFIX = "@PROGRESS "


def parse_progress_json_line(line: str) -> dict[str, Any] | None:
    """Parse uma linha '@PROGRESS {json}' emitida pelo CLI (--progress-json).

    Retorna o dict do evento ({event, progress, message}) ou None para
    qualquer outra linha (logs humanos, vazio, JSON invalido). Usado pela GUI
    para acompanhar a diarizacao rodando em subprocesso (v0.2).
    """
    stripped = (line or "").strip()
    if not stripped.startswith(PROGRESS_JSON_PREFIX):
        return None
    try:
        payload = json.loads(stripped[len(PROGRESS_JSON_PREFIX):])
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_interview_artifact(name: str, interview_id: str) -> bool:
    """True se o nome de arquivo pertence a esta entrevista.

    Derivados seguem os padroes {id}.ext, {id}.kind.ext e {id}_nvivo.tsv.
    Um rglob("{id}*") sozinho tambem casaria "entrevista_10.json" para o id
    "entrevista_1" — este filtro evita apagar/mover artefatos de outra entrevista.
    """
    return (
        name == interview_id
        or name.startswith(interview_id + ".")
        or name == interview_id + "_nvivo.tsv"
    )
