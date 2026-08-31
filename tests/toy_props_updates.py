"""Toy R4-c6: diff puro do form da aba Propriedades.

props_metadata_updates() so grava o que o usuario TOCOU e que DIFERE
do atual — nunca write-back integral (outros fluxos escrevem
speakers_confirmed no mesmo CSV). Paridade de chaves com
MetadataDialog.updates()/SpeakerCountDialog; range normaliza min/max;
rotulos vazios nunca limpam.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from transcribe_pipeline.review_studio_qt import props_metadata_updates
except ImportError as exc:  # CI minimo sem PySide6
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

FORM = {
    "language": "pt",
    "speaker_mode": "exact",
    "speaker_count": 2,
    "min_speakers": 3,
    "max_speakers": 8,
    "speaker_labels": "Entrevistador|Entrevistado",
    "context_text": "Censo 2022.",
    "use_context": True,
}
ATUAL = {
    "language": "pt",
    "speaker_mode": "exact",
    "speaker_count": "2",
    "min_speakers": "2",
    "max_speakers": "2",
    "speaker_labels": "Entrevistador|Entrevistado",
    "context_text": "Censo 2022.",
    "use_context_as_prompt": "true",
}

# Nada tocado -> nada gravado (mesmo com atual "diferente" do form)
assert props_metadata_updates({}, FORM, set()) == {}

# Tocado mas IGUAL ao atual -> nada gravado
assert props_metadata_updates(ATUAL, FORM, {"language", "falantes",
                                            "rotulos", "contexto"}) == {}

# Lingua diferente
u = props_metadata_updates(ATUAL, dict(FORM, language="co"), {"language"})
assert u == {"language": "co"}, u

# Falantes exact -> trio count=min=max (paridade com os dialogos)
u = props_metadata_updates(ATUAL, dict(FORM, speaker_count=3), {"falantes"})
assert u == {"speaker_mode": "exact", "speaker_count": "3",
             "min_speakers": "3", "max_speakers": "3"}, u

# Range com min>max -> normaliza low/high (SpeakerCountDialog faz igual)
u = props_metadata_updates(
    ATUAL, dict(FORM, speaker_mode="range", min_speakers=8, max_speakers=3),
    {"falantes"})
assert u == {"speaker_mode": "range", "speaker_count": "",
             "min_speakers": "3", "max_speakers": "8"}, u

# Auto esvazia o trio
u = props_metadata_updates(ATUAL, dict(FORM, speaker_mode="auto"), {"falantes"})
assert u == {"speaker_mode": "auto", "speaker_count": "",
             "min_speakers": "", "max_speakers": ""}, u

# Rotulos: vazio NUNCA limpa; diferente grava
assert props_metadata_updates(ATUAL, dict(FORM, speaker_labels=""),
                              {"rotulos"}) == {}
u = props_metadata_updates(ATUAL, dict(FORM, speaker_labels="Maria|João"),
                           {"rotulos"})
assert u == {"speaker_labels": "Maria|João"}, u

# Contexto: mudou o texto -> grava o bloco inteiro do contexto
u = props_metadata_updates(ATUAL, dict(FORM, context_text="Outro tema"),
                           {"contexto"})
assert u == {"context_mode": "custom", "context_text": "Outro tema",
             "use_context_as_prompt": "true"}, u

# Desligar o uso como prompt (mesmo texto) tambem e mudanca
u = props_metadata_updates(ATUAL, dict(FORM, use_context=False), {"contexto"})
assert u["use_context_as_prompt"] == "false" and u["context_mode"] == "custom"

# Contexto esvaziado -> empty + prompt off (use_context sem texto nao vale)
u = props_metadata_updates(ATUAL, dict(FORM, context_text="  "), {"contexto"})
assert u == {"context_mode": "empty", "context_text": "",
             "use_context_as_prompt": "false"}, u

# Atual VAZIO (arquivo nunca configurado) + campo nao tocado -> silencio;
# tocado -> grava (o caso "espurio" morre pelo rastreio de tocados)
assert props_metadata_updates({}, FORM, set()) == {}
u = props_metadata_updates({}, FORM, {"falantes"})
assert u["speaker_mode"] == "exact" and u["speaker_count"] == "2", u

print("PASS: toy_props_updates (diff minimo, paridade, normalizacao)")
