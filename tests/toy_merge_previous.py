"""Toy: juntar com o bloco ANTERIOR — 2026-09-04.

So existia juntar com o proximo. O contorno (subir um bloco e juntar para a
frente) troca o bloco aberto, perde o cursor e o ponto de reproducao — o
relato de campo foi exatamente esse. `merge_turn_with_previous` nao inventa
fusao nova: e o mesmo caminho ja testado, aplicado ao bloco de cima.

Puro: so `review_store`. Sem Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store  # noqa: E402


def review() -> dict:
    return {
        "transcript": {
            "turns": [
                {"id": "t1", "speaker": "Entrevistador", "start": 0.0, "end": 2.0,
                 "text": "Como foi", "flags": [], "notes": ""},
                {"id": "t2", "speaker": "Entrevistada", "start": 2.0, "end": 4.0,
                 "text": "Eu cheguei aqui", "flags": ["duvida"], "notes": "conferir"},
                {"id": "t3", "speaker": "Entrevistada", "start": 4.0, "end": 6.5,
                 "text": "em 1998.", "flags": [], "notes": ""},
            ],
        },
        "edits": [],
    }


# --- o caso do relato: a frase comecou no bloco de cima ---
r = review()
sobrou = review_store.merge_turn_with_previous(r, "t3")
turns = review_store.review_turns(r)
assert sobrou == "t2", f"o bloco que sobra e o de CIMA, nao o aberto: {sobrou}"
assert len(turns) == 2
assert turns[1]["text"] == "Eu cheguei aqui em 1998."
assert turns[1]["start"] == 2.0 and turns[1]["end"] == 6.5, "a faixa de tempo cobre os dois"
assert turns[1]["flags"] == ["duvida"], "marcacoes dos dois se somam"
assert turns[1]["edited"] is True
print("PASS: junta com o de cima e o de cima e que sobra")

# --- identidade com o caminho antigo: juntar t3 para tras == juntar t2 para frente ---
a, b = review(), review()
review_store.merge_turn_with_previous(a, "t3")
review_store.merge_turn_with_next(b, "t2")
assert review_store.review_turns(a) == review_store.review_turns(b), \
    "para tras tem de ser exatamente o mesmo caminho ja testado"
print("PASS: mesma fusao, nenhuma logica nova")

# --- primeiro bloco: recusa com o motivo em portugues ---
r = review()
try:
    review_store.merge_turn_with_previous(r, "t1")
except ValueError as exc:
    assert "primeiro bloco" in str(exc), str(exc)
    assert "fundir" not in str(exc).lower(), "vocabulario proibido na UI"
else:
    raise AssertionError("juntar o primeiro bloco com o anterior tem de recusar")
assert len(review_store.review_turns(r)) == 3, "a recusa nao pode mexer nos blocos"
print("PASS: primeiro bloco recusa e nao altera nada")

# --- falantes diferentes: recusa e devolve a lista intacta ---
r = review()
try:
    review_store.merge_turn_with_previous(r, "t2")   # Entrevistada apos Entrevistador
except ValueError as exc:
    assert "falantes diferentes" in str(exc), str(exc)
else:
    raise AssertionError("falantes diferentes tem de recusar")
turns = review_store.review_turns(r)
assert len(turns) == 3 and [t["id"] for t in turns] == ["t1", "t2", "t3"], \
    "a recusa nao pode deixar o bloco fora de lugar"
print("PASS: falantes diferentes recusa sem estragar a ordem")

print("PASS: toy_merge_previous")
