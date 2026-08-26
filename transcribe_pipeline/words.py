"""Indice de palavras por entrevista (fase 3): tempos por palavra.

O WhisperX sempre roda o alinhamento e grava segments[].words[] com
word/start/end/score em 02_asr_raw/{id}.json (o caminho MLX grava o
mesmo formato, sem score). O render descarta as palavras ao montar os
turnos; este modulo as recupera direto do ASR raw e as ancora por TEMPO
(bisect na lista global ordenada) — nada de words dentro dos turnos,
entao canonical/review/split/merge/undo ficam intocados e o indice
sobrevive a qualquer edicao de turno.

Sem ASR (arquivo so-midia), sem words (MLX com asr_word_timestamps
desligado) ou JSON ilegivel: lista vazia e todas as features degradam
em silencio.
"""
from __future__ import annotations

from bisect import bisect_left
from typing import Any

from .config import Paths
from .render import find_whisperx_json, usable_words
from .utils import read_json

UNCERTAIN_PERCENTILE = 10.0


def flatten_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Achata segments[].words[] em lista ordenada por start (pura).

    Cada item: {start, end, text, score|None}. score vem do modelo de
    alinhamento do WhisperX; o caminho MLX nao produz score -> None.
    """
    flat: list[dict[str, Any]] = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for word in usable_words(segment.get("words")):
            raw = word.get("raw") or {}
            try:
                score: float | None = float(raw.get("score"))
            except (TypeError, ValueError):
                score = None
            flat.append({
                "start": float(word["start"]),
                "end": float(word["end"]),
                "text": str(word["text"]),
                "score": score,
            })
    flat.sort(key=lambda item: (item["start"], item["end"]))
    return flat


def load_word_index(paths: Paths, interview_id: str) -> list[dict[str, Any]]:
    """Indice de palavras da entrevista; [] quando indisponivel."""
    source = find_whisperx_json(paths, interview_id)
    if source is None:
        return []
    try:
        payload = read_json(source)
    except Exception:  # noqa: BLE001 - palavras sao opcionais, nunca quebrar
        return []
    if not isinstance(payload, dict):
        return []
    return flatten_words(payload)


def words_in_range(index: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    """Palavras cujo START cai em [start, end) — bisect, pura."""
    if not index or end <= start:
        return []
    starts = [item["start"] for item in index]
    low = bisect_left(starts, float(start))
    high = bisect_left(starts, float(end))
    return index[low:high]


def word_time_for_char(
    words: list[dict[str, Any]], text: str, char_offset: int,
) -> tuple[float | None, bool]:
    """(tempo, exato) da palavra sob o offset de caractere do turno.

    Tokens de text.split() casam 1:1 com as palavras quando o texto nao
    foi editado (caso comum) -> tempo EXATO. Contagens divergentes
    (texto editado) -> palavra pela fracao de tokens (exato=False),
    ainda muito melhor que interpolacao linear no tempo, que ignora
    pausas. Sem palavras ou sem texto -> (None, False).
    """
    tokens = text.split()
    if not words or not tokens:
        return None, False
    spans: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        found = text.find(token, cursor)
        if found < 0:
            found = cursor
        spans.append((found, found + len(token)))
        cursor = found + len(token)
    token_index = len(tokens) - 1
    for position, (_span_start, span_end) in enumerate(spans):
        if char_offset <= span_end:
            token_index = position
            break
    if len(words) == len(tokens):
        return float(words[token_index]["start"]), True
    if len(tokens) > 1:
        fraction = token_index / (len(tokens) - 1)
    else:
        fraction = 0.0
    word_index = round(fraction * (len(words) - 1))
    return float(words[word_index]["start"]), False


def uncertain_threshold(
    index: list[dict[str, Any]], percentile: float = UNCERTAIN_PERCENTILE,
) -> float | None:
    """Corte de score do decil inferior da entrevista (posicao incerta).

    None quando nao ha scores (caminho MLX) -> nenhuma marcacao.
    """
    scores = sorted(
        item["score"] for item in index if item.get("score") is not None)
    if not scores:
        return None
    cut = int(len(scores) * percentile / 100.0)
    cut = min(max(cut, 0), len(scores) - 1)
    return float(scores[cut])
