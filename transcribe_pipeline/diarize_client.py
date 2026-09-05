"""Cliente do servidor de diarizacao (`transcritorio-cli diarize-serve`).

Contexto (2026-09-02): um lote com N arquivos abria N processos `diarize`,
cada um pagando ~35 s em GPU (abrir o Python, importar torch/pyannote,
carregar o modelo) — mais que a propria separacao numa entrevista de
50 min. O servidor carrega uma vez e atende pedidos pelo stdin; este
cliente fala o protocolo (linhas UTF-8):

    GUI  -> {"ids": ["X"]}                       um pedido por vez
    serv -> @PROGRESS {...}                      contrato do --progress-json
    serv -> @DONE {"ids": [...], "failures": n}
    GUI  -> {"quit": true}                       encerra (ou EOF)

Antes do primeiro pedido o servidor escreve `@READY {...}`; se o modelo
nao carrega, `@DONE {"error": ...}` e sai. Qualquer quebra (processo
morto, timeout, linha inesperada) marca o cliente como indisponivel e
`run()` devolve None — quem chama cai para o subprocesso por arquivo, a
mesma rota de sempre. Cancelar mata o servidor (semantica identica a do
subprocesso unico: terminate/kill).

Sem Qt e sem torch aqui: e stdlib + runtime/utils do pacote, testavel com
um servidor falso (tests/toy_diarize_serve.py).
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import runtime
from .utils import (
    DONE_JSON_PREFIX,
    PROGRESS_JSON_PREFIX,
    READY_JSON_PREFIX,
    _no_window_flags,
    parse_prefixed_json_line,
    secure_subprocess_env,
)

_logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]
_EOF = object()


class DiarizeServer:
    """Um processo `diarize-serve` por lote; `run()` por arquivo."""

    # Em CPU a carga do modelo levou ate ~2 min nas medicoes; folga grande
    # porque a espera so acontece no 1o pedido (a carga corre em paralelo
    # com o preparo/transcricao do 1o arquivo).
    READY_TIMEOUT = 900.0
    STOP_TIMEOUT = 5.0

    def __init__(self, project_root: Path, command: list[str] | None = None,
                 threads: int = 0) -> None:
        self.project_root = Path(project_root)
        # 0 = nao mexer (o filho fica como sempre foi).
        self.threads = max(0, int(threads or 0))
        self._command = list(command) if command else runtime.cli_command(
            "--project", str(self.project_root), "diarize-serve",
        )
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[Any] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._ready = False
        self._dead = False
        self.failure_reason = ""
        self.served = 0

    def _child_env(self) -> dict[str, str]:
        """Ambiente do filho, composto LOCALMENTE sobre o funil comum.

        `secure_subprocess_env()` vale para TODO subprocesso do app — ffmpeg,
        ffprobe, canais, worker da LLM. Por um limite de threads la dentro
        reafinaria todos eles em silencio, entao a composicao acontece aqui.
        As variaveis precisam existir ANTES de o filho importar torch."""
        from .capabilities import thread_env

        env = dict(secure_subprocess_env())
        env.update(thread_env(self.threads))
        return env

    # --- ciclo de vida -------------------------------------------------
    def start(self) -> bool:
        """Abre o processo SEM esperar o modelo (a carga corre em paralelo)."""
        if self._process is not None:
            return self.alive()
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self._child_env(),
                **_no_window_flags(),
            )
        except Exception as exc:  # noqa: BLE001 - sem servidor, sem drama: fallback
            self._mark_dead(f"não abriu: {exc}")
            return False
        self._reader = threading.Thread(target=self._read_lines, daemon=True)
        self._reader.start()
        return True

    def alive(self) -> bool:
        return (self._process is not None and not self._dead
                and self._process.poll() is None)

    def stop(self) -> None:
        """`quit` educado, depois terminate/kill. Idempotente."""
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(json.dumps({"quit": True}) + "\n")
                    process.stdin.flush()
                    process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=self.STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._kill()
        self._close_stdin()
        self._dead = True

    # --- pedidos -------------------------------------------------------
    def wait_ready(self, on_progress: ProgressCallback | None = None,
                   should_cancel: Callable[[], bool] | None = None) -> bool:
        """Consome linhas ate o @READY. False = servidor indisponivel."""
        if self._ready:
            return True
        if not self.alive():
            return False
        deadline = time.monotonic() + self.READY_TIMEOUT
        while True:
            kind, payload = self._next_line(0.5)
            if kind == "ready":
                self._ready = True
                return True
            if kind == "progress" and on_progress is not None:
                on_progress(payload)
            elif kind == "done":
                self._mark_dead(f"modelo não carregou: {payload.get('error', '')}")
                return False
            elif kind == "eof":
                self._mark_dead("servidor encerrou antes de ficar pronto")
                return False
            if should_cancel is not None and should_cancel():
                self._kill()
                self._mark_dead("cancelado")
                return False
            if time.monotonic() > deadline:
                self._kill()
                self._mark_dead("tempo esgotado ao carregar o modelo")
                return False

    def run(self, ids: list[str], on_progress: ProgressCallback | None = None,
            should_cancel: Callable[[], bool] | None = None) -> int | None:
        """Separa falantes de `ids`. Devolve o numero de falhas, ou None
        quando o servidor nao esta disponivel (quem chama faz fallback)."""
        ids = [str(item) for item in ids]
        if not self.wait_ready(on_progress, should_cancel):
            return None
        process = self._process
        assert process is not None
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"ids": ids}) + "\n")
            process.stdin.flush()
        except (OSError, ValueError, AssertionError) as exc:
            self._mark_dead(f"não aceitou o pedido: {exc}")
            return None
        while True:
            kind, payload = self._next_line(0.5)
            if kind == "progress":
                if on_progress is not None:
                    on_progress(payload)
            elif kind == "done":
                self.served += 1
                try:
                    return int(payload.get("failures", 0))
                except (TypeError, ValueError):
                    return len(ids) or 1
            elif kind == "eof":
                self._mark_dead("servidor encerrou no meio do pedido")
                return None
            if should_cancel is not None and should_cancel():
                # Mesma semantica do subprocesso unico: cancelar mata.
                self._kill()
                self._mark_dead("cancelado")
                return len(ids) or 1

    # --- internos ------------------------------------------------------
    def _read_lines(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._lines.put(_EOF)
            return
        try:
            for line in process.stdout:
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        self._lines.put(_EOF)

    def _next_line(self, timeout: float) -> tuple[str, dict[str, Any]]:
        """('progress'|'ready'|'done'|'eof'|'', payload)."""
        try:
            item = self._lines.get(timeout=timeout)
        except queue.Empty:
            if self._process is not None and self._process.poll() is not None and self._lines.empty():
                return "eof", {}
            return "", {}
        if item is _EOF:
            return "eof", {}
        line = str(item)
        for kind, prefix in (("progress", PROGRESS_JSON_PREFIX), ("ready", READY_JSON_PREFIX),
                             ("done", DONE_JSON_PREFIX)):
            payload = parse_prefixed_json_line(line, prefix)
            if payload is not None:
                return kind, payload
        return "", {}

    def _kill(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=self.STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=self.STOP_TIMEOUT)
            except OSError:
                pass
        self._close_stdin()

    def _close_stdin(self) -> None:
        # Fechar explicitamente: com o filho morto, o flush do TextIOWrapper
        # na coleta de lixo estourava "Exception ignored ... Errno 22".
        process = self._process
        if process is None or process.stdin is None:
            return
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass

    def _mark_dead(self, reason: str) -> None:
        self._dead = True
        self.failure_reason = reason
        _logger.warning("servidor de diarizacao indisponivel: %s", reason)
