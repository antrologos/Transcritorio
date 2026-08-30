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
# 2026-08-30: "minimo" tambem recomenda small — o base virou demonstracao
# (qualidade insuficiente para trabalho) e saiu do assistente.
assert caps.recommended_asr_variant(caps.parse_fake_hardware("minimo")) == "small"
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
# tiny/base sao DEMONSTRACAO (decisao 2026-08-30): fora do assistente
assert "tiny" not in select_page._checkboxes, "tiny voltou ao assistente"
assert "base" not in select_page._checkboxes, "base voltou ao assistente"
# marcar um segundo modelo NAO desmarca o primeiro, e o rotulo enumera
select_page._checkboxes["medium"].setChecked(True)
assert set(select_page.selected_asr_variants()) == {"small", "medium"}
assert "2 modelos" in select_page.total_label.text()
select_page._checkboxes["medium"].setChecked(False)
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

# --- persistencia do perfil e do MODELO escolhido ---
wizard._profile_radios["essencial"].setChecked(True)
wizard.done(1)  # Accepted
assert app_settings.install_profile() == "essencial"
assert app_settings.alignment_default() is False
assert app_settings.diarize_default() is False
# o modelo escolhido vira default da maquina (projetos novos herdam)
assert app_settings.asr_model_default() == "small", app_settings.asr_model_default()
print("PASS: perfil persistido por maquina")

# --- ModelSetupDialog: token so quando ha modelo restrito pendente ---
from transcribe_pipeline.review_studio_qt import ModelSetupDialog, NewProjectDialog

sem_gated = ModelSetupDialog(asr_variants=["tiny"], include_diarization=False,
                             include_alignment=False)
assert sem_gated._needs_token is False
assert sem_gated.token_edit.isVisible() is False
com_gated = ModelSetupDialog(asr_variants=["tiny"], include_diarization=True,
                             include_alignment=False)
assert com_gated._needs_token is True   # pyannote pendente no cache virgem
print("PASS: ModelSetupDialog exige token so com modelo restrito")

# --- NewProjectDialog: preview honesto e trava de colisao ---
import tempfile as _tf
with _tf.TemporaryDirectory() as _base:
    dlg = NewProjectDialog(initial_dir=_base)
    assert dlg._ok_button.isEnabled() is False           # sem nome, sem criar
    dlg.name_edit.setText("Meu Estudo")
    assert dlg._ok_button.isEnabled() is True
    assert str(dlg.project_root()).endswith("Meu Estudo.transcricao")
    assert ".transcricao" in dlg.preview_label.text()
    (Path(_base) / "Meu Estudo.transcricao").mkdir()     # colisao
    dlg._update_preview()                                # re-avalia o destino
    assert dlg._ok_button.isEnabled() is False
    assert "Já existe" in dlg.preview_label.text()
print("PASS: NewProjectDialog preview e colisao")

# --- Completo pergunta: baixar os modelos de IA agora ou depois (SL-B2) ---
from transcribe_pipeline.review_studio_qt import _wizard_optional_keys

CPU_HW = caps.parse_fake_hardware("cpu")
GPU8_HW = caps.parse_fake_hardware("gpu8")
# pura: perfil != completo nao baixa opcional nenhum
assert _wizard_optional_keys("padrao", GPU8_HW, set()) == ()
assert _wizard_optional_keys("essencial", CPU_HW, set()) == ()
# gpu8: os tres modelos de IA entram
assert _wizard_optional_keys("completo", GPU8_HW, set()) == (
    "search_encoder", "ner_gliner", "llm_qwen")
# cpu: o Qwen (precisa de GPU) fica de fora; os outros dois entram
assert _wizard_optional_keys("completo", CPU_HW, set()) == ("search_encoder", "ner_gliner")
# cache filtra o que ja existe
assert _wizard_optional_keys("completo", GPU8_HW, {"ner_gliner"}) == (
    "search_encoder", "llm_qwen")
print("PASS: _wizard_optional_keys")

