"""Toy test para speaker_sample_clips (plano D2.6).

Amostras v2: excluem turnos com marcacoes suspeitas (duvida/sobreposicao),
espalham por inicio/meio/fim do audio, trazem o TEXTO do trecho e saem em
ordem cronologica. Restricao de fundo (pergunta do usuario 2026-08-23): uma
amostra do falante errado induz nome errado para a voz inteira — turnos
suspeitos sao exatamente os candidatos a contaminacao.

Importa review_studio_qt (helpers fora do bloco Qt); skip no CI minimo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import raw_speaker_key, speaker_sample_clips
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def turn(speaker: str, start: float, end: float, text: str, flags: list[str] | None = None) -> dict:
    return {"speaker": speaker, "human_label": "", "start": start, "end": end, "text": text, "flags": flags or []}


# Espalhamento por tercos + texto + ordem cronologica. Audio de ~90s:
turns = [
    turn("SPEAKER_00", 0.0, 6.0, "abertura da entrevista"),          # inicio
    turn("SPEAKER_00", 2.0, 20.0, "turno mais longo do inicio"),     # inicio (mais longo)
    turn("SPEAKER_00", 40.0, 44.0, "trecho do meio"),                # meio
    turn("SPEAKER_00", 80.0, 90.0, "encerramento da conversa"),      # fim
    turn("SPEAKER_01", 10.0, 15.0, "outra voz"),
]
clips = speaker_sample_clips(turns, "SPEAKER_00", key_fn=raw_speaker_key)
assert len(clips) == 3, clips
assert [c["text"] for c in clips] == ["turno mais longo do inicio", "trecho do meio", "encerramento da conversa"], clips
assert clips == sorted(clips, key=lambda c: c["start"]), "ordem cronologica"
print("PASS: espalha por tercos, escolhe o mais longo de cada, ordem cronologica")

# Corte em max_seconds
assert clips[0]["end"] - clips[0]["start"] <= 8.0 + 1e-9
print("PASS: trecho cortado em max_seconds")

# Turnos suspeitos (duvida/sobreposicao) ficam FORA quando ha alternativa
turns = [
    turn("SPEAKER_00", 0.0, 30.0, "trecho suspeito longo", flags=["duvida"]),
    turn("SPEAKER_00", 40.0, 45.0, "trecho limpo"),
]
clips = speaker_sample_clips(turns, "SPEAKER_00", key_fn=raw_speaker_key)
assert [c["text"] for c in clips] == ["trecho limpo"], clips
print("PASS: turno com 'duvida' excluido quando ha alternativa limpa")

# Fallback: se TODOS sao suspeitos, usa-os mesmo assim (melhor que nada)
turns = [
    turn("SPEAKER_00", 0.0, 10.0, "so tem suspeito", flags=["sobreposicao"]),
]
clips = speaker_sample_clips(turns, "SPEAKER_00", key_fn=raw_speaker_key)
assert len(clips) == 1 and clips[0]["text"] == "so tem suspeito"
print("PASS: fallback quando todos os turnos sao suspeitos")

# Voz inexistente -> vazio; poucos turnos -> completa com os mais longos
assert speaker_sample_clips([], "SPEAKER_00") == []
turns = [
    turn("SPEAKER_00", 0.0, 3.0, "a"),
    turn("SPEAKER_00", 1.0, 9.0, "b"),
]
clips = speaker_sample_clips(turns, "SPEAKER_00", key_fn=raw_speaker_key)
assert len(clips) == 2, clips
print("PASS: bordas (vazio, menos turnos que amostras)")

print()
print("PASS: toy_speaker_sample_clips")
