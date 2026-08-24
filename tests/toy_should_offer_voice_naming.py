"""Toy test para should_offer_voice_naming (gatilho puro do plano D2.5).

O bug original: o rotulo default posicional (Entrevistador/Entrevistado)
mascarava o estado "nunca confirmado" e a pergunta jamais aparecia com N=2.
O gatilho novo ignora os rotulos e olha: voice_naming_prompt do projeto,
speakers_confirmed do arquivo e >=2 vozes CRUAS (excluindo SPEAKER_UNKNOWN).

Importa review_studio_qt (helpers fora do bloco Qt); skip no CI minimo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import raw_voice_ids, should_offer_voice_naming
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def turn(speaker: str, human: str = "") -> dict:
    return {"speaker": speaker, "human_label": human, "start": 0.0, "end": 1.0, "text": "x"}


# CASO-ALVO: N=2 com rotulos default JA aplicados pelo render -> DEVE ofertar
turns = [turn("SPEAKER_00", "Entrevistador"), turn("SPEAKER_01", "Entrevistado")]
assert should_offer_voice_naming({}, {}, turns) is True
assert should_offer_voice_naming({"voice_naming_prompt": True}, {"speakers_confirmed": ""}, turns) is True
print("PASS: N=2 rotulado por default AINDA oferta (o bug relatado)")

# Confirmado por humano -> nunca oferta
assert should_offer_voice_naming({}, {"speakers_confirmed": "true"}, turns) is False
assert should_offer_voice_naming({}, {"speakers_confirmed": " TRUE "}, turns) is False
print("PASS: speakers_confirmed=true silencia")

# Toggle do projeto desligado -> nunca oferta (modo lote)
assert should_offer_voice_naming({"voice_naming_prompt": False}, {}, turns) is False
print("PASS: voice_naming_prompt=False silencia (lote)")

# Menos de 2 vozes cruas -> nao oferta; SPEAKER_UNKNOWN nao conta
assert should_offer_voice_naming({}, {}, [turn("SPEAKER_00")]) is False
assert should_offer_voice_naming({}, {}, [turn("SPEAKER_00"), turn("SPEAKER_UNKNOWN")]) is False
assert raw_voice_ids([turn("SPEAKER_00"), turn("SPEAKER_UNKNOWN"), turn("SPEAKER_02")]) == ["SPEAKER_00", "SPEAKER_02"]
print("PASS: <2 vozes cruas / SPEAKER_UNKNOWN fora")

# Metadado ausente (arquivo legado) -> oferta 1x, como desejado
assert should_offer_voice_naming({}, None, turns) is True
print("PASS: arquivo legado sem metadado oferta uma vez")

print()
print("PASS: toy_should_offer_voice_naming")
