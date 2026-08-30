"""Toy test: ensure_models_ready propaga o escopo e agenda a retomada (F2).

Bugs criticos da checagem geral (2026-08-30): (1) o gate pedia
diarizacao (require_diarization=True) mas show_model_setup recalculava o
escopo SEM ela — "Faltam modelos" -> "nao ha nada para baixar" com botao
morto (beco do Melhorar falantes na instalacao essencial); (2) o
re-teste imediato apos show_model_setup nunca via o download (que e
assincrono) — a acao original nunca seguia; (3) a mensagem dizia que
"os modelos de transcricao e separacao de falantes" faltavam quando so
faltava o pyannote de ~0,1 GB.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs
from transcribe_pipeline import model_manager

app = QApplication.instance() or QApplication([])


class _FakeQMB:
    StandardButton = QMessageBox.StandardButton
    Icon = QMessageBox.Icon
    resposta = QMessageBox.StandardButton.Yes
    perguntas: list[tuple[str, str]] = []

    @staticmethod
    def question(parent, titulo, texto, *a, **k):
        _FakeQMB.perguntas.append((titulo, texto))
        return _FakeQMB.resposta


class _Janela:
    ensure_models_ready = rs.ReviewStudioWindow.ensure_models_ready

    def __init__(self, aceita_download=True):
        self.worker = None
        self.chamadas_setup: list[dict] = []
        self.mensagens: list[str] = []
        self.progress_label = SimpleNamespace(setText=self.mensagens.append)
        self._aceita_download = aceita_download
        self._retry_after_models = None

    def ensure_ffmpeg(self):
        return True

    def _configured_asr_variants(self):
        return ["configurado"]

    def _configured_diarize(self):
        return False  # projeto essencial: diarize desligado

    def _invalidate_capability_cache(self):
        pass

    def show_model_setup(self, asr_variants=None, include_diarization=None,
                         include_alignment=None, align_languages=None):
        self.chamadas_setup.append({
            "asr_variants": asr_variants,
            "include_diarization": include_diarization,
            "include_alignment": include_alignment,
        })
        if self._aceita_download:
            # dialogo aceito: o worker assincrono comeca a rodar
            self.worker = SimpleNamespace(isRunning=lambda: True)


_PENDENTE_DIA = SimpleNamespace(
    cached=False,
    asset=SimpleNamespace(label="Separação de falantes", estimated_gb=0.07, gated=True))


def _roda(janela, **kwargs):
    with patch.object(rs, "QMessageBox", _FakeQMB), \
         patch.object(rs.app_service, "required_models_ready",
                      lambda *a, **k: False), \
         patch.object(model_manager, "has_partial_cache", lambda **k: False), \
         patch.object(model_manager, "status", lambda **k: [_PENDENTE_DIA]), \
         patch("transcribe_pipeline.app_settings.alignment_default", lambda: False):
        return janela.ensure_models_ready(**kwargs)


# --- escopo propagado: require_diarization=True chega ao show_model_setup ---
_FakeQMB.perguntas.clear()
janela = _Janela()
retry_marcado = []
ok = _roda(janela, require_diarization=True, retry=lambda: retry_marcado.append(1))
assert ok is False  # download assincrono iniciado; a acao segue via retomada
assert janela.chamadas_setup == [{
    "asr_variants": ["configurado"],
    "include_diarization": True,      # <- o beco: antes era recalculado False
    "include_alignment": False,
}], janela.chamadas_setup
assert janela._retry_after_models is not None, "retomada nao foi agendada"
janela._retry_after_models()
assert retry_marcado == [1]
print("PASS: escopo propagado e retomada agendada")

# --- mensagem honesta: nomeia o que falta com o tamanho ---
titulo, texto = _FakeQMB.perguntas[0]
assert "Separação de falantes" in texto, texto
assert "0.1" in texto or "0,1" in texto, texto
assert "modelos de transcrição e separação" not in texto, "mensagem falsa voltou"
print("PASS: mensagem lista o que falta de verdade")

# --- variante da rodada tambem propaga ---
janela2 = _Janela()
_roda(janela2, asr_variants=["small"])
assert janela2.chamadas_setup[0]["asr_variants"] == ["small"]
print("PASS: variante da rodada propagada")

# --- usuario cancela o dialogo: sem worker -> aviso claro, retry limpo ---
janela3 = _Janela(aceita_download=False)
ok = _roda(janela3, require_diarization=True, retry=lambda: None)
assert ok is False
assert janela3._retry_after_models is None, "retry ficou armado apos cancelar"
assert any("cancelada" in m for m in janela3.mensagens), janela3.mensagens
print("PASS: cancelar o preparo avisa e desarma a retomada")

# --- usuario responde Nao a pergunta: nada acontece, sem retry ---
_FakeQMB.resposta = QMessageBox.StandardButton.No
janela4 = _Janela()
ok = _roda(janela4, retry=lambda: None)
assert ok is False and not janela4.chamadas_setup
assert janela4._retry_after_models is None
_FakeQMB.resposta = QMessageBox.StandardButton.Yes
print("PASS: recusa nao arma a retomada")

print("PASS: toy_ensure_models_scope")
