"""Toy: dividir um bloco nao pode DOBRAR as marcacoes — 2026-09-05.

`split_turn` monta o bloco novo com `deepcopy(current)`, entao `flags` e
`notes` eram copiadas junto: dividir um bloco marcado pela verificacao acustica
criava DOIS blocos marcados, com a mesma nota, e o contador da faixa 🔍 subia em
vez de descer. Isso aparece toda vez, porque o usuario divide justamente os
blocos que a verificacao marcou.

Regra nova:
- a nota de FRONTEIRA (a que diz que as vozes dos dois lados "parecem iguais")
  acompanha o pedaco da DIREITA, que e quem passa a fazer fronteira com o bloco
  seguinte — a suspeita sempre foi sobre aquela emenda;
- todo o resto (inaudivel, sobreposicao, duvida posta a mao, notas humanas)
  fica no pedaco da esquerda, e a direita nasce limpa.

Puro: so `review_store`. Sem Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store  # noqa: E402
from transcribe_pipeline.boundary_check import BOUNDARY_NOTE_MARKER  # noqa: E402

TEXTO = "Primeira metade da fala. Segunda metade da fala."
CORTE = TEXTO.index("Segunda")

NOTA_DE_FRONTEIRA = (f"A voz deste bloco e a do seguinte {BOUNDARY_NOTE_MARKER} "
                     "(semelhanca 62%) - a divisao entre eles pode estar errada.")


def review(flags: list[str], notes: str) -> dict:
    return {
        "transcript": {"turns": [
            {"id": "t1", "speaker": "SPEAKER_00", "human_label": "Entrevistador",
             "start": 0.0, "end": 10.0, "text": TEXTO,
             "flags": list(flags), "notes": notes},
            {"id": "t2", "speaker": "SPEAKER_01", "human_label": "Entrevistado",
             "start": 10.0, "end": 14.0, "text": "Resposta.", "flags": [], "notes": ""},
        ]},
        "edits": [],
    }


def dividir(flags: list[str], notes: str) -> tuple[dict, dict]:
    r = review(flags, notes)
    novo_id = review_store.split_turn(r, "t1", split_char=CORTE)
    turns = review_store.review_turns(r)
    assert turns[1]["id"] == novo_id
    return turns[0], turns[1]


# --- a marca da verificacao acustica MIGRA para a direita --------------------
esquerda, direita = dividir(["duvida"], NOTA_DE_FRONTEIRA)
assert "duvida" not in esquerda["flags"], f"a esquerda nao faz mais aquela fronteira: {esquerda}"
assert esquerda["notes"] == "", esquerda["notes"]
assert direita["flags"] == ["duvida"], direita["flags"]
assert direita["notes"] == NOTA_DE_FRONTEIRA
print("PASS: a marca de fronteira acompanha o pedaco da direita")

# --- e o total de blocos marcados NAO cresce --------------------------------
antes = 1
depois = sum(1 for t in (esquerda, direita)
             if "duvida" in t["flags"] and BOUNDARY_NOTE_MARKER in str(t["notes"]))
assert depois == antes, f"a faixa 🔍 contaria {depois} onde havia {antes}"
print("PASS: dividir nao dobra a contagem da faixa")

# --- marcacoes de CONTEUDO ficam onde estavam -------------------------------
esquerda, direita = dividir(["inaudivel"], "trecho abafado")
assert esquerda["flags"] == ["inaudivel"] and esquerda["notes"] == "trecho abafado"
assert direita["flags"] == [] and direita["notes"] == ""
print("PASS: marcacoes de conteudo ficam na esquerda; a direita nasce limpa")

# --- duvida posta A MAO (sem nota de fronteira) nao migra --------------------
esquerda, direita = dividir(["duvida"], "conferir o nome citado aqui")
assert esquerda["flags"] == ["duvida"] and "conferir" in esquerda["notes"]
assert direita["flags"] == [] and direita["notes"] == ""
print("PASS: duvida humana nao e confundida com a da verificacao acustica")

# --- as duas coisas ao mesmo tempo ------------------------------------------
esquerda, direita = dividir(["duvida", "inaudivel"], NOTA_DE_FRONTEIRA)
assert esquerda["flags"] == ["inaudivel"], esquerda["flags"]
assert direita["flags"] == ["duvida"], direita["flags"]
print("PASS: so a duvida de fronteira migra; o resto fica")

# --- bloco sem marcacao nenhuma ---------------------------------------------
esquerda, direita = dividir([], "")
assert esquerda["flags"] == [] and direita["flags"] == []
assert esquerda["notes"] == "" and direita["notes"] == ""
print("PASS: bloco limpo continua limpo dos dois lados")

# --- o resto de split_turn nao mudou ----------------------------------------
r = review([], "")
review_store.split_turn(r, "t1", split_char=CORTE, split_time=4.0)
turns = review_store.review_turns(r)
assert turns[0]["text"] == "Primeira metade da fala."
assert turns[1]["text"] == "Segunda metade da fala."
assert turns[0]["end"] == 4.0 and turns[1]["start"] == 4.0, "sem buraco entre os dois"
assert turns[0]["start"] == 0.0 and turns[1]["end"] == 10.0, "as pontas nao se movem"
assert turns[1]["human_label"] == "Entrevistador", "o falante continua sendo herdado"
assert turns[0]["edited"] is True and turns[1]["edited"] is True
assert [e["action"] for e in r["edits"]] == ["split"], r["edits"]
print("PASS: texto, tempos, falante e registro de edicao intactos")

print("PASS: toy_dividir_marcacoes")
