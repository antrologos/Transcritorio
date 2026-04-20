"""Toy test: pos-processamento de Annotation do pyannote.

Valida duas transformacoes propostas no plano kind-mapping-spindle:
1. Filtro: remover segmentos com duration < min_seg
2. Merge: annotation.support(collar=X) funde turnos proximos do mesmo falante

Executar com:
    "%LOCALAPPDATA%\\Transcritorio\\transcricao-venv\\Scripts\\python.exe" -B tests/toy_diarization_postprocess.py
"""
from __future__ import annotations

from pyannote.core import Annotation, Segment


def filter_short_segments(annotation: Annotation, min_seg: float) -> Annotation:
    cleaned = Annotation(uri=annotation.uri)
    for segment, track, speaker in annotation.itertracks(yield_label=True):
        if segment.duration >= min_seg:
            cleaned[segment, track] = speaker
    return cleaned


def build_fragmented_annotation() -> Annotation:
    """Anotacao sintetica imitando a saida fragmentada do pyannote.

    Expectativa: 7 segmentos originais, fragmentacao realista.
    - SPK_A fala de 0.0 a 3.0 com uma pausa breve (1.0-1.2 silencio),
      depois uma disfluencia muito curta (2.95-3.05).
    - SPK_B responde de 3.5 a 5.0 com uma micro-interrupcao (4.0-4.05).
    - SPK_A retoma em 5.2 a 6.0.
    - Um spurious flicker de SPK_B em 5.5-5.58 no meio de SPK_A (overlap curto).
    """
    a = Annotation(uri="toy")
    a[Segment(0.00, 1.00), "t0"] = "SPK_A"
    a[Segment(1.20, 2.95), "t1"] = "SPK_A"
    a[Segment(2.95, 3.05), "t2"] = "SPK_A"  # micro: 0.10s
    a[Segment(3.50, 4.00), "t3"] = "SPK_B"
    a[Segment(4.00, 4.05), "t4"] = "SPK_B"  # micro: 0.05s
    a[Segment(4.05, 5.00), "t5"] = "SPK_B"
    a[Segment(5.20, 6.00), "t6"] = "SPK_A"
    a[Segment(5.50, 5.58), "t7"] = "SPK_B"  # micro: 0.08s (flicker em overlap)
    return a


def count(annotation: Annotation) -> int:
    return sum(1 for _ in annotation.itertracks())


def summarize(annotation: Annotation) -> list[tuple[float, float, str]]:
    return [
        (round(seg.start, 3), round(seg.end, 3), str(spk))
        for seg, _t, spk in annotation.itertracks(yield_label=True)
    ]


def test_filter_removes_micro_segments() -> None:
    anno = build_fragmented_annotation()
    assert count(anno) == 8, f"setup: esperava 8 segmentos, achei {count(anno)}"

    filtered = filter_short_segments(anno, min_seg=0.3)
    kept = summarize(filtered)
    removed = count(anno) - count(filtered)

    assert removed == 3, f"esperava remover 3 micro-segmentos (<0.3s), removi {removed}"
    for start, end, _spk in kept:
        assert (end - start) >= 0.3, f"segmento {start}-{end} ficou apesar de <0.3s"
    print(f"PASS filter: {removed} micro-segmentos removidos, {len(kept)} restantes")


def test_support_merges_close_same_speaker() -> None:
    anno = build_fragmented_annotation()
    filtered = filter_short_segments(anno, min_seg=0.3)
    merged = filtered.support(collar=0.5)

    segments = summarize(merged)
    speakers = [spk for _, _, spk in segments]

    assert count(merged) < count(filtered), (
        f"support(collar=0.5) deveria reduzir segmentos: antes {count(filtered)}, depois {count(merged)}"
    )

    for (s1, e1, spk1), (s2, e2, spk2) in zip(segments, segments[1:]):
        if spk1 == spk2:
            gap = s2 - e1
            assert gap > 0.5, (
                f"segmentos {spk1} em ({s1},{e1}) e ({s2},{e2}) com gap {gap:.3f}s deveriam ter fundido (collar=0.5)"
            )

    a_segments = [(s, e) for s, e, spk in segments if spk == "SPK_A"]
    if a_segments:
        first_a_start, first_a_end = a_segments[0]
        assert first_a_start == 0.00, f"primeiro SPK_A deveria comecar em 0.00, comecou em {first_a_start}"
        assert first_a_end >= 2.95, (
            f"primeiro SPK_A deveria fundir 0-1 + 1.2-2.95 (gap 0.2s), terminou em {first_a_end}"
        )

    print(f"PASS support: {count(filtered)} -> {count(merged)} segmentos")
    print("  segmentos apos merge:")
    for s, e, spk in segments:
        print(f"    {spk}: {s:.2f} - {e:.2f} ({e-s:.2f}s)")


def test_support_preserves_large_gaps() -> None:
    """Gaps maiores que collar NAO devem fundir."""
    a = Annotation(uri="gap")
    a[Segment(0.0, 1.0), "t0"] = "SPK_A"
    a[Segment(2.0, 3.0), "t1"] = "SPK_A"  # gap 1.0s > collar 0.5
    merged = a.support(collar=0.5)
    assert count(merged) == 2, f"gap 1.0s > collar 0.5 nao deveria fundir, got {count(merged)}"
    print("PASS gap preservation: gap > collar nao funde")


def test_support_preserves_speaker_boundaries() -> None:
    """Speaker change NUNCA deve fundir, mesmo com gap pequeno."""
    a = Annotation(uri="speaker")
    a[Segment(0.0, 1.0), "t0"] = "SPK_A"
    a[Segment(1.05, 2.0), "t1"] = "SPK_B"  # gap 0.05s mas falante diferente
    merged = a.support(collar=0.5)
    assert count(merged) == 2, f"falantes diferentes nao deveriam fundir, got {count(merged)}"
    print("PASS speaker preservation: falantes diferentes nunca fundem")


if __name__ == "__main__":
    test_filter_removes_micro_segments()
    test_support_merges_close_same_speaker()
    test_support_preserves_large_gaps()
    test_support_preserves_speaker_boundaries()
    print()
    print("PASS: toy_diarization_postprocess")
