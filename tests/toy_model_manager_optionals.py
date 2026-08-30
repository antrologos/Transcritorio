"""Toy test: gerenciador de modelos lista os OPCIONAIS (SL-D). Offscreen.

Bug original: os modelos de IA (Qwen 8,7 GB, GLiNER, encoder de busca)
nao apareciam no ModelManagerDialog — uma vez baixados ficavam
invisiveis e irremoviveis; e nao havia como escolher/baixar modelos por
item ("cliquei para instalar mais modelos... nao consegui escolher").
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import model_manager, runtime
from transcribe_pipeline.review_studio_qt import ModelManagerDialog

app = QApplication.instance() or QApplication([])

QWEN = model_manager.optional_model("llm_qwen")
GLINER = model_manager.optional_model("ner_gliner")
ENCODER = model_manager.optional_model("search_encoder")


def _linha(dialog, rotulo):
    for r in range(dialog.table.rowCount()):
        item = dialog.table.item(r, dialog.COL_NAME)
        if item is not None and item.text() == rotulo:
            return r
    return None


def _abre(cache: Path):
    with patch.object(runtime, "model_cache_dir", lambda: cache):
        return ModelManagerDialog(lambda: None)


with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)

    # --- sem GPU: Qwen INCOMPATIVEL sem botao; GLiNER/encoder com Baixar ---
    os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "cpu"
    dlg = _abre(cache)
    r = _linha(dlg, QWEN.label)
    assert r is not None, "Qwen nao aparece no gerenciador"
    assert dlg.table.item(r, dlg.COL_STATUS).text() == "Incompativel"
    assert "NVIDIA" in dlg.table.item(r, dlg.COL_STATUS).toolTip()
    assert dlg.table.cellWidget(r, dlg.COL_ACTION) is None
    assert "8.7" in dlg.table.item(r, dlg.COL_SIZE).text()
    r = _linha(dlg, GLINER.label)
    assert dlg.table.item(r, dlg.COL_STATUS).text() == "Disponivel"
    botao = dlg.table.cellWidget(r, dlg.COL_ACTION)
    assert botao is not None and botao.text() == "Baixar"
    print("PASS: opcionais listados com estado por maquina (cpu)")

    # --- gpu8: Qwen vira Disponivel com Baixar ---
    os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu8"
    dlg = _abre(cache)
    r = _linha(dlg, QWEN.label)
    assert dlg.table.item(r, dlg.COL_STATUS).text() == "Disponivel"
    botao = dlg.table.cellWidget(r, dlg.COL_ACTION)
    assert botao is not None and botao.text() == "Baixar"
    print("PASS: gpu8 libera o Qwen para download")

    # --- instalado (estrutura HF fake no cache): Instalado com Remover ---
    repo_dir = cache / ("models--" + ENCODER.repo_id.replace("/", "--"))
    (repo_dir / "snapshots" / "abc123").mkdir(parents=True)
    (repo_dir / "refs").mkdir()
    (repo_dir / "refs" / "main").write_text("abc123", encoding="utf-8")
    (repo_dir / "snapshots" / "abc123" / "peso.bin").write_bytes(b"x" * 2048)
    dlg = _abre(cache)
    r = _linha(dlg, ENCODER.label)
    assert dlg.table.item(r, dlg.COL_STATUS).text() == "Instalado"
    botao = dlg.table.cellWidget(r, dlg.COL_ACTION)
    assert botao is not None and botao.text() == "Remover"
    print("PASS: opcional instalado pode ser removido")

    # --- variante Whisper NAO instalada tambem ganha Baixar por item ---
    r = _linha(dlg, model_manager.friendly_name("tiny"))
    assert r is not None
    assert dlg.table.item(r, dlg.COL_STATUS).text() == "Disponivel"
    botao = dlg.table.cellWidget(r, dlg.COL_ACTION)
    assert botao is not None and botao.text() == "Baixar"
    print("PASS: variante Whisper disponivel tem botao de baixar")

del os.environ["TRANSCRITORIO_FAKE_HARDWARE"]

# --- ModelSetupDialog com tudo em cache: "tudo pronto", OK desabilitado ---
from types import SimpleNamespace

from PySide6.QtWidgets import QDialogButtonBox, QTextEdit

from transcribe_pipeline.review_studio_qt import ModelSetupDialog

_tudo_cached = [SimpleNamespace(cached=True, asset=SimpleNamespace(gated=True))]
with patch.object(model_manager, "status", lambda **k: list(_tudo_cached)):
    dlg = ModelSetupDialog(asr_variants=["tiny"], include_diarization=True,
                           include_alignment=True)
assert dlg._nada_pendente is True
assert dlg._needs_token is False          # nada pendente = nada gated pendente
ok_btn = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
assert ok_btn.isEnabled() is False, "Baixar modelos ativo sem nada a baixar"
textos = " ".join(w.toPlainText() for w in dlg.findChildren(QTextEdit))
assert "não há nada para baixar" in textos, textos
assert "Gerenciar modelos" in textos, textos
print("PASS: ModelSetupDialog com tudo pronto desabilita o Baixar")

print("PASS: toy_model_manager_optionals")
