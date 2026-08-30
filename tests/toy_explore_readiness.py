"""Toy test: janela Perguntar anuncia o estado JA NA ABERTURA (SL-A bis).

Feedback do teste real (2026-08-30, 2a rodada): a janela abria identica
com e sem os modelos instalados — o usuario so descobria o que falta
clicando. Regra do lote: o usuario ve o que falta ANTES do clique.
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


class _JanelaPrincipal:
    """Estado de capacidades controlado pelo teste."""

    def __init__(self, estados):
        self._estados = estados  # {key: (estado, motivo, gb)}

    def _capability_state(self, key):
        return self._estados[key]


class _Dialogo:
    _announce_readiness = rs.ExploreDialog._announce_readiness

    def __init__(self, estados):
        self._window = _JanelaPrincipal(estados)
        self.status_label = QLabel("")
        self.ask_button = QPushButton("Perguntar")


# --- instalacao essencial em maquina COM GPU: os dois avisos aparecem ---
dlg = _Dialogo({
    "resumo_perguntar": ("instalavel", "falta baixar", 8.7),
    "busca_semantica": ("instalavel", "falta baixar", 0.5),
})
with patch("transcribe_pipeline.search.encoder_cached", lambda: False):
    dlg._announce_readiness()
texto = dlg.status_label.text()
assert "8.7" in texto and "download" in texto, texto
assert "0.5" in texto, texto
assert dlg.ask_button.isEnabled() is True  # instalavel: o clique oferece
print("PASS: abertura anuncia os downloads pendentes")

# --- maquina sem GPU: Perguntar desabilitado COM motivo, busca segue ---
dlg = _Dialogo({
    "resumo_perguntar": ("incompativel", "precisa de uma placa NVIDIA", 0.0),
    "busca_semantica": ("instalavel", "falta baixar", 0.5),
})
with patch("transcribe_pipeline.search.encoder_cached", lambda: False):
    dlg._announce_readiness()
assert dlg.ask_button.isEnabled() is False
assert "NVIDIA" in dlg.ask_button.toolTip()
assert "NVIDIA" in dlg.status_label.text()
assert "Encontrar trechos" in dlg.status_label.text()  # o que funciona fica claro
print("PASS: incompativel desabilita o Perguntar com motivo")

# --- tudo pronto: nada de aviso, botao ativo ---
dlg = _Dialogo({
    "resumo_perguntar": ("pronta", "", 0.0),
    "busca_semantica": ("pronta", "", 0.0),
})
with patch("transcribe_pipeline.search.encoder_cached", lambda: True):
    dlg._announce_readiness()
assert dlg.status_label.text() == ""
assert dlg.ask_button.isEnabled() is True
print("PASS: tudo pronto abre limpo")

# --- sonda quebrada nunca derruba a abertura ---
class _JanelaQuebrada:
    def _capability_state(self, key):
        raise RuntimeError("sonda")


dlg = _Dialogo({"resumo_perguntar": ("pronta", "", 0.0),
                "busca_semantica": ("pronta", "", 0.0)})
dlg._window = _JanelaQuebrada()
dlg._announce_readiness()  # nao pode levantar
print("PASS: falha de sonda nao derruba a janela")

print("PASS: toy_explore_readiness")
