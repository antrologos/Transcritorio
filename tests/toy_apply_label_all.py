"""Toy test para review_store.apply_label_to_speaker_key (D2.2).

Renomeia em lote todos os turnos de uma voz (por turn_speaker_key) — base do
botao "Aplicar a todos" e do dialogo "Quem e esta voz?". Sem dependencias
pesadas (review_store e stdlib).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store


def make_review() -> dict:
    return {
        "schema_version": 1,
        "review_status": "draft",
        "transcript": {
            "turns": [
                {"id": "turn_000001", "speaker": "SPEAKER_00", "human_label": "", "text": "Boa tarde.", "start": 0.0, "end": 2.0},
                {"id": "turn_000002", "speaker": "SPEAKER_01", "human_label": "", "text": "Boa tarde!", "start": 2.0, "end": 4.0},
                {"id": "turn_000003", "speaker": "SPEAKER_00", "human_label": "", "text": "Como vai?", "start": 4.0, "end": 6.0},
                {"id": "turn_000004", "speaker": "SPEAKER_01", "human_label": "Maria", "text": "Vou bem.", "start": 6.0, "end": 8.0},
            ]
        },
        "edits": [],
    }


review = make_review()
changed = review_store.apply_label_to_speaker_key(review, "SPEAKER_00", "Entrevistador")
turns = review_store.review_turns(review)
assert changed == 2, changed
assert turns[0]["human_label"] == "Entrevistador" and turns[2]["human_label"] == "Entrevistador"
assert turns[1]["human_label"] == "" and turns[3]["human_label"] == "Maria", "outras vozes intactas"
assert turns[0]["edited"] is True
assert review["edits"][-1]["action"] == "set_speaker_all"
print("PASS: renomeia todos os turnos da voz, sem tocar as demais")

# Idempotente: repetir nao muda nada
assert review_store.apply_label_to_speaker_key(review, "SPEAKER_00", "Entrevistador") == 0
print("PASS: segunda aplicacao identica -> 0 mudancas")

# A voz agora atende pelo NOVO nome (key = rotulo humano normalizado)
assert review_store.apply_label_to_speaker_key(review, "ENTREVISTADOR", "Pesquisadora") == 2
assert review_store.review_turns(review)[0]["human_label"] == "Pesquisadora"
print("PASS: renomear de novo usa a key do rotulo atual")

# Voz ja nomeada individualmente ("Maria") tem key propria
assert review_store.apply_label_to_speaker_key(review, "MARIA", "Maria Silva") == 1
print("PASS: voz com nome proprio renomeada pela key do nome")

# Rotulo vazio e erro
try:
    review_store.apply_label_to_speaker_key(review, "SPEAKER_01", "   ")
    raise AssertionError("esperava ValueError para rotulo vazio")
except ValueError:
    print("PASS: rotulo vazio rejeitado")

print()
print("PASS: toy_apply_label_all")
