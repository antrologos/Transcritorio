"""Toy test: pagina de perfis do assistente (etapa 3), offscreen.

Roda com ambiente REDIRECIONADO (TRANSCRITORIO_HOME/MODEL_CACHE em pasta
temporaria) e maquina SIMULADA (TRANSCRITORIO_FAKE_HARDWARE) — nao toca a
instalacao real nem exige hardware especifico.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_tmp = tempfile.mkdtemp(prefix="wizard_perfil_")
os.environ["TRANSCRITORIO_HOME"] = _tmp
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp) / "models")
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "cpu"

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_settings, capabilities as caps, model_manager
from transcribe_pipeline.review_studio_qt import FirstRunWizard

app = QApplication.instance() or QApplication([])

# --- parse_fake_hardware (pura) ---
assert caps.parse_fake_hardware("cpu").has_gpu is False
assert caps.parse_fake_hardware("gpu24").vram_gb == 24.0
assert caps.parse_fake_hardware("gpu2").vram_gb == 2.0
assert caps.parse_fake_hardware("minimo").ram_gb == 4.0
assert caps.parse_fake_hardware("") is None and caps.parse_fake_hardware(None) is None
assert caps.parse_fake_hardware("gpuXYZ") is None
assert caps.hardware_snapshot().has_gpu is False   # o env "cpu" esta valendo
print("PASS: maquina simulada")

# --- get_required_models respeita o perfil ---
chaves = {a.key for a in model_manager.get_required_models(["tiny"])}
assert "alignment_pt" in chaves and "diarization" in chaves
chaves = {a.key for a in model_manager.get_required_models(
    ["tiny"], include_diarization=False, include_alignment=False)}
assert chaves == {"asr_tiny"}, chaves
print("PASS: get_required_models por perfil")

# --- recomendacao de MODELO acompanha a maquina (pura) ---
assert caps.recommended_asr_variant(caps.parse_fake_hardware("gpu24")) == "large-v3-turbo"
assert caps.recommended_asr_variant(caps.parse_fake_hardware("cpu")) == "small"
assert caps.recommended_asr_variant(caps.parse_fake_hardware("minimo")) == "base"
assert caps.recommended_asr_variant(caps.parse_fake_hardware("gpu2")) == "small"
print("PASS: recommended_asr_variant")

# --- assistente em maquina de CPU: recomenda Padrao, marca sem impor ---
wizard = FirstRunWizard()
assert wizard.selected_profile == "padrao", wizard.selected_profile
assert wizard._profile_radios["padrao"].isChecked()
assert "recomendado" in wizard._profile_radios["padrao"].text()
assert "recomendado" not in wizard._profile_radios["completo"].text()
assert wizard.wants_diarization is True
# aviso de lentidao em CPU aparece ja no perfil recomendado
assert "Sem placa de vídeo" in wizard._profile_warning.text()

# escolher Essencial: sem falantes, fluxo pula conta/termos/token
wizard._profile_radios["essencial"].setChecked(True)
assert wizard.selected_profile == "essencial"
assert wizard.wants_diarization is False
wizard.restart()
assert wizard.currentId() == FirstRunWizard.PAGE_WELCOME
wizard.next()
assert wizard.currentId() == FirstRunWizard.PAGE_PROFILE
wizard.next()
assert wizard.currentId() == FirstRunWizard.PAGE_MODEL_SELECT, wizard.currentId()

# escolher Completo NUMA MAQUINA SEM GPU: permitido, mas com aviso claro
wizard._profile_radios["completo"].setChecked(True)
assert wizard.selected_profile == "completo"
assert "NVIDIA" in wizard._profile_warning.text()
assert "mesmo assim" in wizard._profile_warning.text()
wizard.restart(); wizard.next(); wizard.next()
assert wizard.currentId() == FirstRunWizard.PAGE_ACCOUNT   # completo inclui falantes

# tamanho na pagina de modelos: essencial nao soma extras
wizard._profile_radios["essencial"].setChecked(True)
select_page = wizard.page(FirstRunWizard.PAGE_MODEL_SELECT)
assert select_page.FIXED_GB == 0.0
wizard._profile_radios["padrao"].setChecked(True)
assert select_page.FIXED_GB > 1.0

# em CPU o modelo pre-marcado e o small, NAO o turbo — e so ele
marcados = select_page.selected_asr_variants()
assert marcados == ["small"], marcados
assert "Recomendado para esta máquina" in select_page._checkboxes["small"].text()
assert "Recomendado" not in select_page._checkboxes["large-v3-turbo"].text()
assert wizard.selected_asr_variants == ["small"]
# marcar um segundo modelo NAO desmarca o primeiro, e o rotulo enumera
select_page._checkboxes["tiny"].setChecked(True)
assert set(select_page.selected_asr_variants()) == {"small", "tiny"}
assert "2 modelos" in select_page.total_label.text()
select_page._checkboxes["tiny"].setChecked(False)
assert "Será baixado" in select_page.total_label.text()

# a pagina de perfis declara com qual modelo estimou os tamanhos
pagina_perfil = wizard.page(FirstRunWizard.PAGE_PROFILE)
rotulos = [w.text() for w in pagina_perfil.findChildren(type(wizard._profile_warning))]
assert any("estimativas com o modelo" in t for t in rotulos), rotulos
assert any("small" in t for t in rotulos), "a estimativa nao cita o modelo recomendado"

# pagina de download tem o botao de cancelar (oculto ate comecar)
download_page = wizard.page(FirstRunWizard.PAGE_DOWNLOAD)
assert hasattr(download_page, "cancel_download_button")
assert download_page.cancel_download_button.isVisible() is False
print("PASS: fluxo e avisos do assistente (maquina de CPU)")

# --- persistencia do perfil ---
wizard._profile_radios["essencial"].setChecked(True)
wizard.done(1)  # Accepted
assert app_settings.install_profile() == "essencial"
assert app_settings.alignment_default() is False
assert app_settings.diarize_default() is False
print("PASS: perfil persistido por maquina")

# --- maquina com GPU boa: recomenda Completo, sem avisos ---
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu24"
wizard2 = FirstRunWizard()
assert wizard2.selected_profile == "completo"
assert wizard2._profile_warning.text() == ""
# GPU pequena: nao recomenda completo, e completo avisa sobre a VRAM
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu2"
wizard3 = FirstRunWizard()
assert wizard3.selected_profile == "padrao"
wizard3._profile_radios["completo"].setChecked(True)
assert "memória de vídeo" in wizard3._profile_warning.text()
print("PASS: recomendacao por maquina (gpu24 e gpu2)")

print("PASS: toy_wizard_profile")
