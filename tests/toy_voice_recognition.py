"""Toy test para voice_recognition (plano D2.5+X1a, itens 12-13).

Ancoras locais + regra de recorrencia (>=2 arquivos distintos) + match por
cosseno com limiar. Stdlib pura — roda ate no CI minimo.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import voice_recognition as vr
from transcribe_pipeline.config import ensure_directories, load_config, make_paths

# Cosseno basico + guardas
assert abs(vr.cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
assert abs(vr.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
assert vr.cosine_similarity([1.0], [1.0, 0.0]) == 0.0  # dims diferentes
assert vr.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0  # vetor nulo
assert vr.cosine_similarity([float("nan"), 1.0], [1.0, 0.0]) == 0.0  # NaN
print("PASS: cosseno com guardas (dim, nulo, NaN)")

# Recorrencia: nome so vira candidato com ancoras de >=2 arquivos DISTINTOS
anchors: list[dict] = []
anchors = vr.add_anchor(anchors, "Entrevistador", "F01", [1.0, 0.0])
assert vr.recurring_names(anchors) == []
anchors = vr.add_anchor(anchors, "Entrevistador", "F01", [0.9, 0.1])  # substitui, mesmo arquivo
assert len(anchors) == 1 and vr.recurring_names(anchors) == []
anchors = vr.add_anchor(anchors, "entrevistador", "F02", [1.0, 0.05])  # casefold junta
assert vr.recurring_names(anchors) == ["Entrevistador"]
anchors = vr.add_anchor(anchors, "Andressa", "F02", [0.0, 1.0])  # 1 arquivo so
assert vr.recurring_names(anchors) == ["Entrevistador"]
print("PASS: recorrencia por arquivos distintos (casefold, substituicao)")

# Match: so nomes recorrentes; melhor score; corte por limiar
embeddings = {
    "SPEAKER_00": [0.95, 0.05],   # perto do Entrevistador
    "SPEAKER_01": [0.05, 0.95],   # perto da Andressa (NAO recorrente -> fora)
    "SPEAKER_02": [0.5, 0.5],     # ambigua
}
matches = vr.match_voices(embeddings, anchors, threshold=0.9)
assert set(matches) == {"SPEAKER_00"}, matches
name, score = matches["SPEAKER_00"]
assert name == "Entrevistador" and score > 0.9
print("PASS: match so em recorrentes, acima do limiar")

# Limiar acima do maximo possivel (cosseno <= 1.0) silencia; sem ancoras idem
assert vr.match_voices(embeddings, anchors, threshold=1.01) == {}
assert vr.match_voices(embeddings, [], threshold=0.1) == {}
print("PASS: limiar e ausencia de ancoras silenciam")

# Round-trip em disco (ancoras + embeddings por arquivo)
with tempfile.TemporaryDirectory() as tmp:
    config = load_config(None)
    config["project_root"] = "."
    paths = make_paths(config, base_dir=Path(tmp))
    ensure_directories(paths)

    vr.save_anchors(paths, anchors)
    assert vr.load_anchors(paths) == anchors
    vr.write_speaker_embeddings(paths, "F03", {"SPEAKER_00": [1.0, 0.0]}, "pyannote/x")
    assert vr.load_speaker_embeddings(paths, "F03") == {"SPEAKER_00": [1.0, 0.0]}
    assert vr.load_speaker_embeddings(paths, "NAO_EXISTE") == {}
    # Corrompido nunca crasha
    vr.anchors_path(paths).write_text("nao-e-json{", encoding="utf-8")
    assert vr.load_anchors(paths) == []
    print("PASS: round-trip em disco + corrompido -> vazio")

print()
print("PASS: toy_voice_recognition")
