"""Toy test para o fix D1.3: suavizacao do split palavra->falante.

Criterio ACUSTICO aprovado no plano (nunca contagem de palavras):
- Palavra sem overlap com a diarizacao (caiu em gap/silencio) herda o falante
  do grupo ANTERIOR — nao mais o speaker global do segmento ASR.
- Grupo de 1 palavra so e absorvido pelo vizinho quando a evidencia e
  marginal: fracao da duracao da palavra dentro dos turnos do falante
  vencedor < diarization_split_containment (default 0.5).
- Interjeicoes reais de 1 palavra ("Sim") solidamente dentro do turno do
  outro falante SAO PRESERVADAS — restricao levantada pelo usuario em
  2026-08-23 (absorver por tamanho atribuiria a fala a pessoa errada).

Sem dependencias pesadas (render.py + stdlib).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.render import split_segment_by_word_diarization


def w(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "word": text}


def seg(words: list[dict], speaker: str = "SPEAKER_00") -> dict:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "speaker": speaker,
        "text": " ".join(x["word"] for x in words),
        "words": words,
    }


def d(start: float, end: float, speaker: str) -> dict:
    return {"start": start, "end": end, "speaker": speaker}


# (b) "Sim" genuino de 1 palavra, solidamente dentro do turno de B: PRESERVADO
diar = [d(0, 5, "SPEAKER_00"), d(5.1, 5.6, "SPEAKER_01"), d(5.7, 10, "SPEAKER_00")]
words = [w(1, 1.5, "Ola"), w(2, 2.5, "tudo"), w(5.15, 5.45, "Sim"), w(6, 6.5, "entao"), w(7, 7.5, "veja")]
out = split_segment_by_word_diarization(seg(words), diar)
assert len(out) == 3, [(o["speaker"], o["text"]) for o in out]
assert (out[1]["speaker"], out[1]["text"]) == ("SPEAKER_01", "Sim"), out[1]
print("PASS: 'Sim' genuino (containment 1.0) vira turno proprio de B")

# (a) Palavra marginal que cruza fronteiras (mais fora do que dentro do
# vencedor): ABSORVIDA — o split espurio A-B-A desaparece e o segmento nem
# precisa ser dividido (retorna [] e o fallback por segmento decide)
diar = [d(0, 4.8, "SPEAKER_00"), d(5.0, 5.3, "SPEAKER_01"), d(5.6, 10, "SPEAKER_00")]
words = [w(1, 1.5, "a"), w(2, 2.5, "b"), w(4.7, 5.55, "talvez"), w(6, 6.5, "c")]
# "talvez": dur 0.85; overlap A=0.1, B=0.3 -> B vence com containment 0.35 < 0.5
out = split_segment_by_word_diarization(seg(words), diar)
assert out == [], [(o["speaker"], o["text"]) for o in out]
print("PASS: palavra marginal absorvida (sem turno espurio)")

# (c) Palavra em gap de silencio herda o grupo ANTERIOR (nao o speaker do
# segmento ASR) — fronteira do turno fica no lugar certo
diar = [d(0, 3, "SPEAKER_00"), d(4, 10, "SPEAKER_01")]
words = [w(1, 1.5, "a"), w(2, 2.5, "b"), w(3.2, 3.6, "ha"), w(4.5, 5, "c"), w(5.5, 6, "d")]
out = split_segment_by_word_diarization(seg(words, speaker="SPEAKER_01"), diar)
assert len(out) == 2, [(o["speaker"], o["text"]) for o in out]
assert out[0]["speaker"] == "SPEAKER_00" and out[0]["text"].endswith("ha"), out[0]
assert out[1]["text"] == "c d", out[1]
print("PASS: palavra em gap herda o grupo anterior")

# (d) Primeiro grupo de 1 palavra marginal e absorvido pelo seguinte
diar = [d(0.4, 0.5, "SPEAKER_00"), d(1, 10, "SPEAKER_01")]
words = [w(0.2, 0.9, "eh"), w(1.2, 1.7, "c"), w(2, 2.5, "d")]
# "eh": dur 0.7, overlap A=0.1 -> containment 0.14 < 0.5 -> vira B; colapsa em 1 grupo
out = split_segment_by_word_diarization(seg(words), diar)
assert out == [], [(o["speaker"], o["text"]) for o in out]
print("PASS: primeiro grupo marginal absorvido pelo seguinte")

# (e) Regressao: split normal em fronteira limpa continua identico
diar = [d(0, 5, "SPEAKER_00"), d(5, 10, "SPEAKER_01")]
words = [w(1, 1.5, "a"), w(2, 2.5, "b"), w(6, 6.5, "c"), w(7, 7.5, "d")]
out = split_segment_by_word_diarization(seg(words), diar)
assert [(o["speaker"], o["text"]) for o in out] == [("SPEAKER_00", "a b"), ("SPEAKER_01", "c d")], out
assert all(o.get("diarization_split") for o in out)
print("PASS: split normal inalterado")

# Contratos preservados: < 2 palavras utilizaveis -> []
assert split_segment_by_word_diarization(seg([w(1, 1.5, "a")]), diar) == []
print("PASS: segmento com < 2 palavras continua sem split")

print()
print("PASS: toy_word_split_smoothing")
