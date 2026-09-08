"""Toy: mover a fronteira entre falantes em UM gesto — 2026-09-05.

Quando a separacao automatica erra, um bloco atribuido a A contem fala de A e,
a partir de certo ponto, de B. Consertar isso custava CINCO gestos: clicar no
ponto, dividir, abrir o seletor de falante, escolher B, e juntar com o bloco de
B — e o ultimo so funcionava depois do quarto, porque juntar recusa falantes
diferentes.

O diagnostico: o que esta errado nao e o falante de um bloco, e a FRONTEIRA
entre dois. E o bloco vizinho ja tem o falante certo — entao a operacao nao
precisa perguntar para quem o trecho vai, ela HERDA do vizinho. E por isso que
isto funciona igual em entrevista a dois e em grupo focal.

Puro: so `review_store`. Sem Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store  # noqa: E402

# Bloco do meio contem fala do Entrevistador e, a partir de "Olha,", do
# Entrevistado — o erro tipico da diarizacao.
MISTO = "E como foi? Olha, foi bem difícil no começo."
CORTE = MISTO.index("Olha,")


def review() -> dict:
    return {
        "transcript": {"turns": [
            {"id": "t1", "speaker": "SPEAKER_01", "human_label": "Entrevistado",
             "start": 0.0, "end": 4.0, "text": "Antes disso.", "flags": [], "notes": ""},
            {"id": "t2", "speaker": "SPEAKER_00", "human_label": "Entrevistador",
             "start": 4.0, "end": 12.0, "text": MISTO, "flags": [], "notes": ""},
            {"id": "t3", "speaker": "SPEAKER_01", "human_label": "Entrevistado",
             "start": 12.0, "end": 20.0, "text": "E depois melhorou.", "flags": [], "notes": ""},
        ]},
        "edits": [],
    }


def rotulos(r: dict) -> list[str]:
    return [t["human_label"] for t in review_store.review_turns(r)]


def textos(r: dict) -> list[str]:
    return [t["text"] for t in review_store.review_turns(r)]


# --- o caso principal: o fim do bloco e do falante seguinte ------------------
r = review()
sobrevivente = review_store.move_tail_to_next(r, "t2", split_char=CORTE, split_time=7.0)
turns = review_store.review_turns(r)
assert len(turns) == 3, f"3 blocos: o pedaco movido foi absorvido pelo vizinho: {textos(r)}"
assert textos(r) == ["Antes disso.", "E como foi?",
                     "Olha, foi bem difícil no começo. E depois melhorou."], textos(r)
assert rotulos(r) == ["Entrevistado", "Entrevistador", "Entrevistado"], rotulos(r)
assert turns[1]["end"] == 7.0 and turns[2]["start"] == 7.0, "a fronteira foi para o cursor"
assert turns[2]["end"] == 20.0, "o fim do bloco de destino nao se move"
assert sobrevivente == turns[2]["id"]
print("PASS: o fim do bloco passa para o proximo, herdando o falante dele")

# --- espelho: o comeco do bloco e do falante anterior ------------------------
r = review()
COMECO = "E depois melhorou."
r["transcript"]["turns"][2]["text"] = "que foi difícil. " + COMECO
corte = r["transcript"]["turns"][2]["text"].index("E depois")
sobrevivente = review_store.move_head_to_previous(r, "t3", split_char=corte, split_time=14.0)
turns = review_store.review_turns(r)
assert len(turns) == 3, textos(r)
assert turns[1]["text"].endswith("que foi difícil."), turns[1]["text"]
assert turns[2]["text"] == COMECO
assert turns[1]["human_label"] == "Entrevistador" and turns[2]["human_label"] == "Entrevistado"
assert turns[1]["end"] == 14.0 and turns[2]["start"] == 14.0
assert sobrevivente == turns[1]["id"]
print("PASS: o comeco do bloco passa para o anterior, herdando o falante dele")

# --- cursor na ponta: o bloco INTEIRO migra ---------------------------------
# Caso real: um bloco curto foi atribuido inteiro ao falante errado.
r = review()
review_store.move_tail_to_next(r, "t2", split_char=0)
assert textos(r) == ["Antes disso.", MISTO + " E depois melhorou."], textos(r)
assert rotulos(r) == ["Entrevistado", "Entrevistado"], rotulos(r)
print("PASS: com o cursor na ponta o bloco inteiro passa para o vizinho")

r = review()
review_store.move_head_to_previous(r, "t2", split_char=None)
assert textos(r) == ["Antes disso. " + MISTO, "E depois melhorou."], textos(r)
assert rotulos(r) == ["Entrevistado", "Entrevistado"]
print("PASS: idem no sentido contrario")

# --- sem vizinho: recusa com frase em portugues ------------------------------
for turn_id, funcao, esperado in (
    ("t3", review_store.move_tail_to_next, "último"),
    ("t1", review_store.move_head_to_previous, "primeiro"),
):
    r = review()
    try:
        funcao(r, turn_id, split_char=5)
    except ValueError as exc:
        assert esperado in str(exc), str(exc)
        assert "fundir" not in str(exc).lower(), "vocabulario proibido"
    else:
        raise AssertionError(f"{funcao.__name__} sem vizinho tem de recusar")
    assert len(review_store.review_turns(r)) == 3, "a recusa nao pode mexer nos blocos"
print("PASS: sem bloco vizinho recusa e nao altera nada")

# --- vizinho do mesmo falante: nao ha fronteira para mover -------------------
r = review()
r["transcript"]["turns"][2]["human_label"] = "Entrevistador"   # igual ao t2
try:
    review_store.move_tail_to_next(r, "t2", split_char=CORTE)
except ValueError as exc:
    assert "mesmo falante" in str(exc), str(exc)
else:
    raise AssertionError("vizinho do mesmo falante tem de recusar")
assert textos(r) == ["Antes disso.", MISTO, "E depois melhorou."], "nada mudou"
print("PASS: vizinho do mesmo falante recusa, em vez de dividir e juntar de volta")

# --- um gesto do usuario = um registro de edicao -----------------------------
r = review()
review_store.move_tail_to_next(r, "t2", split_char=CORTE, split_time=7.0)
assert [e["action"] for e in r["edits"]] == ["move_boundary"], r["edits"]
print("PASS: um gesto deixa um registro de edicao, nao quatro")

# --- a soma dos tempos nao muda ---------------------------------------------
r = review()
antes = review_store.review_turns(r)
duracao_antes = max(t["end"] for t in antes) - min(t["start"] for t in antes)
review_store.move_tail_to_next(r, "t2", split_char=CORTE, split_time=7.0)
depois = review_store.review_turns(r)
duracao_depois = max(t["end"] for t in depois) - min(t["start"] for t in depois)
assert abs(duracao_antes - duracao_depois) < 1e-9
inicios = [t["start"] for t in depois]
assert inicios == sorted(inicios), "os blocos continuam em ordem"
for anterior, seguinte in zip(depois, depois[1:]):
    assert abs(anterior["end"] - seguinte["start"]) < 1e-9, "sem buraco nem sobreposicao"
print("PASS: os tempos continuam encostados e cobrindo o mesmo intervalo")

print("PASS: toy_mover_fronteira")
