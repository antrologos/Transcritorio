"""Toy test: retry-loop em _manual_snapshot_download.

v0.1.8 introduziu retry per-blob: se a conexao falha mid-stream, retentamos
ate 5x com backoff exponencial. Caso historico (Denise, 2026-04-25): user
em internet flaky tinha que clicar "Voltar"+"Proximo" 4-5 vezes ate completar.

Valida:
- Falha 3 vezes seguidas, sucesso na 4a tentativa: download completa
- Callback "model_download_retry" eh emitido entre tentativas
- 5 falhas seguidas: levanta a ultima excecao (UI mostra erro fatal)
- Sleep do backoff eh chamado mas nao bloqueia o teste (mockado)

Run: python -B tests/toy_manual_snapshot_retry.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import requests  # noqa: F401
except ImportError:
    print("SKIP: requests nao instalado neste venv")
    sys.exit(0)

from transcribe_pipeline.model_manager import _manual_snapshot_download  # noqa: E402


class _FakeHead:
    def __init__(self, status: int, headers: dict) -> None:
        self.status_code = status
        self.headers = headers


class _FailingThenSucceedingGet:
    """Stream get que falha N vezes (RequestException) e depois retorna body."""

    def __init__(self, body: bytes, fail_count: int) -> None:
        self._body = body
        self._fail_count = fail_count
        self._call_count = 0
        self.status_code = 200

    def __call__(self, *args, **kwargs):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise requests.exceptions.ConnectionError(
                f"Simulated failure {self._call_count}/{self._fail_count}"
            )
        return self  # supports `with session.get(...) as dl:`

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size: int):
        pos = 0
        while pos < len(self._body):
            yield self._body[pos:pos + chunk_size]
            pos += chunk_size


def _make_session(rev_sha: str, body: bytes, fail_count: int):
    """Session que falha `fail_count` vezes no stream-get e depois funciona."""
    failing_get = _FailingThenSucceedingGet(body, fail_count)
    session = MagicMock()
    session.headers = {}

    def get_router(url, timeout=None, allow_redirects=True, stream=False, **kwargs):
        if "/api/models/" in url:
            resp = MagicMock()
            resp.json.return_value = {
                "sha": rev_sha,
                "siblings": [{"rfilename": "model.bin"}],
            }
            resp.raise_for_status = lambda: None
            return resp
        if stream:
            return failing_get(url)
        raise AssertionError(f"GET nao-stream inesperado: {url}")

    def head_router(url, timeout=None, allow_redirects=False, **kwargs):
        return _FakeHead(302, {
            "X-Linked-ETag": '"etag-sha"',
            "X-Linked-Size": str(len(body)),
        })

    session.get.side_effect = get_router
    session.head.side_effect = head_router
    return session, failing_get


def test_retry_succeeds_after_3_failures() -> None:
    """3 falhas, depois OK: retry resolve sem que UI veja exception."""
    rev = "a" * 40
    body = b"x" * 4096
    events: list[dict] = []
    cb = lambda d: events.append(dict(d))
    with tempfile.TemporaryDirectory() as tmp:
        session, fail_get = _make_session(rev, body, fail_count=3)
        with patch("requests.Session", return_value=session), \
             patch("transcribe_pipeline.model_manager.time.sleep") as fake_sleep:
            snap = _manual_snapshot_download(
                repo_id="fakeorg/fakemodel",
                revision=rev,
                cache_dir=Path(tmp),
                token=None,
                label="TestModel",
                start_pct=0,
                end_pct=100,
                estimated_bytes=len(body),
                progress_callback=cb,
                should_cancel=None,
            )
        # Snap dir foi criado e blob baixado
        assert snap.exists()
        # 4 tentativas (3 falhas + 1 sucesso)
        assert fail_get._call_count == 4, f"esperado 4 chamadas, got {fail_get._call_count}"
        # 3 sleeps de backoff entre tentativas (attempt=1,2,3 -> wait 2,4,8)
        sleeps = [c.args[0] for c in fake_sleep.call_args_list]
        assert sleeps == [2, 4, 8], f"backoff esperado [2,4,8], got {sleeps}"
        # Callback de retry emitido entre tentativas
        retry_events = [e for e in events if e.get("event") == "model_download_retry"]
        assert len(retry_events) == 3, f"esperado 3 retry events, got {len(retry_events)}"
    print(f"PASS: retry com 3 falhas + sucesso (4 chamadas, sleeps {sleeps})")


def test_retry_gives_up_after_5_failures() -> None:
    """5 falhas seguidas: levanta ultima excecao para UI mostrar erro fatal."""
    rev = "b" * 40
    body = b"x" * 1024
    events: list[dict] = []
    cb = lambda d: events.append(dict(d))
    with tempfile.TemporaryDirectory() as tmp:
        session, fail_get = _make_session(rev, body, fail_count=10)  # falha sempre
        with patch("requests.Session", return_value=session), \
             patch("transcribe_pipeline.model_manager.time.sleep"):
            try:
                _manual_snapshot_download(
                    repo_id="fakeorg/fakemodel",
                    revision=rev,
                    cache_dir=Path(tmp),
                    token=None,
                    label="TestModel",
                    start_pct=0,
                    end_pct=100,
                    estimated_bytes=len(body),
                    progress_callback=cb,
                    should_cancel=None,
                )
                raise AssertionError("Esperava ConnectionError, nao levantou")
            except requests.exceptions.ConnectionError as exc:
                assert "Simulated failure 5" in str(exc)
        # 5 tentativas (limite), nada alem
        assert fail_get._call_count == 5, f"esperado 5, got {fail_get._call_count}"
    print("PASS: retry desiste apos 5 falhas e re-raise ConnectionError")


def test_no_retry_when_first_attempt_succeeds() -> None:
    """0 falhas: nao deve haver retry events nem sleep de backoff."""
    rev = "c" * 40
    body = b"y" * 512
    events: list[dict] = []
    cb = lambda d: events.append(dict(d))
    with tempfile.TemporaryDirectory() as tmp:
        session, fail_get = _make_session(rev, body, fail_count=0)
        with patch("requests.Session", return_value=session), \
             patch("transcribe_pipeline.model_manager.time.sleep") as fake_sleep:
            snap = _manual_snapshot_download(
                repo_id="fakeorg/fakemodel",
                revision=rev,
                cache_dir=Path(tmp),
                token=None,
                label="TestModel",
                start_pct=0,
                end_pct=100,
                estimated_bytes=len(body),
                progress_callback=cb,
                should_cancel=None,
            )
        assert snap.exists()
        assert fail_get._call_count == 1, f"esperado 1, got {fail_get._call_count}"
        assert fake_sleep.call_count == 0, "nao devia ter chamado sleep no caminho feliz"
        retry_events = [e for e in events if e.get("event") == "model_download_retry"]
        assert len(retry_events) == 0
    print("PASS: caminho feliz sem retry events nem sleep")


if __name__ == "__main__":
    test_retry_succeeds_after_3_failures()
    test_retry_gives_up_after_5_failures()
    test_no_retry_when_first_attempt_succeeds()
    print("\nPASS: toy_manual_snapshot_retry")
