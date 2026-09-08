"""Toy: quem entra na análise (2026-09-03) — inventário, sugestão e recortes.

Puro: `speakers.py` (inventário de falantes e sugestão de quem conduz),
`search.passage_scope_text` (o texto do trecho restrito aos falantes
escolhidos — o que vai para o encoder) e `coding.contiguous_ranges`
(as faixas de turnos que o código pinta dentro de um trecho misto).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import coding, search, speakers  # noqa: E402

# --- fold / looks_like_conductor ---
assert speakers.fold("Entrevistadora") == "entrevistadora"
assert speakers.looks_like_conductor("Entrevistador") is True
assert speakers.looks_like_conductor("ENTREVISTADORA") is True
assert speakers.looks_like_conductor("Moderadora - Ana") is True
assert speakers.looks_like_conductor("Entrevistador 1") is True
assert speakers.looks_like_conductor("Entrevistado") is False, "entrevistado NAO conduz"
assert speakers.looks_like_conductor("Participante 2") is False
assert speakers.looks_like_conductor("SPEAKER_00") is False
assert speakers.looks_like_conductor("") is False
print("PASS: looks_like_conductor")

# --- inventario ---
turns_e1 = [
    {"human_label": "Entrevistador", "text": "como foi?"},
    {"human_label": "Entrevistado", "text": "foi difícil no começo mas depois melhorou"},
    {"human_label": "Entrevistado", "text": "muito difícil"},
]
turns_e2 = [
    {"speaker": "SPEAKER_00", "human_label": "Entrevistador", "text": "e o pagamento?"},
    {"speaker": "SPEAKER_01", "text": "atrasou"},          # sem human_label: vale o speaker
    {"human_label": "  ", "speaker": "", "text": "sem rotulo nenhum"},
]
inv = speakers.speaker_inventory({"E1": turns_e1, "E2": turns_e2})
por_label = {i["label"]: i for i in inv}
assert por_label["Entrevistado"]["turns"] == 2 and por_label["Entrevistado"]["words"] == 9
assert por_label["Entrevistador"]["turns"] == 2 and por_label["Entrevistador"]["interviews"] == 2
assert por_label["SPEAKER_01"]["turns"] == 1
assert "" not in por_label, "turno sem rotulo nao vira falante"
assert inv[0]["label"] == "Entrevistado", "ordenado por quem fala mais"
assert speakers.speaker_inventory({}) == []
print("PASS: speaker_inventory")

# --- sugestao ---
assert speakers.suggest_excluded(["Entrevistador", "Entrevistado"]) == {"Entrevistador"}
assert speakers.suggest_excluded(["Moderadora", "Participante 1", "Participante 2"]) == {"Moderadora"}
assert speakers.suggest_excluded(["SPEAKER_00", "SPEAKER_01"]) == set(), "sem pista, nao sugere nada"
assert speakers.suggest_excluded(["Moderador 1", "Moderador 2"]) == set(), "nunca sugere excluir todos"
assert speakers.suggest_excluded([]) == set()
assert speakers.default_selection(["Entrevistador", "Entrevistado"]) == ["Entrevistado"]
assert speakers.default_selection(["SPEAKER_00", "SPEAKER_01"]) == ["SPEAKER_00", "SPEAKER_01"]
assert speakers.describe_selection(inv, None) == "todos os falantes"
assert "fora: Entrevistador" in speakers.describe_selection(inv, ["Entrevistado", "SPEAKER_01"])
assert speakers.describe_selection(inv, [i["label"] for i in inv]) == "todos os falantes"
assert speakers.describe_selection(inv, []) == "nenhum falante escolhido"
print("PASS: suggest_excluded / default_selection / describe_selection")

# --- passage_scope_text: o que o encoder le ---
turns = [
    {"human_label": "Entrevistador", "text": "e quando você não encontrava ninguém em casa, como fazia?"},
    {"human_label": "Entrevistado", "text": "Marcava pessoa ausente e voltava no dia seguinte."},
    {"human_label": "Entrevistador", "text": "entendi"},
    {"human_label": "Entrevistado", "text": "às vezes o vizinho informava."},
]
passagens = search.build_passages(turns, target_words=100)
assert len(passagens) == 1, passagens
p = passagens[0]
assert p["t_from"] == 0 and p["t_to"] == 3
# sem escolha: o texto do trecho inteiro, igual ao de hoje
assert search.passage_scope_text(turns, p, None) == p["text"]
so_entrevistado = search.passage_scope_text(turns, p, {"Entrevistado"})
assert "Marcava pessoa ausente" in so_entrevistado and "o vizinho informava" in so_entrevistado
assert "como fazia" not in so_entrevistado and "entendi" not in so_entrevistado
assert so_entrevistado.startswith("Entrevistado: ")
# trecho que so tem quem ficou de fora: sem texto para o encoder
so_entrevistador = search.passage_scope_text(turns, p, {"Entrevistador"})
assert "como fazia" in so_entrevistador and "Marcava" not in so_entrevistador
assert search.passage_scope_text(turns, p, {"Ninguém"}) == ""
assert search.passage_scope_text(turns, p, set()) == ""
print("PASS: passage_scope_text")

# --- turno longo partido por sentenca: o recorte respeita c_from/c_to ---
longo = [{"human_label": "Entrevistado", "text": " ".join(f"frase{i} numero {i} aqui." for i in range(60))}]
pedacos = search.build_passages(longo, target_words=100, max_turn_words=60)
assert len(pedacos) > 1, len(pedacos)
segundo = pedacos[1]
assert segundo["t_from"] == segundo["t_to"] == 0 and segundo["c_from"] > 0
recorte = search.passage_scope_text(longo, segundo, {"Entrevistado"})
assert recorte == segundo["text"], (recorte[:60], segundo["text"][:60])
assert search.passage_scope_text(longo, segundo, {"Entrevistador"}) == ""
print("PASS: passage_scope_text em turno longo")

# --- contiguous_ranges: o que o codigo pinta ---
assert coding.contiguous_ranges(turns, 0, 3, None) == [(0, 3)]
assert coding.contiguous_ranges(turns, 0, 3, {"Entrevistado"}) == [(1, 1), (3, 3)]
assert coding.contiguous_ranges(turns, 0, 3, {"Entrevistador"}) == [(0, 0), (2, 2)]
assert coding.contiguous_ranges(turns, 1, 3, {"Entrevistado"}) == [(1, 1), (3, 3)]
assert coding.contiguous_ranges(turns, 0, 3, {"Ninguém"}) == []
# turnos vizinhos do mesmo falante viram UMA faixa
seguidos = [{"human_label": "A", "text": "x"}, {"human_label": "B", "text": "y"},
            {"human_label": "B", "text": "z"}, {"human_label": "A", "text": "w"}]
assert coding.contiguous_ranges(seguidos, 0, 3, {"B"}) == [(1, 2)]
assert coding.contiguous_ranges(seguidos, 0, 3, {"A", "B"}) == [(0, 3)]
# faixa fora dos turnos nao inventa nada
assert coding.contiguous_ranges(turns, 0, 99, {"Entrevistado"}) == [(1, 1), (3, 3)]
assert coding.contiguous_ranges([], 0, 3, {"A"}) == []
print("PASS: contiguous_ranges")

print("PASS: toy_speakers_scope")
