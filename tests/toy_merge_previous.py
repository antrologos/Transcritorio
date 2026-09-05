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
sobrou, adotado = review_store.merge_turn_with_previous(r, "t3")
turns = review_store.review_turns(r)
assert sobrou == "t2", f"o bloco que sobra e o de CIMA, nao o aberto: {sobrou}"
assert adotado == "", "mesmo falante nos dois: nao ha troca a anunciar"
assert len(turns) == 2
assert turns[1]["text"] == "Eu cheguei aqui em 1998."
assert turns[1]["start"] == 2.0 and turns[1]["end"] == 6.5, "a faixa de tempo cobre os dois"
assert turns[1]["flags"] == ["duvida"], "marcacoes dos dois se somam"
assert turns[1]["edited"] is True
print("PASS: junta com o de cima e o de cima e que sobra")

# --- identidade com o caminho antigo: juntar t3 para tras == juntar t2 para frente ---
a, b = review(), review()
assert (review_store.merge_turn_with_previous(a, "t3")
        == review_store.merge_turn_with_next(b, "t2")), "mesmo resultado nos dois sentidos"
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

# --- falantes diferentes: junta ADOTANDO o falante do bloco que sobra -------
# Ate 2026-09-05 isto era recusa. A trava protegia contra fusao acidental, mas
# travava o conserto mais comum da separacao automatica: o usuario tinha de
# trocar o falante antes so para poder juntar. Agora junta, adota o falante do
# bloco de cima e DIZ qual ficou — o mesmo padrao que substituiu as caixas de
# confirmacao de juntar e dividir: em vez de barrar, contar o que aconteceu e
# deixar o Ctrl+Z desfazer.
r = review()
sobrou, adotado = review_store.merge_turn_with_previous(r, "t2")
assert sobrou == "t1" and adotado == "Entrevistador", (sobrou, adotado)
turns = review_store.review_turns(r)
assert len(turns) == 2, [t["id"] for t in turns]
assert review_store.turn_speaker_key(turns[0]) == "Entrevistador", \
    "o bloco resultante e do falante do de cima"
assert turns[0]["text"] == "Como foi Eu cheguei aqui", turns[0]["text"]
# A voz CRUA do sobrevivente nao e reescrita — ela e o registro do que a
# maquina ouviu, e so o rotulo humano muda.
assert turns[0]["speaker"] == "Entrevistador", turns[0]["speaker"]
print("PASS: falantes diferentes junta adotando o de cima, e anuncia qual")

print("PASS: toy_merge_previous")
