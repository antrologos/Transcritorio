"""Toy 2026-08-31: 4 paineis verticais colapsaveis + divisores com curso.

Historia (testes reais b45-b47): o painel de midia engordava com video
e a tabela ficava com ~2 linhas; depois, os minimumSizeHint travavam o
curso dos divisores; por fim o usuario pediu: minimizar CADA secao a
zero e esticar sem teto. O review_splitter virou 4 paineis
independentes (video | audio | blocos | editor), todos colapsaveis.

Secao A: funcao pura media_splitter_sizes (4 slots). Secao B:
transicoes via stub bindado. Secao C: janela real — colapso a zero,
folga dos divisores e pisos cobrindo os minimos reais.
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
for total, video in [(920, True), (920, False), (760, True), (640, True),
                     (500, True), (1200, True), (2000, False)]:
    partes = media_splitter_sizes(total, video)
    assert len(partes) == 4 and sum(partes) == total, (total, video, partes)
    assert all(p >= 0 for p in partes), (total, video, partes)

v, a, t, e = media_splitter_sizes(920, True)
assert v >= 140 and a >= 180 and t >= 180 and e >= 210, (v, a, t, e)
v, a, t, e = media_splitter_sizes(920, False)
assert v == 0 and t >= 400, (v, a, t, e)  # audio: blocos dominam
v, a, t, e = media_splitter_sizes(760, True)
assert t >= 180, (v, a, t, e)  # deficit sai de video/audio, tabela vive
# Janela menor que a soma dos pisos (710 c/ video): proporcional exato
for total in (640, 500):
    partes = media_splitter_sizes(total, True)
    assert sum(partes) == total, partes
assert media_splitter_sizes(0, True) == [0, 0, 0, 0]
assert media_splitter_sizes(-10, False) == [0, 0, 0, 0]
# Monotonicidade: mais janela nunca da MENOS tabela
assert (media_splitter_sizes(1200, True)[2]
        >= media_splitter_sizes(920, True)[2])
print("PASS: media_splitter_sizes 4 slots (somas, pisos, proporcional)")


# ---------------------------------------------------------------- Secao B
class _Splitter:
    def __init__(self):
        self._sizes = [0, 240, 420, 260]
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
stub.review_splitter._sizes = [300, 200, 250, 170]  # usuario arrastou
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

# ------------------------------------------------ Secao C: janela REAL
import csv
import tempfile

import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import (
    DEFAULT_CONFIG, ensure_directories, make_paths, write_config)

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy"])

win = rs.ReviewStudioWindow(project_root=tmp)
win.resize(1440, 900)
win.show()
app.processEvents()

# 4 paineis, todos colapsaveis (minimo ZERO via arrasto/setSizes)
assert win.review_splitter.count() == 4
for i in range(4):
    assert win.review_splitter.isCollapsible(i), f"pane{i} nao colapsavel"
assert win.review_splitter.widget(0) is win.video_widget

# Colapso REAL a zero: blocos ocupam tudo
win.video_widget.setVisible(True)
app.processEvents()
total_v = sum(win.review_splitter.sizes())
win.review_splitter.setSizes([0, 0, total_v, 0])
app.processEvents()
tamanhos = win.review_splitter.sizes()
assert tamanhos[0] == 0 and tamanhos[1] == 0 and tamanhos[3] == 0, tamanhos
assert tamanhos[2] >= total_v - 10, tamanhos
print("PASS: cada painel colapsa a zero (blocos podem ocupar tudo)")

# Pisos da funcao cobrem os minimos reais (senao a distribuicao das
# transicoes nasce clampada)
assert win.review_splitter.widget(1).minimumSizeHint().height() <= 180
assert win.review_splitter.widget(3).minimumSizeHint().height() <= 210
assert win.video_widget.minimumHeight() <= 140

# Horizontal segue com curso (fix do b47)
from PySide6.QtWidgets import QSplitter
h_split = win.interview_table.parent()
while h_split is not None and not isinstance(h_split, QSplitter):
    h_split = h_split.parent()
largura = sum(h_split.sizes())
h_split.setSizes([240, largura - 240])
app.processEvents()
assert h_split.sizes()[0] <= 260, h_split.sizes()
print("PASS: pisos cobrem minimos reais; horizontal com curso")

print("PASS: toy_video_panel_layout")
