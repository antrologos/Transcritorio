"""Busca nas transcricoes: literal + por significado (fase 2.3; v3 em 2026-09-03).

Literal: stdlib pura, insensivel a acentos/caixa, com MAPA de posicoes
(a normalizacao muda comprimentos; o mapa devolve os spans no texto
original para o realce na UI).

Por significado (v3): a unidade e a PASSAGEM — turnos contiguos ate ~100
palavras, com o rotulo de quem fala, sobreposicao de 1 turno e turno longo
partido por sentenca (`build_passages`, pura). O que e pontuado e o que se
exibe e o que a AI recebe — na v2 o vetor era de "turno + vizinhos" mas a
lista mostrava o turno nu, e "Autorizo." aparecia como "muito proximo".

Encoder de RECUPERACAO (pergunta -> trecho), nao de parafrase: o leve
`multilingual-e5-small` (mesma arquitetura e download do MiniLM antigo,
prefixos `query:`/`passage:`) e, quando instalado, o de qualidade
`multilingual-e5-large-instruct` (instalado => aplicado). Roda no
ambiente do app (transformers), CPU ou GPU. Indice por arquivo em
Transcricoes/07_index/{id}.index.json (metadados, JSON direto — regra
Dropbox) + {id}.vectors.npy (float16), invalidado por mtime da fonte,
versao e modelo; a fonte e a transcricao REVISADA quando existe.

A UI nunca usa jargao: as secoes sao "Resultados exatos" e "Trechos com
sentido parecido".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .review_store import canonical_path, review_path
from .utils import read_json, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

INDEX_VERSION = 3  # v3: passagens + encoder de recuperacao + vetores em .npy (2026-09-03)
TOP_N = 20
EMBED_BATCH = 16
PASSAGE_TARGET_WORDS = 100
PASSAGE_MAX_TURN_WORDS = 160   # turno acima disto e partido por sentenca
PASSAGE_OVERLAP_TURNS = 1
MAX_PASSAGES_PER_INTERVIEW = 3  # no ranking com escopo > 1 entrevista
# Compatibilidade: chamadores antigos passam min_similarity; no e5 os
# cossenos vivem em 0,7-0,9, entao o piso absoluto e desligado e o corte
# de relevancia e relativo (z-score) — ver rank_semantic.
MIN_SIMILARITY = 0.0


@dataclass(frozen=True)
class EncoderSpec:
    key: str
    repo: str
    query_prefix: str
    passage_prefix: str
    pooling: str        # "mean" | "cls"
    max_length: int


ENCODERS: dict[str, EncoderSpec] = {
    # Leve (padrao em qualquer maquina): 12 camadas x 384, MIT.
    "search_encoder": EncoderSpec(
        "search_encoder", "intfloat/multilingual-e5-small",
        query_prefix="query: ", passage_prefix="passage: ", pooling="mean", max_length=512),
    # Qualidade (instalado => aplicado): XLM-R large 24 x 1024, MIT, com
    # instrucao na consulta (em portugues) e passagem sem prefixo; mean
    # pooling. Escolhido pelo gabarito de 2026-09-03 (hit@1 0,92, P@5 0,82).
    "search_encoder_hq": EncoderSpec(
        "search_encoder_hq", "intfloat/multilingual-e5-large-instruct",
        query_prefix=("Instruct: Dada uma pergunta de pesquisa, recupere trechos de "
                      "entrevistas que a respondam\nQuery: "),
        passage_prefix="", pooling="mean", max_length=512),
}
DEFAULT_ENCODER_KEY = "search_encoder"
ENCODER_REPO = ENCODERS[DEFAULT_ENCODER_KEY].repo  # compat: nome antigo


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


# ---------------------------------------------------------------------------
# Passagens (puro)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _turn_label(turn: dict[str, Any]) -> str:
    return " ".join(str(turn.get("human_label") or turn.get("speaker") or "").split())


def _split_long_turn(text: str, target_words: int) -> list[tuple[int, int, str]]:
    """Pedacos (c_from, c_to, texto) de um turno longo, cortando em fim de
    sentenca e juntando sentencas ate ~target_words; nunca corta palavra."""
    pieces: list[tuple[int, int, str]] = []
    sentences: list[tuple[int, int]] = []
    cursor = 0
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence:
            continue
        start = text.find(sentence, cursor)
        if start < 0:
            start = cursor
        sentences.append((start, start + len(sentence)))
        cursor = start + len(sentence)
    if not sentences:
        return [(0, len(text), text)]
    buf_from, buf_to, buf_words = None, None, 0
    for s_from, s_to in sentences:
        words = len(text[s_from:s_to].split())
        if buf_from is not None and buf_words + words > target_words and buf_words > 0:
            pieces.append((buf_from, buf_to, text[buf_from:buf_to]))
            buf_from, buf_to, buf_words = None, None, 0
        if buf_from is None:
            buf_from = s_from
        buf_to = s_to
        buf_words += words
    if buf_from is not None:
        pieces.append((buf_from, buf_to, text[buf_from:buf_to]))
    return pieces


def build_passages(
    turns: list[dict[str, Any]],
    target_words: int = PASSAGE_TARGET_WORDS,
    max_turn_words: int = PASSAGE_MAX_TURN_WORDS,
    overlap_turns: int = PASSAGE_OVERLAP_TURNS,
) -> list[dict[str, Any]]:
    """Passagens de turnos contiguos ate ~target_words palavras.

    Cada passagem: {p, t_from, t_to, c_from, c_to, start, end, text, words}.
    - comeca em fronteira de turno e carrega "Rotulo: " por turno;
    - a proxima passagem repete os ultimos `overlap_turns` turnos da anterior
      (mesmo momento pontua nas duas; o ranking colapsa sobrepostas);
    - turno com mais de max_turn_words vira pedacos por sentenca, cada um
      uma passagem propria (c_from/c_to = offsets no texto do turno).
    Turnos vazios sao ignorados. Pura, testavel.
    """
    # pieces: (turn_index, c_from, c_to, text, words, first_piece_of_turn)
    pieces: list[tuple[int, int, int, str, int, bool]] = []
    for index, turn in enumerate(turns):
        raw = str(turn.get("text") or "")
        text = " ".join(raw.split())
        if not text:
            continue
        words = len(text.split())
        if words > max_turn_words:
            for n, (c_from, c_to, piece) in enumerate(_split_long_turn(text, target_words)):
                pieces.append((index, c_from, c_to, piece, len(piece.split()), n == 0))
        else:
            pieces.append((index, 0, len(text), text, words, True))

    passages: list[dict[str, Any]] = []
    # Enche ate ~target sem estourar; so estoura quando o grupo ainda e
    # pequeno (fragmentos curtos nunca ficam sozinhos numa passagem minima).
    min_words = max(1, int(target_words * 0.4))
    i = 0
    while i < len(pieces):
        j = i
        words = 0
        while j < len(pieces):
            next_words = pieces[j][4]
            if j > i and words >= min_words and words + next_words > target_words:
                break
            words += next_words
            j += 1
        group = pieces[i:j]
        parts: list[str] = []
        for turn_index, _c_from, _c_to, piece, _w, first in group:
            label = _turn_label(turns[turn_index])
            parts.append(f"{label}: {piece}" if (first and label) else piece)
        t_from, t_to = group[0][0], group[-1][0]
        passages.append({
            "p": len(passages),
            "t_from": t_from,
            "t_to": t_to,
            "c_from": group[0][1],
            "c_to": group[-1][2],
            "start": float(turns[t_from].get("start", 0) or 0),
            "end": float(turns[t_to].get("end", turns[t_to].get("start", 0)) or 0),
            "text": " ".join(parts),
            "words": words,
        })
        if j >= len(pieces):
            break
        # Sobreposicao: recua `overlap_turns` turnos inteiros, garantindo avanco.
        next_i = j
        if overlap_turns > 0 and len(group) > 1:
            back = j - 1
            turns_back = 0
            last_turn = None
            while back > i:
                if pieces[back][0] != last_turn:
                    last_turn = pieces[back][0]
                    turns_back += 1
                    if turns_back > overlap_turns:
                        break
                back -= 1
            candidate = back + 1 if back > i else i + 1
            next_i = max(i + 1, candidate)
        i = next_i
    return passages


def passage_scope_text(turns: list[dict[str, Any]], passage: dict[str, Any],
                       speakers: set[str] | None) -> str:
    """O texto da passagem restrito aos falantes escolhidos (puro).

    E ISTO que vai para o encoder quando o usuario tira alguem da analise
    (o entrevistador, o moderador): o trecho continua sendo mostrado,
    codificado e exportado INTEIRO — com a pergunta como contexto —, mas o
    vetor sai so da fala escolhida, para o roteiro de quem pergunta nao
    puxar trechos para o mesmo tema. `speakers=None` devolve o texto
    inteiro, identico ao de `build_passages`; nenhum falante escolhido
    dentro da passagem devolve "" (a passagem nao existe para os temas).

    Reproduz as regras de `build_passages`: rotulo so no primeiro pedaco
    de cada turno e recorte por c_from/c_to nos turnos longos partidos."""
    t_from, t_to = int(passage.get("t_from", 0)), int(passage.get("t_to", 0))
    c_from, c_to = int(passage.get("c_from", 0)), int(passage.get("c_to", -1))
    parts: list[str] = []
    for index in range(max(0, t_from), min(t_to, len(turns) - 1) + 1):
        turn = turns[index]
        label = _turn_label(turn)
        if speakers is not None and label not in speakers:
            continue
        text = " ".join(str(turn.get("text") or "").split())
        inicio = c_from if index == t_from else 0
        fim = c_to if (index == t_to and c_to >= 0) else len(text)
        piece = text[inicio:fim]
        if not piece:
            continue
        parts.append(f"{label}: {piece}" if (label and inicio == 0) else piece)
    return " ".join(parts)


def passage_overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Duas passagens da mesma entrevista compartilham algum turno?"""
    if str(a.get("interview_id")) != str(b.get("interview_id")):
        return False
    return not (int(a["t_to"]) < int(b["t_from"]) or int(b["t_to"]) < int(a["t_from"]))


