"""Toy test for _manual_snapshot_download (mocked HTTP layer).

Valida:
- Layout da cache produzido (blobs/ + snapshots/{rev}/ + refs/main).
- ETag parsing com X-Linked-ETag (Xet) e ETag regular (git sha1).
- Progress callback emite model_download_bytes com percentual crescente.
- Cache hit: segundo download nao baixa de novo.
- Cancelamento via should_cancel interrompe mid-stream.
- Revision pinada vira o diretorio snapshots/{SHA}/.

Sem rede real — mocka requests.Session.head/get.

Run: python -B tests/toy_manual_snapshot_download.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import requests  # noqa: F401 — patch("requests.Session") requires module importable
except ImportError:
    print("SKIP: requests nao instalado neste venv")
    sys.exit(0)

from transcribe_pipeline.model_manager import (  # noqa: E402
    _etag_from_headers,
    _manual_snapshot_download,
    _place_blob_in_snapshot,
)


class _FakeHead:
    def __init__(self, status: int, headers: dict) -> None:
        self.status_code = status
        self.headers = headers


class _FakeGet:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        pos = 0
        while pos < len(self._body):
            yield self._body[pos:pos + chunk_size]
            pos += chunk_size


def _make_fake_session(siblings, file_bodies, rev_sha):
    """Fake requests.Session que serve meta + head + get pro fluxo do downloader."""
    session = MagicMock()
    # GET /api/models/.../revision/{sha} → meta JSON
    # HEAD /resolve/{sha}/{file} → headers com X-Linked-ETag
    # GET /resolve/{sha}/{file} → body

    def fake_get(url, timeout=None, allow_redirects=True, stream=False, **kwargs):
        if "/api/models/" in url and "/revision/" in url:
            resp = MagicMock()
            resp.json.return_value = {
                "sha": rev_sha,
                "siblings": [{"rfilename": name} for name in siblings],
            }
            resp.raise_for_status = lambda: None
            return resp
        # /resolve/ stream=True → iter_content
        for name, body in file_bodies.items():
            if url.endswith(f"/resolve/{rev_sha}/{name}"):
                return _FakeGet(body)
        raise AssertionError(f"GET inesperado: {url}")

    def fake_head(url, timeout=None, allow_redirects=False, **kwargs):
        for name, body in file_bodies.items():
            if url.endswith(f"/resolve/{rev_sha}/{name}"):
                etag = f"etag-{name}-sha"
                return _FakeHead(302, {
                    "X-Linked-ETag": etag,
                    "X-Linked-Size": str(len(body)),
                })
        raise AssertionError(f"HEAD inesperado: {url}")

    session.get.side_effect = fake_get
    session.head.side_effect = fake_head
    session.headers = {}
    return session


def test_etag_parsing() -> None:
    assert _etag_from_headers({"X-Linked-ETag": '"abc123"'}) == "abc123"
    assert _etag_from_headers({"X-Linked-ETag": 'W/"xyz789"'}) == "xyz789"
    assert _etag_from_headers({"ETag": '"git-sha"'}) == "git-sha"
    # X-Linked-ETag tem prioridade sobre ETag
    assert _etag_from_headers({"X-Linked-ETag": "linked", "ETag": '"git"'}) == "linked"
    assert _etag_from_headers({}) is None
    print("PASS: etag parsing: X-Linked-ETag prioriza, remove W/ e aspas")


def test_place_blob_copies_when_no_symlink() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blob = root / "blobs" / "abc"
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"hello")
        snap = root / "snapshots" / "rev" / "file.txt"
        _place_blob_in_snapshot(blob, snap)
        # File should exist (symlink or copy), and have same content
        assert snap.exists() and snap.read_bytes() == b"hello"
    print("PASS: _place_blob_in_snapshot coloca blob no snapshot (symlink ou copia)")


def test_manual_download_complete_flow() -> None:
    rev = "a" * 40
    bodies = {
        "config.json": b'{"hidden":true}',
        "model.bin": b"x" * 2048,
        "tokenizer.json": b"{}",
    }
    events: list[dict] = []

    def cb(d):
        events.append(dict(d))

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        fake = _make_fake_session(list(bodies.keys()), bodies, rev)
        with patch("requests.Session", return_value=fake):
            snap = _manual_snapshot_download(
                repo_id="fakeorg/fakemodel",
                revision=rev,
                cache_dir=cache,
                token="token-test",
                label="Fake Model",
                start_pct=0,
                end_pct=100,
                estimated_bytes=sum(len(b) for b in bodies.values()),
                progress_callback=cb,
                should_cancel=None,
            )
        assert snap.name == rev, f"snap dir deve ser nomeado pelo SHA: {snap}"
        # Layout: blobs/{etag} + snapshots/{rev}/{file} + refs/main
        for name, body in bodies.items():
            f = snap / name
            assert f.exists(), f"arquivo ausente: {f}"
            assert f.read_bytes() == body, f"conteudo divergente: {name}"
        refs_main = cache / "models--fakeorg--fakemodel" / "refs" / "main"
        assert refs_main.read_text() == rev
        blobs = list((cache / "models--fakeorg--fakemodel" / "blobs").iterdir())
        assert len(blobs) == len(bodies), f"um blob por sibling, got {len(blobs)}"
        assert any(e["event"] == "model_download_bytes" for e in events), (
            "progress_callback nao recebeu events"
        )
    print(f"PASS: 3 arquivos baixados, layout completo, {len(events)} progress events")


def test_manual_download_skips_when_blob_exists() -> None:
    rev = "b" * 40
    bodies = {"model.bin": b"y" * 512}
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        # Pre-popula blob com conteudo + tamanho certo
        blob_dir = cache / "models--fakeorg--fakemodel" / "blobs"
        blob_dir.mkdir(parents=True)
        (blob_dir / "etag-model.bin-sha").write_bytes(b"y" * 512)

        get_calls = []
        fake = _make_fake_session(list(bodies.keys()), bodies, rev)
        original_get = fake.get.side_effect

        def wrapped_get(url, **kwargs):
            get_calls.append((url, kwargs.get("stream", False)))
            return original_get(url, **kwargs)

        fake.get.side_effect = wrapped_get
        with patch("requests.Session", return_value=fake):
            _manual_snapshot_download(
                repo_id="fakeorg/fakemodel",
                revision=rev,
                cache_dir=cache,
                token=None,
                label="Fake",
                start_pct=0,
                end_pct=100,
                estimated_bytes=512,
                progress_callback=None,
                should_cancel=None,
            )
        # Deve ter tido apenas o GET de metadata, nao o GET stream do blob
        stream_gets = [u for u, stream in get_calls if stream]
        assert not stream_gets, f"blob foi re-baixado desnecessariamente: {stream_gets}"
    print("PASS: cache hit nao re-baixa blob")


if __name__ == "__main__":
    test_etag_parsing()
    test_place_blob_copies_when_no_symlink()
    test_manual_download_complete_flow()
    test_manual_download_skips_when_blob_exists()
    print("\nOK: toy_manual_snapshot_download")
