"""Toy test: combo Dispositivo honesto + selo Motor clicavel (SL-E).

Bugs originais: o combo oferecia "GPU NVIDIA (CUDA)" mesmo em maquina
sem placa (escolha que so falharia depois), e o selo "Motor: ..." do
cabecalho nao era clicavel — o proprio autor nao achou como alternar
para CPU (o seletor sempre existiu no dialogo do Motor).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline.review_studio_qt import EngineSettingsDialog, ReviewStudioWindow

app = QApplication.instance() or QApplication([])


def _item_cuda(dlg):
    idx = dlg.device_combo.findData("cuda")
    assert idx >= 0
    return dlg.device_combo.model().item(idx)


# --- sem GPU: item CUDA desabilitado com motivo; config "cuda" cai p/ auto.
# NO macOS o comportamento correto e OUTRO: "cuda" e a rota documentada do
# MLX/Metal e nunca e desabilitado (review_studio_qt, device_combo) ---
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "cpu"
dlg = EngineSettingsDialog({"asr_device": "cuda"})
if sys.platform == "darwin":
    assert _item_cuda(dlg).isEnabled() is True
    print("PASS: no macOS o CUDA segue habilitado (rota MLX/Metal)")
else:
    assert _item_cuda(dlg).isEnabled() is False
    assert "NVIDIA" in _item_cuda(dlg).toolTip()
    assert dlg.device_combo.currentData() == "auto", dlg.device_combo.currentData()
    print("PASS: sem GPU o CUDA fica desabilitado com motivo e cai para auto")
# CPU continua escolhivel
assert dlg.device_combo.findData("cpu") >= 0

# --- com GPU: CUDA habilitado e escolha preservada ---
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu8"
dlg2 = EngineSettingsDialog({"asr_device": "cuda"})
assert _item_cuda(dlg2).isEnabled() is True
assert dlg2.device_combo.currentData() == "cuda"
# quem TEM CUDA pode escolher CPU (pedido do usuario 2026-08-30)
dlg2.device_combo.setCurrentIndex(dlg2.device_combo.findData("cpu"))
assert dlg2.updates()["asr_device"] == "cpu"
del os.environ["TRANSCRITORIO_FAKE_HARDWARE"]
print("PASS: com GPU a alternancia CUDA<->CPU funciona")

# --- selo "Motor" do cabecalho e um LINK para engine-settings ---
class _Janela:
    project_header_text = ReviewStudioWindow.project_header_text

    def __init__(self):
        from types import SimpleNamespace
        self.context = SimpleNamespace(
            project={"project_name": "Teste"},
            config={"asr_model": "tiny", "asr_device": "auto"},
            paths=SimpleNamespace(project_root=Path("C:/tmp/teste")),
        )


html = _Janela().project_header_text()
assert 'href="engine-settings"' in html
# o selo Motor precisa estar DENTRO de um <a> (dois links: Modelo e Motor)
assert html.count('href="engine-settings"') >= 2, html
assert "Motor:" in html
print("PASS: selo Motor virou link para a configuracao")

# --- combo de idioma gerado do registro (etapa 4): 16 + Automatico ---
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu8"
dlg3 = EngineSettingsDialog({"asr_language": "nl"})
assert dlg3.language_combo.count() == 17, dlg3.language_combo.count()
assert dlg3.language_combo.currentData() == "nl"
codigos = {dlg3.language_combo.itemData(i) for i in range(dlg3.language_combo.count())}
assert {"pt", "auto", "nl", "ja", "zh"} <= codigos, codigos
# Automatico declara a limitacao; idioma sem pacote instalado anuncia o download
idx_auto = dlg3.language_combo.findData("auto")
assert "sem tempos por palavra" in dlg3.language_combo.itemText(idx_auto)
idx_nl = dlg3.language_combo.findData("nl")
assert "baixa ~1.2 GB" in dlg3.language_combo.itemText(idx_nl), \
    dlg3.language_combo.itemText(idx_nl)
del os.environ["TRANSCRITORIO_FAKE_HARDWARE"]
print("PASS: combo de idioma honesto")

print("PASS: toy_engine_settings_device")