# maquina de CPU: escolher Completo mostra a pergunta com "depois" recomendado
wizard_ia = FirstRunWizard()
assert wizard_ia._ai_download_group.isHidden() is True  # padrao recomendado
wizard_ia._profile_radios["completo"].setChecked(True)
assert wizard_ia._ai_download_group.isHidden() is False
assert wizard_ia._ai_later_radio.isChecked()
assert "recomendado" in wizard_ia._ai_later_radio.text()
assert wizard_ia.wants_ai_models_now is False
# nota honesta: o Qwen nao entra sem GPU
assert "NVIDIA" in wizard_ia._ai_blocked_note.text()
wizard_ia._profile_radios["padrao"].setChecked(True)
assert wizard_ia._ai_download_group.isHidden() is True
print("PASS: Completo pergunta (maquina de CPU: depois recomendado)")

# --- pagina de idiomas (E4-2): pt pre-marcado; essencial pula ---
wizard_l = FirstRunWizard()  # cpu -> perfil padrao recomendado
assert len(wizard_l._lang_checkboxes) == 16
assert wizard_l._lang_checkboxes["pt"].isChecked()
assert not wizard_l._lang_checkboxes["en"].isChecked()
assert wizard_l.selected_languages == ("pt",)
wizard_l._lang_checkboxes["en"].setChecked(True)
assert set(wizard_l.selected_languages) == {"en", "pt"}
# fluxo padrao: a pagina de idiomas vem depois da escolha de modelo
wizard_l.restart()
for _ in range(4):  # welcome -> profile -> account -> terms -> model
    wizard_l.next()
assert wizard_l.currentId() == FirstRunWizard.PAGE_MODEL_SELECT
assert wizard_l.nextId() == FirstRunWizard.PAGE_LANGS
wizard_l.next()
assert wizard_l.currentId() == FirstRunWizard.PAGE_LANGS
assert wizard_l.nextId() == FirstRunWizard.PAGE_TOKEN
# essencial (sem alinhamento) pula a pagina de idiomas
wizard_l._profile_radios["essencial"].setChecked(True)
wizard_l.restart()
wizard_l.next(); wizard_l.next()
assert wizard_l.currentId() == FirstRunWizard.PAGE_MODEL_SELECT
assert wizard_l.nextId() == FirstRunWizard.PAGE_DOWNLOAD
print("PASS: pagina de idiomas no fluxo certo")

# um UNICO idioma escolhido vira o default de projetos novos da maquina
wizard_l2 = FirstRunWizard()
wizard_l2._profile_radios["padrao"].setChecked(True)
wizard_l2._lang_checkboxes["pt"].setChecked(False)
wizard_l2._lang_checkboxes["en"].setChecked(True)
assert wizard_l2.selected_languages == ("en",)
wizard_l2.done(1)
assert app_settings.language_default() == "en", app_settings.language_default()
# varios idiomas: mantem o pt como default neutro
wizard_l3 = FirstRunWizard()
wizard_l3._profile_radios["padrao"].setChecked(True)
wizard_l3._lang_checkboxes["en"].setChecked(True)
wizard_l3.done(1)
assert app_settings.language_default() == "pt"
print("PASS: idioma unico vira default da maquina")

# --- maquina com GPU boa: recomenda Completo, sem avisos ---
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu24"
wizard2 = FirstRunWizard()
assert wizard2.selected_profile == "completo"
assert wizard2._profile_warning.text() == ""
# GPU boa + Completo default: pergunta visivel com "agora" recomendado
assert wizard2._ai_download_group.isHidden() is False
assert wizard2._ai_now_radio.isChecked()
assert "recomendado" in wizard2._ai_now_radio.text()
assert wizard2.wants_ai_models_now is True
# escolher "depois" desliga o download imediato
wizard2._ai_later_radio.setChecked(True)
assert wizard2.wants_ai_models_now is False
print("PASS: Completo pergunta (GPU boa: agora recomendado)")
# GPU pequena: nao recomenda completo, e completo avisa sobre a VRAM
os.environ["TRANSCRITORIO_FAKE_HARDWARE"] = "gpu2"
wizard3 = FirstRunWizard()
assert wizard3.selected_profile == "padrao"
wizard3._profile_radios["completo"].setChecked(True)
assert "memória de vídeo" in wizard3._profile_warning.text()
print("PASS: recomendacao por maquina (gpu24 e gpu2)")

print("PASS: toy_wizard_profile")
