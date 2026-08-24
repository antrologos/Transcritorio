"""Toy test para review_store.apply_label_to_raw_speaker (plano D2.5, item 7).

Relabel por voz CRUA (SPEAKER_NN) restrito a key dominante: imune a rotulos
nao-injetivos (duas vozes com o mesmo rotulo default) e preserva turnos que o
usuario ja reatribuiu a mao. Sem dependencias pesadas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store


def make_review(turns: list[dict]) -> dict:
    for index, turn in enumerate(turns, start=1):
        turn.setdefault("id", f"turn_{index:06d}")
        turn.setdefault("start", 0.0)
        turn.setdefault("end", 1.0)
        turn.setdefault("text", "x")
    return {"schema_version": 1, "transcript": {"turns": turns}, "edits": []}


# Nao-injetividade: duas vozes cruas com o MESMO rotulo default "Participante".
# Aplicar o nome da voz 00 NAO pode tocar a voz 01.
review = make_review([
    {"speaker": "SPEAKER_00", "human_label": "Participante"},
    {"speaker": "SPEAKER_01", "human_label": "Participante"},
    {"speaker": "SPEAKER_00", "human_label": "Participante"},
])
changed = review_store.apply_label_to_raw_speaker(review, "SPEAKER_00", "Maria", "PARTICIPANTE")
turns = review_store.review_turns(review)
assert changed == 2, changed
assert turns[0]["human_label"] == "Maria" and turns[2]["human_label"] == "Maria"
assert turns[1]["human_label"] == "Participante", "voz 01 intacta apesar do rotulo igual"
print("PASS: rotulos duplicados nao colapsam (aplicacao por voz crua)")

# Reatribuicao manual preservada: um turno da voz 00 foi passado a mao para
# "Joana" (key divergente da dominante) — o relabel em lote nao pode toca-lo.
review = make_review([
    {"speaker": "SPEAKER_00", "human_label": "Entrevistado"},
    {"speaker": "SPEAKER_00", "human_label": "Joana"},
    {"speaker": "SPEAKER_00", "human_label": "Entrevistado"},
])
changed = review_store.apply_label_to_raw_speaker(review, "SPEAKER_00", "Carlos", "ENTREVISTADO")
turns = review_store.review_turns(review)
assert changed == 2, changed
assert turns[1]["human_label"] == "Joana", "reatribuicao manual intacta"
assert turns[0]["human_label"] == "Carlos" and turns[2]["human_label"] == "Carlos"
print("PASS: turno reatribuido a mao fica fora do relabel")

# Duas vozes recebendo o MESMO nome e legitimo (fusao de voz super-segmentada)
review = make_review([
    {"speaker": "SPEAKER_00", "human_label": ""},
    {"speaker": "SPEAKER_01", "human_label": ""},
])
assert review_store.apply_label_to_raw_speaker(review, "SPEAKER_00", "Ana", "SPEAKER_00") == 1
assert review_store.apply_label_to_raw_speaker(review, "SPEAKER_01", "Ana", "SPEAKER_01") == 1
turns = review_store.review_turns(review)
assert turns[0]["human_label"] == turns[1]["human_label"] == "Ana"
print("PASS: mesmo nome em duas vozes (fusao) funciona")

# Trilha de edicao + nome vazio rejeitado
assert review["edits"] and review["edits"][-1]["action"] == "set_speaker_all"
try:
    review_store.apply_label_to_raw_speaker(review, "SPEAKER_00", "  ", "ANA")
    raise AssertionError("esperava ValueError")
except ValueError:
    pass
print("PASS: record_edit + nome vazio rejeitado")

print()
print("PASS: toy_apply_label_raw_speaker")
