"""Toy test: dialogo "Transcrever novamente..." (SL-C2), offscreen.

So modelos INSTALADOS entram no combo; o atual vem pre-selecionado; o
aviso de recriacao (backup em edits/backups) esta no proprio dialogo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import RetranscribeDialog

app = QApplication.instance() or QApplication([])

# atual pre-selecionado; so instalados no combo
dlg = RetranscribeDialog("E01", installed=["tiny", "small"], current="small")
assert dlg.selected_model() == "small", dlg.selected_model()
assert dlg.model_combo.count() == 2, dlg.model_combo.count()
chaves = {dlg.model_combo.itemData(i) for i in range(dlg.model_combo.count())}
assert chaves == {"tiny", "small"}, chaves

# trocar a escolha muda o retorno
dlg.model_combo.setCurrentIndex(0 if dlg.model_combo.itemData(0) != "small" else 1)
assert dlg.selected_model() != "small"

# aviso honesto presente (recriacao + backup)
textos = " ".join(w.text() for w in dlg.findChildren(QLabel))
assert "DO ZERO" in textos and "backups" in textos, textos
assert "Gerenciar modelos" in textos, "nota de onde baixar outros modelos sumiu"

# nenhum instalado (cache exotico): cai para o atual, sem combo vazio
dlg2 = RetranscribeDialog("E02", installed=[], current="tiny")
assert dlg2.model_combo.count() == 1
assert dlg2.selected_model() == "tiny"

# --- ensure_models_ready valida o modelo da RODADA, nao o configurado ---
from unittest.mock import patch

import transcribe_pipeline.review_studio_qt as rs


class _JanelaGate:
    ensure_models_ready = rs.ReviewStudioWindow.ensure_models_ready

    def ensure_ffmpeg(self):
        return True

    def _configured_asr_variants(self):
        return ["configurado"]

    def _configured_diarize(self):
        return False


recebidos: list[list[str]] = []


def _fake_ready(variants, include_diarization, include_alignment, align_languages=None):
    recebidos.append(list(variants))
    return True


with patch.object(rs.app_service, "required_models_ready", _fake_ready):
    janela = _JanelaGate()
    assert janela.ensure_models_ready() is True
    assert janela.ensure_models_ready(asr_variants=["small"]) is True
assert recebidos == [["configurado"], ["small"]], recebidos
print("PASS: gate valida o modelo escolhido na rodada")

print("PASS: toy_retranscribe_dialog")
