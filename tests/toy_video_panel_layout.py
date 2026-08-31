"""Toy 2026-08-31: painel de video compacto + divisor que funciona.

Teste real do b45: em entrevista de VIDEO o painel de midia engordava
(sobra do layout ia toda para o QVideoWidget) e a tabela de blocos
ficava com ~2 linhas; o setSizes da construcao rodava com o video
oculto e nada redistribuia na transicao.

Secao A: funcao pura media_splitter_sizes (invariantes de soma, pisos
e proporcionalidade). Secao B: transicoes via stub bindado (padrao
toy_open_media_state) — redistribui SO na mudanca de estado; toggle
oculta o video sem tocar o player; residuo some ao fechar.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs
from transcribe_pipeline.review_studio_qt import media_splitter_sizes

app = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------- Secao A
for total, video in [(920, True), (920, False), (700, True), (640, True),
                     (500, True), (1200, True), (2000, False)]:
    partes = media_splitter_sizes(total, video)
    assert sum(partes) == total, (total, video, partes)
    assert all(p >= 0 for p in partes), (total, video, partes)

m, t, e = media_splitter_sizes(920, True)
assert m >= 300 and t >= 180 and e >= 150, (m, t, e)
m, t, e = media_splitter_sizes(920, False)
assert t >= 420, (m, t, e)  # audio: blocos dominam, como o layout atual
m, t, e = media_splitter_sizes(700, True)
assert m == 300 and t >= 180, (m, t, e)  # midia clampa no piso
m, t, e = media_splitter_sizes(640, True)
assert t >= 180 and e >= 150, (m, t, e)
# Janela menor que a soma dos pisos: escala proporcional, soma exata
partes = media_splitter_sizes(500, True)
assert sum(partes) == 500 and all(p > 0 for p in partes), partes
assert media_splitter_sizes(0, True) == [0, 0, 0]
assert media_splitter_sizes(-10, False) == [0, 0, 0]
# Monotonicidade: mais janela nunca da MENOS tabela
assert (media_splitter_sizes(1200, True)[1]
        >= media_splitter_sizes(920, True)[1])
print("PASS: media_splitter_sizes (somas, pisos, proporcional, monotonia)")


# ---------------------------------------------------------------- Secao B
class _Splitter:
    def __init__(self):
        self._sizes = [240, 420, 260]
        self.chamadas: list[list[int]] = []

    def sizes(self):
        return list(self._sizes)

    def setSizes(self, sizes):
        self._sizes = list(sizes)
        self.chamadas.append(list(sizes))


class _Widget:
    def __init__(self):
        self.visivel = False
        self.texto = ""

    def setVisible(self, v):
        self.visivel = bool(v)

    def setText(self, t):
        self.texto = t


class _Stub:
    set_media_source = rs.ReviewStudioWindow.set_media_source
    _sync_video_panel = rs.ReviewStudioWindow._sync_video_panel
    _toggle_video_panel = rs.ReviewStudioWindow._toggle_video_panel
    media_has_video = rs.ReviewStudioWindow.media_has_video

    def __init__(self):
        self.review_splitter = _Splitter()
        self.video_widget = _Widget()
        self.video_toggle_button = _Widget()
        self.player_calls: list[str] = []
        self.player = SimpleNamespace(
            setSource=lambda url: self.player_calls.append("setSource"),
            stop=lambda: self.player_calls.append("stop"),
            pause=lambda: self.player_calls.append("pause"))
        self.media_candidates = []
        self.media_candidate_index = 0
        self._video_user_hidden = False
        self._video_panel_visible = False


# 1) audio -> video: redistribui UMA vez; botao aparece
stub = _Stub()
stub.media_candidates = [Path(r"C:\tmp\e03.mov")]
stub.set_media_source(0)
assert stub.video_widget.visivel is True
assert stub.video_toggle_button.visivel is True
assert stub.video_toggle_button.texto == "Ocultar vídeo"
assert stub.review_splitter.chamadas == [media_splitter_sizes(920, True)], \
    stub.review_splitter.chamadas
print("PASS: audio->video redistribui 1x e mostra o botao")

# 2) video -> video (outro arquivo): NAO redistribui (arrasto respeitado)
stub.review_splitter._sizes = [500, 250, 170]  # usuario arrastou
stub.media_candidates = [Path(r"C:\tmp\outro.mp4")]
stub.set_media_source(0)
assert len(stub.review_splitter.chamadas) == 1, stub.review_splitter.chamadas
print("PASS: video->video nao mexe no arrasto do usuario")

# 3) fechar (sync False): video e botao somem, redistribui p/ audio
stub._sync_video_panel(False)
assert stub.video_widget.visivel is False
assert stub.video_toggle_button.visivel is False
assert stub.review_splitter.chamadas[-1] == media_splitter_sizes(920, False)
print("PASS: fechar esconde video+botao e redistribui")

# 4) toggle: oculta o video, botao SEGUE visivel ("Mostrar vídeo"),
#    e o player nunca e pausado/parado
stub2 = _Stub()
stub2.media_candidates = [Path(r"C:\tmp\e03.mov")]
stub2.set_media_source(0)
stub2.player_calls.clear()
stub2._toggle_video_panel()
assert stub2.video_widget.visivel is False
assert stub2.video_toggle_button.visivel is True
assert stub2.video_toggle_button.texto == "Mostrar vídeo"
assert stub2.player_calls == [], stub2.player_calls
assert stub2.review_splitter.chamadas[-1] == media_splitter_sizes(920, False)
stub2._toggle_video_panel()
assert stub2.video_widget.visivel is True
assert stub2.video_toggle_button.texto == "Ocultar vídeo"
print("PASS: toggle oculta/mostra sem tocar o player")

# 5) preferencia da sessao: oculto pelo usuario vence a midia com video
stub3 = _Stub()
stub3._video_user_hidden = True
stub3.media_candidates = [Path(r"C:\tmp\e03.mov")]
stub3.set_media_source(0)
assert stub3.video_widget.visivel is False
assert stub3.video_toggle_button.visivel is True
assert stub3.video_toggle_button.texto == "Mostrar vídeo"
assert stub3.review_splitter.chamadas == [], stub3.review_splitter.chamadas
print("PASS: preferencia da sessao vence (video fica oculto, botao oferece)")

print("PASS: toy_video_panel_layout")
