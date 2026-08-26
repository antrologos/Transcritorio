"""Busca nas transcricoes: literal + por significado (fase 2.3).

Literal: stdlib pura, insensivel a acentos/caixa, com MAPA de posicoes
(a normalizacao muda comprimentos; o mapa devolve os spans no texto
original para o realce na UI).

Por significado: encoder MiniLM multilingue rodando NO ambiente do app
(transformers transitivo; validado em 2026-08-26: CPU 0,15s/3 frases,
funciona sem GPU e sem o llm-venv). Indice por arquivo em
Transcricoes/07_index/{id}.index.json (JSON direto, regra Dropbox),
invalidado por mtime da fonte; a fonte e a transcricao REVISADA quando
existe (mesma preferencia do resumo).

A UI nunca usa jargao: as secoes sao "Resultados exatos" e "Trechos com
sentido parecido".
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .review_store import canonical_path, review_path
from .utils import read_json, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

ENCODER_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
INDEX_VERSION = 2  # v2: embeddings com janela de contexto (2026-08-26)
MIN_SIMILARITY = 0.35
TOP_N = 20
EMBED_BATCH = 16
CONTEXT_TAIL_CHARS = 150
CONTEXT_HEAD_CHARS = 150


# ---------------------------------------------------------------------------
# Literal (puro)
# ---------------------------------------------------------------------------

def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """(texto normalizado, mapa) — mapa[i] = indice no texto ORIGINAL.

    Normaliza caixa e remove acentos preservando a rastreabilidade das
    posicoes, para o realce marcar os spans certos no texto original.
    """
    normalized_chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFD", char)
        base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
        for out_char in base.lower():
            normalized_chars.append(out_char)
            positions.append(index)
    return "".join(normalized_chars), positions


def normalize(text: str) -> str:
    return normalize_with_map(str(text))[0]


def literal_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Spans [inicio, fim) do termo no texto ORIGINAL (todos os matches)."""
    query_norm = normalize(query)
    if not query_norm:
        return []
    haystack, positions = normalize_with_map(str(text))
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = haystack.find(query_norm, start)
        if found < 0:
            break
        original_start = positions[found]
        original_end = positions[found + len(query_norm) - 1] + 1
        spans.append((original_start, original_end))
        start = found + 1
    return spans


