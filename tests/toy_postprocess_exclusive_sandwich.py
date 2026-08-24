"""Toy test para _postprocess_annotation com preserve_exclusive (fix C59).

Bug (confirmado empiricamente em 2026-08-23): Annotation.support(collar)
funde POR ROTULO, independentemente dos demais falantes. No padrao A-B-A
com gap A-A < collar (um aparte curto de B, ex. "hum-hum"), A e esticado
por cima de B — reintroduzindo overlap na annotation exclusiva, cuja
invariante e justamente nao ter overlap. Isso dispara needs_speaker_review
e disputas de best_overlap_speaker no render.

Depende de numpy + pyannote.core (leves, sem torch). Skip condicional se
ausentes (CI minimo), conforme tests/ (politica 0.3+).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from pyannote.core import Annotation, Segment

    from transcribe_pipeline.diarization import _postprocess_annotation
except ImportError as exc:  # CI minimo sem pyannote.core/numpy
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

CONFIG = {"diarization_min_segment": 0.3, "diarization_collar": 0.5}


def segs(annotation) -> list[tuple[float, float, str]]:
    return [
        (round(s.start, 3), round(s.end, 3), str(spk))
        for s, _t, spk in annotation.itertracks(yield_label=True)
    ]


def has_overlap(annotation) -> bool:
    items = sorted(segs(annotation))
    for (_s1, e1, _l1), (s2, _e2, _l2) in zip(items, items[1:]):
        if s2 < e1 - 1e-9:
            return True
    return False


def sandwich() -> Annotation:
    # Gap A-A = 0.4 < collar 0.5; aparte B de 0.3s sobrevive ao min_segment.
    a = Annotation(uri="toy")
    a[Segment(0.0, 1.0), "t0"] = "SPEAKER_00"
    a[Segment(1.05, 1.35), "t1"] = "SPEAKER_01"
    a[Segment(1.4, 2.4), "t2"] = "SPEAKER_00"
    return a


# 1. Exclusive: sanduiche A-B-A preservado, sem overlap (o fix C59)
out = _postprocess_annotation(sandwich(), CONFIG, preserve_exclusive=True)
assert not has_overlap(out), f"exclusive nao pode ter overlap: {segs(out)}"
assert len(segs(out)) == 3, f"aparte de B foi engolido: {segs(out)}"
assert "SPEAKER_01" in {s[2] for s in segs(out)}, segs(out)
print("PASS: exclusive preserva o sanduiche A-B-A sem overlap")

# 2. Regular: mantem a semantica antiga do support() (overlaps sao legitimos
#    na camada regular; apenas documenta a diferenca intencional)
out = _postprocess_annotation(sandwich(), CONFIG)
assert has_overlap(out), f"regular deveria manter semantica support(): {segs(out)}"
print("PASS: regular mantem support() (sem mudanca de comportamento)")

# 3. Ponte sobre silencio do mesmo falante continua fundindo no exclusive
a = Annotation(uri="bridge")
a[Segment(0.0, 1.0), "t0"] = "SPEAKER_00"
a[Segment(1.2, 2.0), "t1"] = "SPEAKER_00"
out = _postprocess_annotation(a, CONFIG, preserve_exclusive=True)
assert segs(out) == [(0.0, 2.0, "SPEAKER_00")], segs(out)
print("PASS: gap silencioso < collar continua fundindo")

# 4. Gap == collar NAO funde (mesma semantica estrita do support, confirmada
#    empiricamente: support() so funde gap estritamente menor que o collar)
a = Annotation(uri="wide")
a[Segment(0.0, 1.0), "t0"] = "SPEAKER_00"
a[Segment(1.5, 2.3), "t1"] = "SPEAKER_00"
out = _postprocess_annotation(a, CONFIG, preserve_exclusive=True)
assert len(segs(out)) == 2, segs(out)
print("PASS: gap == collar preserva (paridade estrita com support)")

# 5. Miolo removido pelo min_segment: os A adjacentes fundem (intencional —
#    o micro-aparte ja foi descartado pela regra de segmento minimo)
a = Annotation(uri="removed")
a[Segment(0.0, 1.0), "t0"] = "SPEAKER_00"
a[Segment(1.0, 1.1), "t1"] = "SPEAKER_01"  # 0.1s < min_segment 0.3
a[Segment(1.2, 2.0), "t2"] = "SPEAKER_00"
out = _postprocess_annotation(a, CONFIG, preserve_exclusive=True)
assert segs(out) == [(0.0, 2.0, "SPEAKER_00")], segs(out)
print("PASS: micro-aparte removido pelo min_segment ainda permite a ponte")

print()
print("PASS: toy_postprocess_exclusive_sandwich")