def collapse_overlapping(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantem so o melhor entre hits que compartilham turnos (mesmo momento
    pontua nas duas passagens sobrepostas). Espera ordem decrescente."""
    kept: list[dict[str, Any]] = []
    for hit in hits:
        if any(passage_overlaps(hit, other) for other in kept):
            continue
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


def vectors_path(paths: Paths, interview_id: str) -> Path:
    return index_dir(paths) / f"{interview_id}.vectors.npy"


def index_is_fresh(paths: Paths, interview_id: str, model_repo: str | None = None) -> bool:
    source = source_path_for(paths, interview_id)
    target = index_path(paths, interview_id)
    if source is None or not target.exists() or not vectors_path(paths, interview_id).exists():
        return False
    try:
        payload = read_json(target)
    except Exception:  # noqa: BLE001 - indice corrompido = refazer
        return False
    repo = model_repo or active_encoder().repo
    if not (
        int(payload.get("version", -1)) == INDEX_VERSION
        and float(payload.get("source_mtime", -1)) == source.stat().st_mtime
        and str(payload.get("model")) == repo
    ):
        return False
    # O .npy tambem precisa ABRIR e ter uma linha por passagem: um arquivo
    # truncado (sincronizacao interrompida, disco cheio) continuava "fresco",
    # nao era refeito, e a entrevista sumia da busca e dos temas em silencio.
    # Leitura inteira (float16, poucas centenas de KB por entrevista) em vez
    # de mmap: no Windows um mapeamento vivo impediria o proprio
    # `write_index` de reescrever o arquivo logo em seguida.
    try:
        import numpy as np

        with vectors_path(paths, interview_id).open("rb") as handle:
            vectors = np.load(handle, allow_pickle=False)
        return vectors.ndim == 2 and vectors.shape[0] == len(payload.get("passages") or [])
    except Exception:  # noqa: BLE001 - ilegivel = refazer
        return False


def build_index_payload(
    interview_id: str,
    passages: list[dict[str, Any]],
    source_mtime: float,
    model_repo: str,
    dim: int,
) -> dict[str, Any]:
    """Payload puro do indice v3 (metadados; vetores vao no .npy)."""
    return {
        "version": INDEX_VERSION,
        "model": model_repo,
        "interview_id": interview_id,
        "source_mtime": float(source_mtime),
        "dim": int(dim),
        "passages": [
            {
                "p": int(p["p"]),
                "t_from": int(p["t_from"]),
                "t_to": int(p["t_to"]),
                "c_from": int(p.get("c_from", 0)),
                "c_to": int(p.get("c_to", 0)),
                "start": float(p.get("start", 0) or 0),
                "end": float(p.get("end", 0) or 0),
                "text": str(p.get("text") or ""),
            }
            for p in passages
        ],
    }


def write_index(paths: Paths, interview_id: str, payload: dict[str, Any], vectors) -> None:
    """JSON + .npy escritos DIRETO (sem temp/rename — regra Dropbox)."""
    import numpy as np

    target = index_path(paths, interview_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(vectors, dtype=np.float16)
    with vectors_path(paths, interview_id).open("wb") as handle:
        np.save(handle, array, allow_pickle=False)
    write_json(target, payload)


def read_index(paths: Paths, interview_id: str) -> tuple[dict[str, Any], Any]:
    """(payload, vetores float32 [n, dim]); levanta se faltar/corromper."""
    import numpy as np

    payload = read_json(index_path(paths, interview_id))
    with vectors_path(paths, interview_id).open("rb") as handle:
        vectors = np.load(handle, allow_pickle=False)
    vectors = np.asarray(vectors, dtype=np.float32)
    n = len(payload.get("passages") or [])
    if vectors.ndim != 2 or vectors.shape[0] != n:
        raise ValueError(f"indice de {interview_id}: {vectors.shape} vetores para {n} passagens")
    return payload, vectors


# ---------------------------------------------------------------------------
# Encoder (transformers do proprio app; CPU ok, GPU se houver)
# ---------------------------------------------------------------------------

_ENCODER_CACHE: dict[str, Any] = {}


def encoder_cached(key: str | None = None) -> bool:
    """O modelo do encoder (leve por padrao) esta no cache do app?"""
    from . import model_manager, runtime

    spec = ENCODERS[key or DEFAULT_ENCODER_KEY]
    try:
        return model_manager.cached_snapshot_path(spec.repo, runtime.model_cache_dir()) is not None
    except Exception:  # noqa: BLE001
        return False


def active_encoder() -> EncoderSpec:
    """Instalado => aplicado: o de qualidade quando esta no cache; senao o leve."""
    if encoder_cached("search_encoder_hq"):
        return ENCODERS["search_encoder_hq"]
    return ENCODERS[DEFAULT_ENCODER_KEY]


def _tokenizer_canary(tokenizer, spec: EncoderSpec) -> None:
    """Falha ALTO se o tokenizer virou <unk> (transformers 5 carregava o
    tokenizer lento errado e o indice viraria ruido em silencio)."""
    ids = tokenizer("Você autoriza que essa entrevista seja gravada?")["input_ids"]
    unk = getattr(tokenizer, "unk_token_id", None)
    if unk is not None and sum(1 for i in ids if i == unk) >= 2:
        raise RuntimeError(
            f"tokenizer de {spec.repo} nao reconhece portugues (<unk>) — "
            "versao do transformers incompativel")


def load_encoder(key: str | None = None):
    """Encoder carregado UMA vez por processo: (tokenizer, model, device, spec)."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    from . import runtime

    spec = ENCODERS[key] if key else active_encoder()
    cached = _ENCODER_CACHE.get(spec.key)
    if cached is not None:
        return cached
    cache = str(runtime.model_cache_dir())
    tokenizer = AutoTokenizer.from_pretrained(spec.repo, cache_dir=cache, local_files_only=True)
    _tokenizer_canary(tokenizer, spec)
    model = AutoModel.from_pretrained(spec.repo, cache_dir=cache, local_files_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    encoder = (tokenizer, model, device, spec)
    _ENCODER_CACHE[spec.key] = encoder
    return encoder


def embed_texts(texts: list[str], encoder, kind: str = "passage"):
    """Vetores normalizados (float32 [n, dim]) com o prefixo do encoder.

    kind: "passage" (indice) ou "query" (consulta) — o e5 exige os dois
    prefixos; o granite nao usa nenhum.
    """
    import numpy as np
    import torch

    tokenizer, model, device, spec = encoder
    prefix = spec.query_prefix if kind == "query" else spec.passage_prefix
    rows = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [prefix + text for text in texts[start:start + EMBED_BATCH]]
        with torch.no_grad():
            enc = tokenizer(batch, padding=True, truncation=True, max_length=spec.max_length,
                            return_tensors="pt").to(device)
            hidden = model(**enc).last_hidden_state
            if spec.pooling == "cls":
                pooled = hidden[:, 0]
            else:
                mask = enc["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled.float(), dim=1)
        rows.append(pooled.cpu().numpy())
    if not rows:
        return np.zeros((0, 0), dtype=np.float32)
    return np.concatenate(rows, axis=0).astype(np.float32)


def build_indexes(
    paths: Paths,
    interview_ids: list[str],
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """(Re)constroi os indices DESATUALIZADOS com o encoder ativo; retorna falhas."""
    spec = active_encoder()
    stale = [iid for iid in interview_ids if not index_is_fresh(paths, iid, spec.repo)]
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
                    f"Lendo {interview_id} com o modelo de busca por sentido "
                    f"({position + 1}/{len(stale)})..."
                ),
            })
        try:
            source = source_path_for(paths, interview_id)
            turns = load_source_turns(paths, interview_id)
            if source is None or not turns:
                continue
            if encoder is None:
                encoder = load_encoder(spec.key)
            passages = build_passages(turns)
            vectors = embed_texts([p["text"] for p in passages], encoder, kind="passage")
            payload = build_index_payload(
                interview_id, passages, source.stat().st_mtime, spec.repo,
                int(vectors.shape[1]) if vectors.size else 0)
            write_index(paths, interview_id, payload, vectors)
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
    query_vector,
    indexes: list[tuple[dict[str, Any], Any]],
    exclude: set[tuple[str, int]] = frozenset(),
    min_similarity: float = MIN_SIMILARITY,
    top_n: int = TOP_N,
    per_interview_cap: int | None = MAX_PASSAGES_PER_INTERVIEW,
) -> list[dict[str, Any]]:
    """Ranqueia passagens por cosseno (puro com numpy, testavel).

    indexes: [(payload, vetores)]; exclude: turnos (interview_id, t) que nao
    podem aparecer (ex.: hits ja exatos). Cada hit traz `similarity` e `z`
    (desvios acima da media do escopo — a base do corte de relevancia,
    porque os cossenos de um encoder de recuperacao vivem comprimidos em
    0,7-0,9 e um piso absoluto nao separa nada). Sobrepostas colapsam e,
    com mais de uma entrevista no escopo, cada uma leva no maximo
    per_interview_cap passagens.
    """
    import numpy as np

    q = np.asarray(query_vector, dtype=np.float32).reshape(-1)
    scored: list[dict[str, Any]] = []
    all_sims: list[float] = []
    for payload, vectors in indexes:
        interview_id = str(payload.get("interview_id"))
        passages = payload.get("passages") or []
        if vectors is None or len(passages) == 0:
            continue
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(passages) or matrix.shape[1] != q.shape[0]:
            continue
        sims = matrix @ q
        all_sims.extend(float(s) for s in sims)
        for entry, similarity in zip(passages, sims):
            t_from, t_to = int(entry.get("t_from", -1)), int(entry.get("t_to", -1))
            if exclude and any((interview_id, t) in exclude for t in range(t_from, t_to + 1)):
                continue
            if float(similarity) < min_similarity:
                continue
            scored.append({
                "interview_id": interview_id,
                "p": int(entry.get("p", -1)),
                "t_from": t_from,
                "t_to": t_to,
                "turn_index": t_from,  # compat: chamadores antigos abrem pelo turno
                "start": float(entry.get("start", 0) or 0),
                "end": float(entry.get("end", 0) or 0),
                "label": "",
                "text": str(entry.get("text") or ""),
                "similarity": round(float(similarity), 4),
            })
    if not scored:
        return []
    mean = float(np.mean(all_sims)) if all_sims else 0.0
    std = float(np.std(all_sims)) if len(all_sims) > 1 else 0.0
    for hit in scored:
        hit["z"] = round((hit["similarity"] - mean) / std, 2) if std > 1e-6 else 0.0
    scored.sort(key=lambda item: -item["similarity"])
    collapsed = collapse_overlapping(scored)
    if per_interview_cap and len(indexes) > 1:
        counts: dict[str, int] = {}
        limited: list[dict[str, Any]] = []
        for hit in collapsed:
            n = counts.get(hit["interview_id"], 0)
            if n >= per_interview_cap:
                continue
            counts[hit["interview_id"]] = n + 1
            limited.append(hit)
        collapsed = limited
    return collapsed[:top_n]


