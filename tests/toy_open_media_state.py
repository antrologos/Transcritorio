"""Toy 2026-08-31: abrir midia SEM transcricao nao pode herdar estado.

Bug do teste real do b44: abrir um arquivo nao transcrito mantinha o
banner de trocas de falante da entrevista aberta ANTES ("3 trocas de
falante com vozes parecidas" numa midia sem nenhum bloco) —
open_media_only zerava review/turns mas nunca chamava a cascata de
atualizacao dos banners (_update_voice_banner -> diar_failed ->
boundary -> refresh da aba corrente), ao contrario do close_open_file.
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
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs

app = QApplication.instance() or QApplication([])


class _Stub:
    open_media_only = rs.ReviewStudioWindow.open_media_only

    def __init__(self):
        self.context = object()
        self.chamadas: list[str] = []
        # Residuo da entrevista anterior — o que vazava para a midia nova.
        self.review = {"turns": ["residuo"]}
        self.turns = [{"flags": ["duvida"], "notes": "residuo"}]
        self.current_interview_id = "ANTERIOR"
        self.current_turn_id = "t9"
        self.current_play_row = 3
        self.word_index = ["w"]
        self._word_uncertain_cutoff = 0.5
        self.media_candidates = []
        self.player = SimpleNamespace(stop=lambda: self.chamadas.append("stop"))
        self.turn_table = SimpleNamespace(setRowCount=lambda n: None)
        self.text_edit = SimpleNamespace(clear=lambda: None)
        self.undo_stack = SimpleNamespace(clear=lambda: None)
        self.review_title = SimpleNamespace(setText=lambda t: None)
        self.progress_label = SimpleNamespace(setText=lambda t: None)

    def set_editor_enabled(self, enabled):
        pass

    def set_media_source(self, index):
        pass

    def load_waveform(self):
        pass

    def set_save_state(self, state):
        pass

    def update_action_states(self):
        pass

    def _update_voice_banner(self):
        self.chamadas.append("banners")


stub = _Stub()
with patch.object(rs.app_service, "get_media_candidates",
                  lambda ctx, iid: [Path("C:/tmp/e03.wav")]):
    stub.open_media_only("E03R_0730")

assert stub.review is None, "review da entrevista anterior vazou"
assert stub.turns == [], "turns da entrevista anterior vazaram"
assert stub.current_interview_id == "E03R_0730"
assert "banners" in stub.chamadas, (
    "open_media_only nao atualizou os banners — o aviso de trocas de "
    "falante da entrevista anterior fica pintado na midia sem transcricao")
print("PASS: abrir midia sem transcricao zera estado E atualiza banners")

print("PASS: toy_open_media_state")
