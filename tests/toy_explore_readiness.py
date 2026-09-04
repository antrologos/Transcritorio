"""Toy test: janela Perguntar (v3, um so botao) — estado na abertura e puras.

Feedback do teste real (2026-08-30): a janela abria identica com e sem os
modelos — o usuario so descobria o que falta clicando. Regra: o usuario ve
o que roda NESTA maquina antes do clique. v3 (2026-09-03): o botao nunca
fica cinza (os trechos funcionam em qualquer maquina); so a resposta
escrita depende do modelo de analise. Puras: fmt_gb, render_answer_html,
results_footer_text.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

import transcribe_pipeline.review_studio_qt as rs

app = QApplication.instance() or QApplication([])

# --- puras ---
assert rs.fmt_gb(8.7) == "8,7 GB" and rs.fmt_gb(0.5) == "0,5 GB" and rs.fmt_gb(3.0) == "3 GB" and rs.fmt_gb("x") == "? GB"
html = rs.render_answer_html("O pagamento atrasou [1] e o bônus demorou [2]; [9] não existe.\n\nVer [A05R].", 2, ("A05R",))
assert '<a href="trecho:1">[1]</a>' in html and '<a href="trecho:2">[2]</a>' in html
assert "[9]" in html and 'href="trecho:9"' not in html
assert '<a href="entrevista:A05R">[A05R]</a>' in html and "</p><p>" in html
assert "&lt;" in rs.render_answer_html("a <b> b", 0)  # escapa HTML
r = {"hits": [1, 2, 3], "max_results": 20, "considered": 30, "reranked": True}
assert rs.results_footer_text(r) == "3 trechos tratam disso (de até 20). O restante ficou fora por não tratar do tema."
assert rs.results_footer_text({"hits": [1], "max_results": 20, "considered": 1, "reranked": True}) == "1 trecho trata disso (de até 20)."
assert rs.results_footer_text({"hits": [], "reranked": True}) == "Nenhum trecho trata disso de perto."
assert "só pela semelhança" in rs.results_footer_text({"hits": [1], "max_results": 5, "reranked": False})
print("PASS: fmt_gb / render_answer_html / results_footer_text")


class _JanelaPrincipal:
    """Estado de capacidades controlado pelo teste."""

    def __init__(self, estados, aviso=""):
        self._estados = estados  # {key: (estado, motivo, gb)}
        self._aviso = aviso

    def _capability_state(self, key):
        return self._estados[key]

    def _capability_warning(self, key):
        return self._aviso


class _Dialogo:
    _announce_readiness = rs.ExploreDialog._announce_readiness
    _llm_state = rs.ExploreDialog._llm_state

    def __init__(self, estados, aviso=""):
        self._window = _JanelaPrincipal(estados, aviso)
        self.status_label = QLabel("")
        self.state_label = QLabel("")
        self.ask_button = QPushButton("Perguntar")


# --- instalacao essencial em maquina COM GPU: os dois downloads anunciados, virgula ---
dlg = _Dialogo({
    "resumo_perguntar": ("instalavel", "falta baixar", 8.7),
    "busca_semantica": ("instalavel", "falta baixar", 0.5),
})
with patch("transcribe_pipeline.search.encoder_cached", lambda *a, **k: False), \
        patch("transcribe_pipeline.search.reranker_cached", lambda: False):
    dlg._announce_readiness()
texto = dlg.state_label.text()
assert texto.startswith("Nesta máquina:") and "8,7 GB" in texto and "0,5 GB" in texto, texto
assert "falta baixar" in texto and "o clique oferece" in texto
assert dlg.ask_button.isEnabled() is True
print("PASS: abertura anuncia os downloads pendentes (GB com vírgula)")

# --- maquina sem GPU: botao CONTINUA habilitado (trechos funcionam); resposta "nao roda" ---
dlg = _Dialogo({
    "resumo_perguntar": ("incompativel", "precisa de uma placa NVIDIA", 0.0),
    "busca_semantica": ("pronta", "", 0.0),
})
with patch("transcribe_pipeline.search.encoder_cached", lambda *a, **k: True), \
        patch("transcribe_pipeline.search.reranker_cached", lambda: True):
    dlg._announce_readiness()
texto = dlg.state_label.text()
assert dlg.ask_button.isEnabled() is True
assert "NVIDIA" in texto and "não roda neste computador" in texto and "trechos funcionam" in texto, texto
assert "trechos pelo sentido — pronta (com reordenador)" in texto
print("PASS: sem GPU o botao segue vivo e a linha explica o que roda")

# --- tudo pronto, com aviso de VRAM ---
dlg = _Dialogo({
    "resumo_perguntar": ("pronta", "", 0.0),
    "busca_semantica": ("pronta", "", 0.0),
}, aviso="recomenda cerca de 6 GB de memória de vídeo; esta placa tem 4 GB — pode falhar ou ficar lento")
with patch("transcribe_pipeline.search.encoder_cached", lambda *a, **k: True), \
        patch("transcribe_pipeline.search.reranker_cached", lambda: False):
    dlg._announce_readiness()
texto = dlg.state_label.text()
assert "resposta escrita — pronta" in texto and "Atenção" in texto and "conta e risco" in texto, texto
assert "(com reordenador)" not in texto
print("PASS: tudo pronto com aviso de VRAM")

print("PASS: toy_explore_readiness")