# ---------------------------------------------------------------------------
# Reordenador (cross-encoder), hibrido literal+vetor e corte de relevancia
# ---------------------------------------------------------------------------

RERANKER_KEY = "search_reranker"
RERANK_CANDIDATES_GPU = 30
RERANK_CANDIDATES_CPU = 20
RERANK_MAX_LENGTH = 512
# Limiares do logit do bge-reranker-v2-m3, calibrados no gabarito de
# 2026-09-03 (12 consultas, 360 candidatos julgados): relevantes tem
# mediana -2,8 (p90 +0,5), irrelevantes mediana -6,7 (p90 -3,2). Corte em
# -5 mantem recall 0,71 com precisao 0,57 e nenhuma consulta vazia; acima
# de -2 a precisao e 0,73 ("Respondem"). A pergunta global ("do que falam
# as entrevistas?") ficou toda abaixo de -2,5 -> nada "responde".
RERANK_ANSWERS = -2.0
RERANK_RELATED = -5.0
# Sem reordenador: z-score relativo ao escopo (rank_semantic).
Z_STRONG = 3.0
Z_RELATED = 2.0
RRF_K = 60

_RERANKER_CACHE: dict[str, Any] = {}


def reranker_cached() -> bool:
    from . import model_manager, runtime

    try:
        asset = model_manager.optional_model(RERANKER_KEY)
        return model_manager.cached_snapshot_path(asset.repo_id, runtime.model_cache_dir()) is not None
    except Exception:  # noqa: BLE001
        return False


