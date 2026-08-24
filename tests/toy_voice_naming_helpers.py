"""Toy test para os helpers puros do dialogo "Quem e esta voz?" (D2.1/D2.4).

Cobre: unlabeled_speaker_ids, ordered_speaker_keys, speaker_sample_ranges,
speaker_talk_summary e a heuristica order_role_suggestions (que so ORDENA
sugestoes — nunca rotula sozinha).

Importa review_studio_qt (helpers ficam fora do bloco Qt, mas o import do
modulo arrasta app_service -> numpy etc.); skip condicional no CI minimo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import (
        order_role_suggestions,
        ordered_speaker_keys,
        speaker_sample_ranges,
        speaker_talk_summary,
        unlabeled_speaker_ids,
    )
except ImportError as exc:  # CI minimo
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def turn(speaker: str, start: float, end: float, text: str, human: str = "") -> dict:
    return {"speaker": speaker, "human_label": human, "start": start, "end": end, "text": text}


# Entrevista tipica: SPEAKER_01 (entrevistador) fala pouco e pergunta muito;
# SPEAKER_00 (entrevistado) fala muito. Ordem numerica != papel.
turns = [
    turn("SPEAKER_01", 0.0, 3.0, "Boa tarde. Pode se apresentar?"),
    turn("SPEAKER_00", 3.0, 40.0, "Claro. Eu nasci em..."),
    turn("SPEAKER_01", 40.0, 42.0, "E depois disso?"),
    turn("SPEAKER_00", 42.0, 90.0, "Depois eu me mudei e trabalhei muitos anos."),
    turn("SPEAKER_00", 90.0, 95.0, "Foi isso."),
]

assert unlabeled_speaker_ids(turns) == ["SPEAKER_00", "SPEAKER_01"]
assert ordered_speaker_keys(turns) == ["SPEAKER_00", "SPEAKER_01"]
print("PASS: ids ordenados por indice numerico")

# Rotulado deixa de ser 'unlabeled'
labeled = [turn("SPEAKER_00", 0, 5, "oi", human="Maria"), turn("SPEAKER_01", 5, 9, "ola")]
assert unlabeled_speaker_ids(labeled) == ["SPEAKER_01"]
print("PASS: voz com nome humano sai da lista de pendentes")

# Amostras: turnos mais longos primeiro, cortados em max_seconds
samples = speaker_sample_ranges(turns, "SPEAKER_00", count=2, max_seconds=8.0)
assert samples == [(42.0, 50.0), (3.0, 11.0)], samples
print("PASS: amostras = turnos mais longos, cortados em 8s")

seconds, blocks = speaker_talk_summary(turns, "SPEAKER_00")
assert blocks == 3 and abs(seconds - 90.0) < 1e-9, (seconds, blocks)
print("PASS: resumo de fala por voz")

# Heuristica de papel: quem pergunta mais e fala menos -> Entrevistador primeiro
suggestions = order_role_suggestions(turns, ["SPEAKER_00", "SPEAKER_01"])
assert suggestions["SPEAKER_01"][0] == "Entrevistador", suggestions
assert suggestions["SPEAKER_00"][0] == "Entrevistado", suggestions
print("PASS: heuristica sugere Entrevistador para quem pergunta e fala menos")

# Grupo focal (4 vozes): moderador sugerido para quem conduz; demais participantes
focus = [
    turn("SPEAKER_00", 0, 4, "Bem-vindos. Quem quer comecar? O que acham do tema?"),
    turn("SPEAKER_01", 4, 60, "Eu acho que..."),
    turn("SPEAKER_02", 60, 130, "Na minha experiencia..."),
    turn("SPEAKER_03", 130, 190, "Concordo em parte..."),
    turn("SPEAKER_00", 190, 193, "E voce, o que pensa?"),
]
ids = unlabeled_speaker_ids(focus)
assert ids == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "SPEAKER_03"]
suggestions = order_role_suggestions(focus, ids)
assert suggestions["SPEAKER_00"][0] == "Moderador", suggestions
others = [suggestions[key][0] for key in ids[1:]]
assert others == ["Participante 1", "Participante 2", "Participante 3"], others
assert suggestions["SPEAKER_01"][1] == "Moderador"
print("PASS: grupo focal sugere Moderador/Participante N")

# Sem vozes -> dict vazio, sem crash
assert order_role_suggestions([], []) == {}
print("PASS: lista vazia nao quebra")

# key_fn CRU (fix do stress-test D2.5): com turnos JA rotulados pelo render
# (o caso N=2 real), os helpers por key humana achariam ZERO turnos ao buscar
# SPEAKER_00 — por chave crua tudo funciona.
from transcribe_pipeline.review_studio_qt import dominant_speaker_key, raw_speaker_key, raw_voice_ids

labeled_turns = [
    turn("SPEAKER_01", 0.0, 3.0, "Boa tarde. Pode se apresentar?", human="Entrevistador"),
    turn("SPEAKER_00", 3.0, 40.0, "Claro. Eu nasci em...", human="Entrevistado"),
    turn("SPEAKER_01", 40.0, 42.0, "E depois disso?", human="Entrevistador"),
    turn("SPEAKER_00", 42.0, 90.0, "Depois eu me mudei...", human="Entrevistado"),
]
assert unlabeled_speaker_ids(labeled_turns) == []  # rotulo default mascara — o gatilho novo nao depende disto
assert raw_voice_ids(labeled_turns) == ["SPEAKER_00", "SPEAKER_01"]
assert speaker_sample_ranges(labeled_turns, "SPEAKER_00") == []  # por key humana: zero (o bug)
raw_samples = speaker_sample_ranges(labeled_turns, "SPEAKER_00", key_fn=raw_speaker_key)
assert raw_samples and raw_samples[0] == (42.0, 50.0), raw_samples
seconds, blocks = speaker_talk_summary(labeled_turns, "SPEAKER_01", key_fn=raw_speaker_key)
assert blocks == 2 and abs(seconds - 5.0) < 1e-9, (seconds, blocks)
raw_suggestions = order_role_suggestions(labeled_turns, ["SPEAKER_00", "SPEAKER_01"], key_fn=raw_speaker_key)
assert raw_suggestions["SPEAKER_01"][0] == "Entrevistador", raw_suggestions
assert raw_suggestions["SPEAKER_00"][0] == "Entrevistado", raw_suggestions
assert dominant_speaker_key(labeled_turns, "SPEAKER_00") == "ENTREVISTADO"
print("PASS: helpers por chave crua funcionam com turnos rotulados (caso N=2 real)")

print()
print("PASS: toy_voice_naming_helpers")