def search_turns(turns: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Hits literais num arquivo: [{turn_index, spans}] na ordem dos turnos."""
    hits: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        spans = literal_spans(str(turn.get("text") or ""), query)
        if spans:
            hits.append({"turn_index": index, "spans": spans})
    return hits


def context_window_text(
    turns: list[dict[str, Any]],
    index: int,
    tail_chars: int = CONTEXT_TAIL_CHARS,
    head_chars: int = CONTEXT_HEAD_CHARS,
) -> str:
    """Texto do EMBEDDING do turno index: cauda do turno anterior + turno +
    cabeca do seguinte (pura, testavel).

    Fragmentos e interjeicoes ("Eita", "Mas isso") herdam o TEMA da
    vizinhanca em vez de virarem vetores espurios que pontuam contra
    qualquer consulta (feedback 2026-08-26; corte por tamanho foi vetado
    — pesquisador pode querer exatamente interjeicoes). O texto exibido
    e ancorado segue sendo o do proprio turno.
    """

    def clean(position: int) -> str:
        if 0 <= position < len(turns):
            return " ".join(str(turns[position].get("text") or "").split())
        return ""

    before = clean(index - 1)[-tail_chars:] if tail_chars else ""
    after = clean(index + 1)[:head_chars] if head_chars else ""
    return " ".join(part for part in (before, clean(index), after) if part)


def collapse_adjacent(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantem so o melhor hit de turnos adjacentes da mesma entrevista.

    Com janelas de contexto, vizinhos do mesmo momento pontuam juntos;
    espera hits em ordem decrescente de similaridade (pura, testavel).
    """
    kept: list[dict[str, Any]] = []
    taken: set[tuple[str, int]] = set()
    for hit in hits:
        key = (str(hit.get("interview_id")), int(hit.get("turn_index", -1)))
        if (key in taken
                or (key[0], key[1] - 1) in taken
                or (key[0], key[1] + 1) in taken):
            continue
        taken.add(key)
        kept.append(hit)
    return kept


# ---------------------------------------------------------------------------
# Fonte dos turnos (review > canonico) e indice
# ---------------------------------------------------------------------------

def source_path_for(paths: Paths, interview_id: str) -> Path | None:
    review = review_path(paths, interview_id)
    if review.exists():
        return review
    canonical = canonical_path(paths, interview_id)
    return canonical if canonical.exists() else None


def load_source_turns(paths: Paths, interview_id: str) -> list[dict[str, Any]]:
    source = source_path_for(paths, interview_id)
    if source is None:
        return []
    payload = read_json(source)
    transcript = payload.get("transcript") or payload
    return [t for t in (transcript.get("turns") or []) if str(t.get("text") or "").strip()]


def index_dir(paths: Paths) -> Path:
    return paths.output_root / "07_index"


def index_path(paths: Paths, interview_id: str) -> Path:
    return index_dir(paths) / f"{interview_id}.index.json"


def index_is_fresh(paths: Paths, interview_id: str) -> bool:
    source = source_path_for(paths, interview_id)
    target = index_path(paths, interview_id)
    if source is None or not target.exists():
        return False
    try:
        payload = read_json(target)
    except Exception:  # noqa: BLE001 - indice corrompido = refazer
        return False
    return (
        int(payload.get("version", -1)) == INDEX_VERSION
        and float(payload.get("source_mtime", -1)) == source.stat().st_mtime
        and str(payload.get("model")) == ENCODER_REPO
    )


def build_index_payload(
    interview_id: str,
    turns: list[dict[str, Any]],
    vectors: list[list[float]],
    source_mtime: float,
) -> dict[str, Any]:
    """Payload puro do indice (testavel sem encoder)."""
    entries = [
        {
            "t": index,
            "start": float(turn.get("start", 0) or 0),
            "label": str(turn.get("human_label") or turn.get("speaker") or ""),
            "text": " ".join(str(turn.get("text") or "").split()),
        }
        for index, turn in enumerate(turns)
    ]
    return {
        "version": INDEX_VERSION,
        "model": ENCODER_REPO,
        "interview_id": interview_id,
        "source_mtime": float(source_mtime),
        "dim": len(vectors[0]) if vectors else 0,
        "turns": entries,
        "vectors": [[round(float(v), 4) for v in vec] for vec in vectors],
    }


# ---------------------------------------------------------------------------
# Encoder (transformers do proprio app; CPU ok, GPU se houver)
# ---------------------------------------------------------------------------

def load_encoder():
    """(tokenizer, model, device) — do cache do app, offline."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    from . import runtime

    cache = str(runtime.model_cache_dir())
    tokenizer = AutoTokenizer.from_pretrained(ENCODER_REPO, cache_dir=cache, local_files_only=True)
    model = AutoModel.from_pretrained(ENCODER_REPO, cache_dir=cache, local_files_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    return tokenizer, model, device


def embed_texts(texts: list[str], encoder) -> list[list[float]]:
    """Embeddings normalizados (mean pooling) em lotes."""
    import torch

    tokenizer, model, device = encoder
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        with torch.no_grad():
            enc = tokenizer(batch, padding=True, truncation=True, max_length=256,
                            return_tensors="pt").to(device)
            hidden = model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, dim=1)
        vectors.extend([float(v) for v in row] for row in pooled.cpu())
    return vectors


def encoder_cached() -> bool:
    from . import model_manager, runtime

    try:
        return model_manager.cached_snapshot_path(
            ENCODER_REPO, runtime.model_cache_dir()) is not None
    except Exception:  # noqa: BLE001
        return False


def build_indexes(
    paths: Paths,
    interview_ids: list[str],
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """(Re)constroi os indices DESATUALIZADOS; retorna falhas."""
    stale = [iid for iid in interview_ids if not index_is_fresh(paths, iid)]
    if not stale:
        return 0
    encoder = None
    failures = 0
    for position, interview_id in enumerate(stale):
        if should_cancel is not None and should_cancel():
            break
        if progress_callback is not None:
            progress_callback({
                "event": "index_progress",
                "progress": int(100 * position / len(stale)),
                "message": (
                    f"Lendo {interview_id} com o modelo de busca semantica "
                    f"({position + 1}/{len(stale)})..."
                ),
            })
        try:
            source = source_path_for(paths, interview_id)
            turns = load_source_turns(paths, interview_id)
            if source is None or not turns:
                continue
            if encoder is None:
                encoder = load_encoder()
            vectors = embed_texts(
                [context_window_text(turns, i) for i in range(len(turns))], encoder)
            payload = build_index_payload(interview_id, turns, vectors, source.stat().st_mtime)
            target = index_path(paths, interview_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, payload)
        except Exception as exc:  # noqa: BLE001 - um arquivo nao derruba o lote
            failures += 1
            print(f"Falha ao indexar {interview_id}: {exc}")
    if progress_callback is not None:
        progress_callback({"event": "index_progress", "progress": 100,
                           "message": "Busca por sentido pronta."})
    return failures


# ---------------------------------------------------------------------------
# Consultas de projeto
# ---------------------------------------------------------------------------

def project_literal_search(
    paths: Paths, interview_ids: list[str], query: str,
) -> list[dict[str, Any]]:
    """Hits exatos no projeto: [{interview_id, turn_index, start, label,
    text, spans}], agrupados por arquivo na ordem dada, tempo crescente."""
    results: list[dict[str, Any]] = []
    for interview_id in interview_ids:
        turns = load_source_turns(paths, interview_id)
        for hit in search_turns(turns, query):
            turn = turns[hit["turn_index"]]
            results.append({
                "interview_id": interview_id,
                "turn_index": hit["turn_index"],
                "start": float(turn.get("start", 0) or 0),
                "label": str(turn.get("human_label") or turn.get("speaker") or ""),
                "text": " ".join(str(turn.get("text") or "").split()),
                "spans": hit["spans"],
            })
    return results


def rank_semantic(
    query_vector: list[float],
    indexes: list[dict[str, Any]],
    exclude: set[tuple[str, int]] = frozenset(),
    min_similarity: float = MIN_SIMILARITY,
    top_n: int = TOP_N,
) -> list[dict[str, Any]]:
    """Ranqueia por cosseno (puro, testavel). exclude = hits ja exatos."""
    from .voice_recognition import cosine_similarity

    scored: list[dict[str, Any]] = []
    for payload in indexes:
        interview_id = str(payload.get("interview_id"))
        turns = payload.get("turns") or []
        vectors = payload.get("vectors") or []
        for entry, vector in zip(turns, vectors):
            key = (interview_id, int(entry.get("t", -1)))
            if key in exclude:
                continue
            similarity = cosine_similarity(query_vector, [float(v) for v in vector])
            if similarity >= min_similarity:
                scored.append({
                    "interview_id": interview_id,
                    "turn_index": int(entry.get("t", -1)),
                    "start": float(entry.get("start", 0) or 0),
                    "label": str(entry.get("label") or ""),
                    "text": str(entry.get("text") or ""),
                    "similarity": round(float(similarity), 3),
                })
    scored.sort(key=lambda item: -item["similarity"])
    return collapse_adjacent(scored)[:top_n]


def project_semantic_search(
    paths: Paths,
    interview_ids: list[str],
    query: str,
    exclude: set[tuple[str, int]] = frozenset(),
    min_similarity: float = MIN_SIMILARITY,
    top_n: int = TOP_N,
) -> list[dict[str, Any]]:
    """Consulta por sentido sobre os indices EXISTENTES e frescos."""
    indexes = []
    for interview_id in interview_ids:
        if index_is_fresh(paths, interview_id):
            indexes.append(read_json(index_path(paths, interview_id)))
    if not indexes:
        return []
    encoder = load_encoder()
    query_vector = embed_texts([query], encoder)[0]
    return rank_semantic(query_vector, indexes, exclude=exclude,
                         min_similarity=min_similarity, top_n=top_n)
