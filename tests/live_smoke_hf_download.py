"""LIVE smoke test: baixa um modelo real da HuggingFace + valida cache-check.

**Este teste bate na rede real**. Propósito: gate empírico antes do rebuild
do .exe pra garantir QUE O FLUXO INTEIRO funciona contra a HF:

1. Download do tiny (~72 MB via Xet Storage) via _manual_snapshot_download
2. Validação via _snapshot_has_weights contra o cache resultante

Sem isto, bugs passam despercebidos:
- 2026-04-22: huggingface_hub 0.36.2 travando em cas-bridge.xethub.hf.co
- 2026-04-23: _snapshot_has_weights não vendo blobs atrás de symlinks em
  frozen bundle PyInstaller no Windows

Watchdog de 120s: se travar, falha explicitamente em vez do runner CI
ficar parado.

Run: python -B tests/live_smoke_hf_download.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.model_manager import (  # noqa: E402
    _manual_snapshot_download,
    _snapshot_has_weights,
    cached_snapshot_path,
)

# Um modelo pequeno, publico, nao-gated, estavel.
REPO_ID = "Systran/faster-whisper-tiny"
REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
EXPECTED_MIN_MODEL_BIN_BYTES = 40 * 1024 * 1024  # 40 MB threshold
TIMEOUT_S = 120.0


def _watchdog(cancel_flag: dict, start: float, timeout: float) -> None:
    while not cancel_flag["done"]:
        if time.monotonic() - start > timeout:
            sys.stderr.write(
                f"[LIVE SMOKE] TIMEOUT: download nao completou em {timeout}s — "
                "possivel regressao do bug Xet. Abortando.\n"
            )
            os._exit(2)
        time.sleep(2)


def test_live_download_tiny() -> None:
    events: list[dict] = []

    def cb(d: dict) -> None:
        events.append(dict(d))

    cancel_flag = {"done": False}
    start = time.monotonic()
    threading.Thread(target=_watchdog, args=(cancel_flag, start, TIMEOUT_S), daemon=True).start()

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        try:
            snap = _manual_snapshot_download(
                repo_id=REPO_ID,
                revision=REVISION,
                cache_dir=cache,
                token=os.environ.get("HF_TOKEN"),
                label="Whisper tiny",
                start_pct=0,
                end_pct=100,
                estimated_bytes=150 * 1024 * 1024,
                progress_callback=cb,
                should_cancel=None,
            )
        finally:
            cancel_flag["done"] = True
        elapsed = time.monotonic() - start

        # 1. Snapshot dir existe e tem o SHA pedido
        assert snap.name == REVISION
        assert snap.exists()

        # 2. model.bin presente e com tamanho razoavel (> 40 MB, < 200 MB)
        model_bin = snap / "model.bin"
        assert model_bin.exists(), f"model.bin ausente: {snap}"
        size = model_bin.stat().st_size
        assert size >= EXPECTED_MIN_MODEL_BIN_BYTES, (
            f"model.bin parece truncado: {size} bytes"
        )
        assert size <= 200 * 1024 * 1024, (
            f"model.bin maior que o esperado pro tiny: {size} bytes"
        )

        # 3. refs/main aponta pro SHA
        refs_main = cache / f"models--{REPO_ID.replace('/', '--')}" / "refs" / "main"
        assert refs_main.exists() and refs_main.read_text().strip() == REVISION

        # 4. progress_callback recebeu events com progresso > 0
        pcts = [e["progress"] for e in events if e["event"] == "model_download_bytes"]
        assert any(p > 0 for p in pcts), f"progresso nunca cresceu: {pcts}"

        # 5. Tempo total razoavel (permite ate 120s pra rodar em CI lento)
        assert elapsed < TIMEOUT_S, f"download levou {elapsed:.0f}s (>{TIMEOUT_S}s)"

        # 6. cached_snapshot_path encontra o snapshot via revision pinada
        resolved_snap = cached_snapshot_path(REPO_ID, cache, revision=REVISION)
        assert resolved_snap == snap, (
            f"cached_snapshot_path divergiu: {resolved_snap} vs {snap}"
        )

        # 7. _snapshot_has_weights retorna True contra o cache real — o gate
        # critico que protege contra o bug do 2026-04-23 (verify false-negative
        # por causa de symlink em frozen PyInstaller Windows)
        assert _snapshot_has_weights(snap) is True, (
            "_snapshot_has_weights retornou False apesar do model.bin de "
            f"{size} bytes estar no cache. Este e O bug — significa que "
            "verify_required_models retornaria failures > 0 e a UI mostraria "
            "'Modelos ausentes ou incompletos' mesmo com download OK."
        )

    print(
        f"PASS: {REPO_ID}@{REVISION[:8]}... baixado em {elapsed:.1f}s, "
        f"model.bin={size / (1024 * 1024):.1f}MB, "
        f"has_weights=OK, {len(events)} progress events"
    )


if __name__ == "__main__":
    try:
        test_live_download_tiny()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    print("\nOK: live_smoke_hf_download — download real de HF completou")
