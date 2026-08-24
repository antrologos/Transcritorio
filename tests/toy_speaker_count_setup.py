"""Toy test para a Fase D3: "Quantas pessoas falam?" + cores por voz.

Cobre: project_store.default_speaker_labels (Moderador/Participante N para
grupos), ids_without_speaker_setup (marcador speaker_setup — o sync pre-semeia
speaker_mode, entao presenca de modo NAO significa escolha humana),
voice_color_map (cor estavel por voz) e o SpeakerCountDialog offscreen.

Skip condicional no CI minimo (imports pesados transitivos).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.project_store import default_speaker_labels
    from transcribe_pipeline.review_studio_qt import ids_without_speaker_setup, voice_color_map
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

# Rotulos default por contagem
assert default_speaker_labels(2) == ["Entrevistador", "Entrevistado"]
assert default_speaker_labels(4) == ["Moderador", "Participante 1", "Participante 2", "Participante 3"]
assert default_speaker_labels(1) == ["Falante 1"]
print("PASS: default_speaker_labels (2=entrevista, >2=grupo, 1=falante)")

# ids_without_speaker_setup: so o marcador conta, nunca o speaker_mode semeado
metadata = {
    "SEMEADO": {"speaker_mode": "exact", "speaker_count": "2"},        # sync default -> pendente
    "ESCOLHIDO": {"speaker_mode": "range", "speaker_setup": "true"},   # humano configurou
    "VAZIO": {},
}
pending = ids_without_speaker_setup(metadata, ["SEMEADO", "ESCOLHIDO", "VAZIO", "SEM_METADATA"])
assert pending == ["SEMEADO", "VAZIO", "SEM_METADATA"], pending
print("PASS: marcador speaker_setup decide, modo semeado nao")

# Cores estaveis por voz crua
turns = [
    {"speaker": "SPEAKER_01", "human_label": "Entrevistador"},
    {"speaker": "SPEAKER_00", "human_label": ""},
    {"speaker": "SPEAKER_01", "human_label": ""},
]
colors = voice_color_map(turns)
assert set(colors) == {"SPEAKER_00", "SPEAKER_01"}
assert colors["SPEAKER_00"] != colors["SPEAKER_01"]
assert voice_color_map(turns) == colors, "estavel entre chamadas"
print("PASS: voice_color_map estavel e distinta por voz")

# Dialogo offscreen: os 4 modos produzem os updates certos
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import SpeakerCountDialog
except ImportError as exc:
    print(f"SKIP parcial: PySide6 ausente ({exc})")
    print("PASS: toy_speaker_count_setup")
    sys.exit(0)

app = QApplication.instance() or QApplication([])
dialog = SpeakerCountDialog(3)

updates = dialog.updates()  # default: Entrevista
assert updates["speaker_setup"] == "true"
assert updates["speaker_mode"] == "exact" and updates["speaker_count"] == "2"
assert updates["speaker_labels"] == "Entrevistador|Entrevistado"
print("PASS: preset Entrevista (default)")

dialog.group_radio.setChecked(True)
dialog.group_min_spin.setValue(6)
dialog.group_max_spin.setValue(4)  # invertido de proposito
updates = dialog.updates()
assert updates["speaker_mode"] == "range"
assert updates["min_speakers"] == "4" and updates["max_speakers"] == "6"
assert updates["speaker_labels"].startswith("Moderador|Participante 1")
print("PASS: preset Grupo focal (range, min/max corrigidos)")

dialog.exact_radio.setChecked(True)
dialog.exact_spin.setValue(5)
updates = dialog.updates()
assert updates["speaker_mode"] == "exact" and updates["speaker_count"] == "5"
assert updates["speaker_labels"].count("|") == 4
print("PASS: preset Numero exato")

dialog.auto_radio.setChecked(True)
updates = dialog.updates()
assert updates["speaker_mode"] == "auto" and updates["speaker_count"] == ""
assert "speaker_labels" not in updates
assert updates["speaker_setup"] == "true"
print("PASS: preset Automatico (marca setup mesmo sem contagem)")

print()
print("PASS: toy_speaker_count_setup")
