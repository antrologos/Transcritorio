"""Toy: contagem de falantes conforme a duração do áudio — 2026-09-04.

Um usuário testou um recorte de 1 minuto e disse que a separação de vozes
"ficou ruim". Estava: o app passava `num_speakers=2`, que é uma ORDEM e não
uma dica — o agrupamento fica obrigado a devolver 2 grupos e parte a voz única
em duas. Medido contra a diarização da entrevista inteira: erro de 28,4% e
32,1% nos recortes de 1 min forçando 2, contra 0,3% deixando contar. Na
entrevista de 24 min, forçado e automático dão exatamente o mesmo resultado —
ou seja, a imposição não ajuda no longo e destrói no curto.

Puro: só `speaker_kwargs`. Sem torch, sem pyannote.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.diarization import SHORT_AUDIO_SECONDS, speaker_kwargs  # noqa: E402

DOIS = {"diarization_num_speakers": 2}
LONGO = 24 * 60.0
CURTO = 60.0

# --- sem saber a duração, nada muda (todos os caminhos antigos) ---
assert speaker_kwargs(DOIS) == {"num_speakers": 2}
assert speaker_kwargs(DOIS, None) == {"num_speakers": 2}

# --- áudio longo: a exigência continua exata, como sempre foi ---
assert speaker_kwargs(DOIS, LONGO) == {"num_speakers": 2}
assert speaker_kwargs(DOIS, SHORT_AUDIO_SECONDS) == {"num_speakers": 2}, "o limiar nao entra"
assert speaker_kwargs(DOIS, SHORT_AUDIO_SECONDS + 1) == {"num_speakers": 2}

# --- áudio curto: vira FAIXA, e o teto continua sendo o do projeto ---
assert speaker_kwargs(DOIS, CURTO) == {"min_speakers": 1, "max_speakers": 2}
assert speaker_kwargs(DOIS, 1.0) == {"min_speakers": 1, "max_speakers": 2}
assert speaker_kwargs({"diarization_num_speakers": 4}, CURTO) == {"min_speakers": 1, "max_speakers": 4}
# um projeto declarado com 1 falante continua com teto 1 (nunca inventa vozes)
assert speaker_kwargs({"diarization_num_speakers": 1}, CURTO) == {"min_speakers": 1, "max_speakers": 1}
assert speaker_kwargs({"diarization_num_speakers": 0}, CURTO) == {"min_speakers": 1, "max_speakers": 1}
print("PASS: exato no audio longo, faixa no curto")

# --- duração inválida não muda nada (0, negativa) ---
assert speaker_kwargs(DOIS, 0.0) == {"num_speakers": 2}
assert speaker_kwargs(DOIS, -5.0) == {"num_speakers": 2}
print("PASS: duracao invalida nao mexe na regra")

# --- quem já usava faixa continua igual, com ou sem duração ---
faixa = {"min_speakers": 2, "max_speakers": 5}
assert speaker_kwargs(faixa) == faixa
assert speaker_kwargs(faixa, CURTO) == faixa
assert speaker_kwargs(faixa, LONGO) == faixa
assert speaker_kwargs({}) == {}
assert speaker_kwargs({}, CURTO) == {}
assert speaker_kwargs({"min_speakers": 2}, CURTO) == {"min_speakers": 2}
print("PASS: configuracao por faixa e vazia intactas")

print("PASS: toy_speaker_kwargs")
