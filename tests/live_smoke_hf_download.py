"""LIVE smoke test: baixa um modelo real da HuggingFace via _manual_snapshot_download.

**Este teste bate na rede real**. Propósito: gate empírico antes do rebuild
do .exe pra garantir que o fluxo de download de fato funciona contra a HF
(Xet Storage incluso). Sem este teste, bugs como o do 2026-04-22 (huggingface_hub
0.36.2 travando em cas-bridge.xethub.hf.co) voltam a passar despercebidos.

Modelo testado: Systran/faster-whisper-tiny (150 MB — suficiente pra validar
fluxo de LFS/Xet sem fazer o CI levar 40 min). SHA pinada.

Watchdog de 120s: se travar, o teste falha explicitamente em vez de fazer o
runner do CI ficar 6 horas parado.

Run: python -B tests/live_smoke_hf_download.py
"""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transcribe_pipeline.model_manager import _manual_snapshot_download  # noqa: E402

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

    print(
        f"PASS: {REPO_ID}@{REVISION[:8]}... baixado em {elapsed:.1f}s, "
        f"model.bin={size / (1024 * 1024):.1f}MB, {len(events)} progress events"
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
