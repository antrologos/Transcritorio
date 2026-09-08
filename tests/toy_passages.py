"""Toy: passagens da busca por sentido v3 (2026-09-03) — puro, sem modelos.

A unidade da busca deixou de ser "turno + pedacos dos vizinhos" (o vetor
era de uma coisa e a lista mostrava outra: "Autorizo." como "muito
proximo") e passou a ser a PASSAGEM: turnos contiguos ate ~100 palavras,
com o rotulo de quem fala, sobreposicao de 1 turno e turno longo partido
por sentenca.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.search import (  # noqa: E402
    build_passages,
    collapse_overlapping,
    passage_overlaps,
)


def turn(i: int, text: str, label: str = "Entrevistado") -> dict:
    return {"start": float(i * 10), "end": float(i * 10 + 9), "human_label": label, "text": text}


# --- fronteiras de turno, rotulos, sobreposicao de 1 turno ---
turns = [turn(i, " ".join(f"p{i}w{k}" for k in range(30)), "Entrevistador" if i % 2 == 0 else "Entrevistado")
         for i in range(10)]  # 10 turnos de 30 palavras
ps = build_passages(turns, target_words=100)
assert ps, "sem passagens"
# enche ate ~100 sem estourar: 3 turnos de 30 = 90; o 4o faria 120
assert all(p["words"] <= 100 for p in ps), [p["words"] for p in ps]
assert ps[0]["t_from"] == 0 and ps[0]["t_to"] == 2 and ps[0]["words"] == 90, ps[0]
assert ps[0]["text"].startswith("Entrevistador: p0w0") and "Entrevistado: p1w0" in ps[0]["text"]
assert ps[0]["start"] == 0.0 and ps[0]["end"] == 29.0
# a 2a passagem repete o ultimo turno da 1a (sobreposicao de 1 turno)
assert ps[1]["t_from"] == ps[0]["t_to"], (ps[0], ps[1])
# todas as passagens comecam em fronteira de turno e cobrem todos os turnos
assert all(p["c_from"] == 0 for p in ps)
covered = set()
for p in ps:
    covered |= set(range(p["t_from"], p["t_to"] + 1))
assert covered == set(range(10)), covered
assert [p["p"] for p in ps] == list(range(len(ps)))
assert len(ps) == 5 and [(p["t_from"], p["t_to"]) for p in ps] == [(0, 2), (2, 4), (4, 6), (6, 8), (8, 9)], [(p["t_from"], p["t_to"]) for p in ps]
print(f"PASS: passagens por fronteira de turno com sobreposicao ({len(ps)} passagens)")

# --- turnos curtos entram (veto ao corte por tamanho) mas nunca sozinhos ---
curtos = [turn(0, "Você autoriza que essa entrevista seja gravada?", "Entrevistador"),
          turn(1, "Autorizo."), turn(2, "Tá bom,"), turn(3, "Perfeito."),
          turn(4, " ".join(["palavra"] * 120))]
ps = build_passages(curtos, target_words=100)
assert ps[0]["t_from"] == 0 and ps[0]["t_to"] == 4, ps[0]
assert "Entrevistado: Autorizo." in ps[0]["text"] and "Entrevistador: Você autoriza" in ps[0]["text"]
print("PASS: fragmentos curtos vivem dentro da passagem, nao sozinhos")

# --- turno longo e partido por sentenca em pedacos de ~target ---
frases = " ".join(f"Frase numero {k} com algumas palavras dentro dela." for k in range(60))  # ~480 palavras
longo = [turn(0, "Pergunta curta?", "Entrevistador"), turn(1, frases), turn(2, "Fim.", "Entrevistador")]
ps = build_passages(longo, target_words=100, max_turn_words=160)
partes = [p for p in ps if p["t_from"] == 1 and p["t_to"] == 1]
assert len(partes) >= 4, [p["words"] for p in ps]
assert all(p["words"] <= 110 for p in partes), [p["words"] for p in partes]
# offsets dentro do turno reconstroem o texto do pedaco
texto = " ".join(frases.split())
for p in partes:
    assert texto[p["c_from"]:p["c_to"]].strip().startswith("Frase numero"), (p["c_from"], p["c_to"])
    assert p["text"].startswith("Entrevistado: Frase") or p["text"].startswith("Frase"), p["text"][:40]
# o pedaco nunca corta no meio de uma sentenca
assert all(texto[p["c_to"] - 1] == "." for p in partes), [texto[p["c_to"] - 1] for p in partes]
print("PASS: turno longo partido por sentenca")

# --- turnos vazios ignorados; lista vazia -> nenhuma passagem ---
assert build_passages([]) == []
assert build_passages([turn(0, "   "), turn(1, "so este")])[0]["t_from"] == 1
print("PASS: vazios ignorados")

# --- sobreposicao e colapso no ranking ---
a = {"interview_id": "A", "t_from": 0, "t_to": 3, "similarity": 0.9}
b = {"interview_id": "A", "t_from": 3, "t_to": 6, "similarity": 0.8}   # compartilha o turno 3
c = {"interview_id": "A", "t_from": 7, "t_to": 9, "similarity": 0.7}
d = {"interview_id": "B", "t_from": 0, "t_to": 3, "similarity": 0.6}   # outra entrevista
assert passage_overlaps(a, b) and not passage_overlaps(a, c) and not passage_overlaps(a, d)
assert [(h["interview_id"], h["t_from"]) for h in collapse_overlapping([a, b, c, d])] == [("A", 0), ("A", 7), ("B", 0)]
print("PASS: collapse_overlapping")

print("PASS: toy_passages")
