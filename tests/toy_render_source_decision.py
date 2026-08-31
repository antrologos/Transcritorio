"""Toy test: fonte de falantes do render decidida POR arquivo (SL-B1).

Bug original: os fluxos de render/rotulos/nomes (inclusive a acao
"Atualizar transcricao editavel", removida na R3/R4) forcavam
overrides={"diarization_source": "pyannote_exclusive"} — no perfil
essencial (sem diarizacao) o exclusive.json nao existe e o render
falhava. A decisao correta ja existia no fluxo principal:
canais informativos > pyannote_exclusive > modo sem falantes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import ReviewStudioWindow

app = QApplication.instance() or QApplication([])


class _Janela:
    _render_source_overrides = ReviewStudioWindow._render_source_overrides
    _exclusive_diarization_exists = ReviewStudioWindow._exclusive_diarization_exists
    _channels_diarization_exists = ReviewStudioWindow._channels_diarization_exists

    def __init__(self, diar_dir: Path):
        self.context = SimpleNamespace(paths=SimpleNamespace(diarization_dir=diar_dir))


with tempfile.TemporaryDirectory() as tmp:
    diar = Path(tmp) / "03_diarization"
    (diar / "json").mkdir(parents=True)
    janela = _Janela(diar)

    # 1. Perfil essencial: nada de diarizacao -> render sem falantes
    assert janela._render_source_overrides("E01") == {}, "sem diarizacao deve remontar sem falantes"

    # 2. So exclusive.json -> pyannote_exclusive
    (diar / "json" / "E01.exclusive.json").write_text("{}", encoding="utf-8")
    assert janela._render_source_overrides("E01") == {"diarization_source": "pyannote_exclusive"}

    # 3. channels.json informativo vence o exclusive
    (diar / "json" / "E01.channels.json").write_text(
        json.dumps({"decision": "informative"}), encoding="utf-8")
    assert janela._render_source_overrides("E01") == {"diarization_source": "channels"}

    # 4. channels.json NAO-informativo (canais identicos) -> volta ao exclusive
    (diar / "json" / "E01.channels.json").write_text(
        json.dumps({"decision": "identical"}), encoding="utf-8")
    assert janela._render_source_overrides("E01") == {"diarization_source": "pyannote_exclusive"}

    # 5. A decisao e por ARQUIVO: outro id continua sem falantes
    assert janela._render_source_overrides("E02") == {}

print("PASS: toy_render_source_decision")