def load_reranker():
    """(tokenizer, model, device) do cross-encoder, carregado uma vez por processo."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from . import model_manager, runtime

    cached = _RERANKER_CACHE.get(RERANKER_KEY)
    if cached is not None:
        return cached
    asset = model_manager.optional_model(RERANKER_KEY)
    cache = str(runtime.model_cache_dir())
    tokenizer = AutoTokenizer.from_pretrained(asset.repo_id, cache_dir=cache, local_files_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    kwargs = {"dtype": torch.float16} if device.type == "cuda" else {}
    model = AutoModelForSequenceClassification.from_pretrained(
        asset.repo_id, cache_dir=cache, local_files_only=True, **kwargs)
    model.to(device)
    model.eval()
    reranker = (tokenizer, model, device)
    _RERANKER_CACHE[RERANKER_KEY] = reranker
    return reranker


class RerankCancelled(Exception):
    """Cancelamento pedido durante a reordenacao: quem chama mostra a lista
    por semelhanca (ja calculada) em vez de esperar."""


def rerank_hits(query: str, hits: list[dict[str, Any]], reranker, batch_size: int = 8,
                should_cancel: Callable[[], bool] | None = None) -> list[dict[str, Any]]:
    """Le (pergunta, passagem) juntos e grava `score` (logit) em cada hit;
    devolve os hits em ordem decrescente de score. Levanta RerankCancelled
    entre lotes quando should_cancel() vira True."""
    import torch

    tokenizer, model, device = reranker
    scores: list[float] = []
    for start in range(0, len(hits), batch_size):
        if should_cancel is not None and should_cancel():
            raise RerankCancelled()
        pairs = [[query, str(hit.get("text") or "")] for hit in hits[start:start + batch_size]]
        with torch.no_grad():
            enc = tokenizer(pairs, padding=True, truncation=True, max_length=RERANK_MAX_LENGTH,
                            return_tensors="pt").to(device)
            logits = model(**enc, return_dict=True).logits.view(-1).float().cpu().tolist()
        scores.extend(logits)
    for hit, score in zip(hits, scores):
        hit["score"] = round(float(score), 3)
    return sorted(hits, key=lambda item: -item["score"])


def rrf_fuse(*ranked_lists: list[dict[str, Any]], k: int = RRF_K) -> list[dict[str, Any]]:
    """Fusao por posicao (Reciprocal Rank Fusion) de listas de hits (pura).

    Identidade do hit = (interview_id, t_from, t_to). Cada lista contribui
    1/(k + posicao); o hit devolvido e o do primeiro encontro, com `rrf`.
    """
    scores: dict[tuple[str, int, int], float] = {}
    first: dict[tuple[str, int, int], dict[str, Any]] = {}
    for ranked in ranked_lists:
        for position, hit in enumerate(ranked, start=1):
            key = (str(hit.get("interview_id")), int(hit.get("t_from", -1)), int(hit.get("t_to", -1)))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position)
            first.setdefault(key, hit)
    fused = []
    for key, value in sorted(scores.items(), key=lambda item: -item[1]):
        hit = dict(first[key])
        hit["rrf"] = round(value, 6)
        fused.append(hit)
    return fused


def literal_passage_hits(
    indexes: list[tuple[dict[str, Any], Any]], query: str,
) -> list[dict[str, Any]]:
    """Passagens que contem o termo LITERAL (acertos exatos como candidatos
    do hibrido), na ordem das entrevistas do escopo."""
    query_norm = normalize(query)
    if len(query_norm) < 3:
        return []
    hits: list[dict[str, Any]] = []
    for payload, _vectors in indexes:
        interview_id = str(payload.get("interview_id"))
        for entry in payload.get("passages") or []:
            if query_norm in normalize(str(entry.get("text") or "")):
                hits.append({
                    "interview_id": interview_id,
                    "p": int(entry.get("p", -1)),
                    "t_from": int(entry.get("t_from", -1)),
                    "t_to": int(entry.get("t_to", -1)),
                    "turn_index": int(entry.get("t_from", -1)),
                    "start": float(entry.get("start", 0) or 0),
                    "end": float(entry.get("end", 0) or 0),
                    "label": "",
                    "text": str(entry.get("text") or ""),
                    "similarity": 0.0,
                    "z": 0.0,
                    "literal": True,
                })
    return hits


def relevance_sections(hits: list[dict[str, Any]], reranked: bool) -> list[dict[str, Any]]:
    """Agrupa hits ja ordenados em secoes com rotulo (pura; UI sem numeros).

    Com reordenador (score = logit): "Respondem" (>= RERANK_ANSWERS),
    "Relacionados" (>= RERANK_RELATED); abaixo disso o trecho SOME — nao
    trata do tema. Sem reordenador: por z relativo ao escopo, "Muito
    proximos" (>= Z_STRONG), "Proximos" (>= Z_RELATED), "Relacionados".
    Quando nada chega a "responder" (ou a "proximo"), a primeira secao
    ganha `weak=True` para a UI dizer que nada e realmente proximo.
    Devolve [{"key", "label", "weak", "hits"}] sem secoes vazias.
    """
    groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    if reranked:
        answers = [h for h in hits if float(h.get("score", -99)) >= RERANK_ANSWERS]
        related = [h for h in hits if RERANK_RELATED <= float(h.get("score", -99)) < RERANK_ANSWERS]
        groups = [("responde", "Respondem", answers), ("relacionado", "Relacionados", related)]
    else:
        strong = [h for h in hits if float(h.get("z", 0)) >= Z_STRONG]
        near = [h for h in hits if Z_RELATED <= float(h.get("z", 0)) < Z_STRONG]
        rest = [h for h in hits if float(h.get("z", 0)) < Z_RELATED]
        groups = [("muito_proximo", "Muito próximos", strong), ("proximo", "Próximos", near),
                  ("relacionado", "Relacionados", rest)]
    sections = [{"key": key, "label": label, "weak": False, "hits": items}
                for key, label, items in groups if items]
    if sections and sections[0]["key"] == "relacionado":
        sections[0]["weak"] = True
    return sections


def search_passages(
    paths: Paths,
    interview_ids: list[str],
    query: str,
    max_results: int = TOP_N,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    use_reranker: bool | None = None,
) -> dict[str, Any]:
    """Busca completa da janela Perguntar: vetor + literal (RRF) + reordenador
    quando instalado + corte de relevancia + teto por entrevista.

    Devolve {"hits": [...], "sections": [...], "reranked": bool,
    "considered": n, "max_results": max_results}. `hits` sao SO os que
    tratam do tema (pode ser menos que max_results); a UI diz quantos
    ficaram de fora.
    """
    import torch

    def emit(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": "search_progress", "progress": progress, "message": message})

    if encoder_cached(active_encoder().key):
        build_indexes(paths, interview_ids, progress_callback=progress_callback,
                      should_cancel=should_cancel)
    indexes = load_indexes(paths, interview_ids)
    empty = {"hits": [], "sections": [], "reranked": False, "considered": 0, "max_results": max_results}
    if not indexes:
        return empty
    emit(60, "Procurando trechos pelo sentido...")
    encoder = load_encoder()
    query_vector = embed_texts([query], encoder, kind="query")[0]
    reranked = reranker_cached() if use_reranker is None else bool(use_reranker)
    on_gpu = torch.cuda.is_available()
    n_candidates = (RERANK_CANDIDATES_GPU if on_gpu else RERANK_CANDIDATES_CPU) if reranked else max_results * 2
    dense = rank_semantic(query_vector, indexes, top_n=n_candidates, per_interview_cap=None)
    literal = literal_passage_hits(indexes, query)
    candidates = rrf_fuse(dense, literal) if literal else dense
    # z do vetor vale para todos (o literal nao tem): recuperar do denso.
    z_by_key = {(h["interview_id"], h["t_from"], h["t_to"]): (h.get("similarity", 0.0), h.get("z", 0.0)) for h in dense}
    for hit in candidates:
        sim, z = z_by_key.get((hit["interview_id"], hit["t_from"], hit["t_to"]), (hit.get("similarity", 0.0), hit.get("z", 0.0)))
        hit["similarity"], hit["z"] = sim, z
    candidates = candidates[:n_candidates]
    cancelled = False
    if reranked and candidates:
        emit(75, "Lendo pergunta e trechos juntos (reordenador)...")
        try:
            candidates = rerank_hits(query, candidates, load_reranker(), should_cancel=should_cancel)
        except RerankCancelled:
            # Cancelar devolve a lista por semelhanca, sem esperar.
            cancelled = True
            reranked = False
            candidates.sort(key=lambda h: -float(h.get("similarity", 0)))
        except Exception as exc:  # noqa: BLE001 - reordenador e refinamento, nunca bloqueio
            print(f"Reordenador indisponivel: {exc}")
            reranked = False
            candidates.sort(key=lambda h: -float(h.get("similarity", 0)))
    else:
        candidates.sort(key=lambda h: -float(h.get("similarity", 0)))
    candidates = collapse_overlapping(candidates)
    sections = relevance_sections(candidates, reranked)
    kept: list[dict[str, Any]] = []
    for section in sections:
        kept.extend(section["hits"])
    # Teto por entrevista cresce com N (max(3, N/10)): "quem falou de
    # pagamento?" em 99 entrevistas nao pode ficar preso a 3 por arquivo.
    cap = max(int(MAX_PASSAGES_PER_INTERVIEW or 0), max_results // 10) if MAX_PASSAGES_PER_INTERVIEW else 0
    if len(indexes) > 1 and cap:
        counts: dict[str, int] = {}
        limited = []
        for hit in kept:
            n = counts.get(hit["interview_id"], 0)
            if n >= cap:
                continue
            counts[hit["interview_id"]] = n + 1
            limited.append(hit)
        kept = limited
    kept = kept[:max_results]
    keep_keys = {(h["interview_id"], h["t_from"], h["t_to"]) for h in kept}
    final_sections = []
    for section in sections:
        items = [h for h in section["hits"] if (h["interview_id"], h["t_from"], h["t_to"]) in keep_keys]
        if items:
            final_sections.append(dict(section, hits=items))
    emit(100, "Trechos encontrados.")
    return {"hits": kept, "sections": final_sections, "reranked": reranked,
            "considered": len(candidates), "max_results": max_results,
            "rerank_cancelled": cancelled}


def load_indexes(paths: Paths, interview_ids: list[str]) -> list[tuple[dict[str, Any], Any]]:
    """Indices FRESCOS do escopo (os demais ficam de fora — ver build_indexes)."""
    spec = active_encoder()
    indexes = []
    for interview_id in interview_ids:
        if not index_is_fresh(paths, interview_id, spec.repo):
            continue
        try:
            indexes.append(read_index(paths, interview_id))
        except Exception as exc:  # noqa: BLE001 - indice corrompido: refeito na proxima
            print(f"Indice de {interview_id} ilegivel: {exc}")
    return indexes


def project_semantic_search(
    paths: Paths,
    interview_ids: list[str],
    query: str,
    exclude: set[tuple[str, int]] = frozenset(),
    min_similarity: float = MIN_SIMILARITY,
    top_n: int = TOP_N,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    per_interview_cap: int | None = MAX_PASSAGES_PER_INTERVIEW,
) -> list[dict[str, Any]]:
    """Consulta por sentido no escopo.

    Indices desatualizados sao REFEITOS antes (com progresso) em vez de
    pulados em silencio — a entrevista que o pesquisador acabou de editar
    e a que ele mais procura. Sem o encoder no cache, so os frescos entram.
    """
    if encoder_cached(active_encoder().key):
        build_indexes(paths, interview_ids, progress_callback=progress_callback,
                      should_cancel=should_cancel)
    indexes = load_indexes(paths, interview_ids)
    if not indexes:
        return []
    encoder = load_encoder()
    query_vector = embed_texts([query], encoder, kind="query")[0]
    return rank_semantic(query_vector, indexes, exclude=exclude,
                         min_similarity=min_similarity, top_n=top_n,
                         per_interview_cap=per_interview_cap)
