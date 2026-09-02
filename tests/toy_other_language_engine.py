"""Toy: aviso didatico de idioma com o TAGARELA + caminho para outro modelo.

2026-09-02 (decisao do usuario): o TAGARELA e o padrao em todas as maquinas
e so transcreve portugues. Ao pedir outro idioma o app deve AVISAR e ENSINAR
a escolher outro modelo: nota inline nos seletores de idioma e, ao
Transcrever, uma janela que oferece o Whisper de reserva (com a qualidade de
cada opcao) so para o lote ou para o projeto. Puras + dialogos Qt.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_other_lang_")
os.environ["TRANSCRITORIO_APP_DATA"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import (
        EngineSettingsDialog,
        OtherLanguageEngineDialog,
        engine_language_note,
        whisper_reserve_options,
    )
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

# --- puras ---
assert engine_language_note("parakeet-pt", "es").startswith("⚠")
assert "português" in engine_language_note("parakeet-pt", "en")
assert "Whisper" in engine_language_note("parakeet-pt", "en")
for lang in ("pt", "", None, "auto", "automático", "project"):
    assert engine_language_note("parakeet-pt", lang) == "", lang
assert engine_language_note("large-v3-turbo", "es") == ""
assert engine_language_note("small", "en") == ""
assert engine_language_note("", "es") == ""
print("PASS: engine_language_note")

ops = whisper_reserve_options(["small", "parakeet-pt", "large-v3"], "large-v3-turbo")
assert [k for k, _n, _i in ops] == ["large-v3-turbo", "large-v3", "small"], ops
assert ops[0][2] is False and ops[1][2] is True and ops[2][2] is True      # recomendado nao instalado
assert all(n for _k, n, _i in ops), "toda opcao explica a qualidade"
assert "erra mais" in dict((k, n) for k, n, _ in ops)["small"]
ops = whisper_reserve_options([], "small")
assert ops == [("small", ops[0][1], False)], ops
ops = whisper_reserve_options(["parakeet-pt"], "small")
assert [k for k, _n, _i in ops] == ["small"]
assert whisper_reserve_options(["tiny", "medium"], "medium")[0][0] == "medium"
print("PASS: whisper_reserve_options")

# --- dialogo ao Transcrever ---
app = QApplication.instance() or QApplication([])
d = OtherLanguageEngineDialog(["en", "es"], whisper_reserve_options(["small"], "large-v3-turbo"))
assert d.windowTitle() == "Este lote tem outro idioma"
# idiomas aparecem pelo NOME (Inglês, Espanhol), nao pelo codigo
from PySide6.QtWidgets import QLabel as _QLabel
from transcribe_pipeline import model_manager as _mm_t
_texto = d.findChildren(_QLabel)[0].text()
for _c in ("en", "es"):
    assert str(_mm_t.ALIGN_LANGUAGES[_c]["label"]) in _texto, _texto
assert ": en, es" not in _texto
assert d.model_combo.count() == 2 and d.chosen_model() == "large-v3-turbo"
assert "baixar" in d.model_combo.itemText(0) and "baixar" not in d.model_combo.itemText(1)
assert d.apply_to_project() is False and d.wants_settings is False
d.model_combo.setCurrentIndex(1)
assert d.chosen_model() == "small"
d.project_check.setChecked(True)
assert d.apply_to_project() is True
d.settings_button.click()
assert d.wants_settings is True and d.result() == d.DialogCode.Rejected
vazio = OtherLanguageEngineDialog(["en"], [])
assert vazio.ok_button.isEnabled() is False and vazio.chosen_model() == ""
print("PASS: OtherLanguageEngineDialog")

# --- nota inline no dialogo do motor ---
cfg = {"asr_model": "parakeet-pt", "asr_language": "es", "asr_device": "cpu"}
e = EngineSettingsDialog(cfg)
assert e.engine_lang_note.isVisibleTo(e), "TAGARELA + espanhol devia avisar"
e.language_combo.setCurrentIndex(max(0, e.language_combo.findData("pt")))
assert not e.engine_lang_note.isVisibleTo(e)
e.language_combo.setCurrentIndex(max(0, e.language_combo.findData("auto")))
assert not e.engine_lang_note.isVisibleTo(e), "automático nao bloqueia (= portugues)"
e2 = EngineSettingsDialog({"asr_model": "small", "asr_language": "es", "asr_device": "cpu"})
assert not e2.engine_lang_note.isVisibleTo(e2)
print("PASS: nota inline em Configurar transcrição")

print("PASS: toy_other_language_engine")
