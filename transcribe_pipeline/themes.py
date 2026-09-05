"""Temas das entrevistas (Parte B, 2026-09-03): agrupar as passagens da busca.

Materia-prima: as passagens ja vetorizadas pelo indice da busca por sentido
(search.py, v3) — escopo inteiro, sem teto por entrevista. Agrupamento por
semelhanca em numpy (micro-grupos por k-means++ ligados dois a dois ate a
quantidade pedida, ruido de fora, e PERTENCIMENTO MULTIPLO: uma passagem
entra em todo tema de que esta perto, porque trechos nao sao mutuamente
excludentes — decisao do usuario). Roda em qualquer maquina, em segundos.

Nomes: sem modelo de analise, termos caracteristicos (c-TF-IDF) — nomes
crus, mas imediatos; com o modelo de analise (GPU), o Qwen nomeia e descreve
cada tema a partir das passagens centrais (llm_worker, tarefa nomear_temas),
sempre em segundo plano e substituindo os rotulos ao terminar. A LLM nunca
agrupa; o encoder nunca nomeia (regra de papeis do plano de camadas).

Persistencia: Transcricoes/07_index/temas_projeto.json (JSON direto).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import search
from .config import Paths
from .utils import read_json, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

MIN_THEME_PASSAGES = 3          # tema com menos que isto vira "Outros"
# Fala escolhida menor que isto nao caracteriza o trecho. 10 e o MENOR valor
# que funciona, medido na copia de 10 entrevistas reais: com piso 0, 3, 5 ou 8
# as respostas de cortesia ao roteiro ("tá bom", "autorizo", "eu te agradeço")
# ainda se juntam num tema proprio de 39 a 54 trechos em 9-10 entrevistas; a
# partir de 10 ele some. Mexer aqui exige medir de novo — nao e chute.
MIN_SCOPE_WORDS = 10
MEMBERSHIP_SLACK = 0.03         # passagem entra em todo tema a ate isto do seu melhor
MEMBERSHIP_FLOOR = 0.0          # piso absoluto (o piso real e relativo aos dados)
OUTLIER_SIGMAS = 1.5            # abaixo de media - 1,5 desvio = isolado (ruido / "Outros")
TOP_TERMS = 4
CENTRAL_PASSAGES = 8

# Stopwords de fala em pt-BR + rotulos de falante. Curta de proposito: o
# c-TF-IDF ja rebaixa o que aparece em todo tema.
_STOP = set("""
a o os as um uma uns umas de do da dos das em no na nos nas por para pra pro com sem sob sobre
e ou mas que se nao não sim ne né ta tá tava tinha tem ter era foi ser sao são eu tu ele ela eles
elas nos nós voce você voces vocês meu minha meus minhas seu sua seus suas dele dela deles delas
isso isto aquilo esse essa esses essas este esta estes estas aquele aquela aqui ali la lá ai aí
entao então assim tipo muito muita muitos muitas mais menos bem mal ja já ainda sempre nunca tambem
também ate até so só como quando onde porque porquê pois cada todo toda todos todas outro outra
outros outras mesmo mesma mesmos mesmas gente pessoa pessoas coisa coisas vez vezes dia dias
falar fala falou falei disse dizer fazer fez fazia feito ir ia vai foi vou ver via vi olha
uhum aham eh ah oh hum né tá bom certo entendi entendeu sabe sei acho acha
entrevistador entrevistada entrevistado entrevistadora speaker unknown
""".split())


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


_WORD = re.compile(r"[a-zà-ÿ]{4,}", re.IGNORECASE)


def tokens(text: str) -> list[str]:
    """Palavras de 4+ letras, sem acentos/caixa, sem stopwords e sem rotulos (puro)."""
    out = []
    for word in _WORD.findall(str(text or "")):
        folded = _fold(word)
        if folded in _STOP or len(folded) < 4:
            continue
        out.append(folded)
    return out


# ---------------------------------------------------------------------------
# Agrupamento (numpy)
# ---------------------------------------------------------------------------

def _kmeans(vectors, k: int, iterations: int = 25, seed: int = 0):
    """k-means++ em vetores normalizados (cosseno = produto interno). Devolve
    (labels, centros normalizados)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = vectors.shape[0]
    k = max(1, min(k, n))
    # k-means++
    centers = [vectors[rng.integers(n)]]
    for _ in range(1, k):
        sims = np.max(np.stack([vectors @ c for c in centers], axis=1), axis=1)
        dist = np.clip(1.0 - sims, 0.0, None) ** 2
        total = float(dist.sum())
        if total <= 1e-12:
            centers.append(vectors[rng.integers(n)])
            continue
        centers.append(vectors[rng.choice(n, p=dist / total)])
    C = np.stack(centers)
    labels = np.zeros(n, dtype=int)
    for _ in range(iterations):
        sims = vectors @ C.T
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels) and _ > 0:
            break
        labels = new_labels
        for j in range(k):
            members = vectors[labels == j]
            if len(members):
                c = members.mean(axis=0)
                C[j] = c / (np.linalg.norm(c) + 1e-9)
    return labels, C


def default_k(n_passages: int) -> int:
    """Quantidade de temas sugerida: ~raiz(n/2), entre 6 e 40 (puro). O
    usuario ajusta ("mais temas / menos temas")."""
    return int(max(6, min(40, round(math.sqrt(max(1, n_passages) / 2.0)))))


def _relative_floor(values, sigmas: float = OUTLIER_SIGMAS) -> float:
    """Piso relativo aos dados: media menos `sigmas` desvios (puro). Vale
    para qualquer encoder — cossenos comprimidos (e5: 0,75-0,95) ou nao."""
    import numpy as np

    arr = np.asarray(values, dtype=np.float32)
    if arr.size < 3:
        return -1.0
    return float(arr.mean() - sigmas * arr.std())


def _center(V):
    """Tira a direcao comum (media) e renormaliza (puro). Encoders como o
    e5 poem todo texto num cone estreito (cossenos 0,75-0,95 entre
    qualquer par); sem isto, um grupo grande vira um "ima" que engole os
    outros na ligacao, porque a media de muitos vetores e parecida com tudo."""
    import numpy as np

    X = V - V.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(norms, 1e-6)


def _agglomerate(C, weights, n_target: int):
    """Ligacao media (UPGMA) entre micro-grupos ate sobrarem n_target:
    a semelhanca entre dois grupos e a MEDIA ponderada das semelhancas
    entre seus micro-centros (Lance-Williams), nao o cosseno dos centros
    fundidos — assim um grupo grande nao fica "parecido com tudo".
    Devolve (centros ponderados, pesos). Puro (numpy); k_micro <= 200."""
    import numpy as np

    C = np.asarray(C, dtype=np.float32).copy()
    W = np.asarray(weights, dtype=np.float32).copy()
    S = C @ C.T
    while C.shape[0] > max(1, n_target):
        np.fill_diagonal(S, -2.0)
        i, j = np.unravel_index(int(np.argmax(S)), S.shape)
        wi, wj = float(W[i]), float(W[j])
        row = (S[i] * wi + S[j] * wj) / (wi + wj)
        keep = [r for r in range(C.shape[0]) if r not in (i, j)]
        merged = C[i] * wi + C[j] * wj
        merged = merged / (np.linalg.norm(merged) + 1e-9)
        C = np.vstack([C[keep], merged[None, :]]) if keep else merged[None, :]
        W = np.concatenate([W[keep], [wi + wj]])
        row_kept = row[keep]
        S = S[np.ix_(keep, keep)]
        S = np.pad(S, ((0, 1), (0, 1)))
        S[-1, :-1] = row_kept
        S[:-1, -1] = row_kept
        S[-1, -1] = -2.0
    return C, W


def cluster_vectors(vectors, n_themes: int | None = None,
                    slack: float = MEMBERSHIP_SLACK, floor: float = MEMBERSHIP_FLOOR):
    """Agrupa vetores normalizados em ate n_themes temas.

    Passos: (1) micro-agrupamento (k-means++ com k folgado, para nao
    depender de um k certo de primeira); (2) micro-grupos isolados — vizinho
    mais proximo muito abaixo do tipico — sao ruido e ficam de fora, para
    nao ocupar vaga de tema; (3) ligacao dos demais, sempre o par mais
    parecido, ate o numero pedido (aglomerativo; barato porque sao <= 200
    centros); (4) grupos com menos que o minimo de passagens nao viram
    tema; (5) PERTENCIMENTO MULTIPLO: cada vetor entra em todo tema a ate
    `slack` do seu melhor, desde que acima do piso relativo (quem fica
    abaixo vai para "Outros"). Devolve (centros, membros) com membros[j] =
    [(indice, cosseno)...] em ordem decrescente. Puro (numpy)."""
    import numpy as np

    V = np.asarray(vectors, dtype=np.float32)
    if V.ndim != 2 or V.shape[0] == 0:
        return np.zeros((0, 0), dtype=np.float32), []
    n = V.shape[0]
    if n >= 3:
        V = _center(V)
    n_themes = int(n_themes or default_k(n))
    k_micro = int(max(n_themes, min(200, max(n_themes * 4, n // 4))))
    k_micro = max(1, min(k_micro, n))
    labels, micro = _kmeans(V, k_micro)
    weights = np.array([float((labels == j).sum()) for j in range(micro.shape[0])], dtype=np.float32)
    keep = weights > 0
    micro, weights = micro[keep], weights[keep]
    if micro.shape[0] > 2:
        nn = micro @ micro.T
        np.fill_diagonal(nn, -2.0)
        nearest = nn.max(axis=1)
        connected = nearest >= _relative_floor(nearest)
        if int(connected.sum()) >= 2:
            micro, weights = micro[connected], weights[connected]
    C, W = _agglomerate(micro, weights, n_themes)
    big = W >= MIN_THEME_PASSAGES
    if int(big.sum()) >= 1:
        C = C[big]
    return C, assign_members(V, C, slack=slack, floor=floor)


def assign_members(V, C, slack: float = MEMBERSHIP_SLACK, floor: float = MEMBERSHIP_FLOOR):
    """Pertencimento multiplo (puro): cada vetor entra em todo tema a ate
    `slack` do seu melhor cosseno, desde que acima do piso (o maior entre
    o absoluto e o relativo aos dados). Devolve membros[j] = [(indice,
    cosseno)...] em ordem decrescente."""
    import numpy as np

    V = np.asarray(V, dtype=np.float32)
    C = np.asarray(C, dtype=np.float32)
    if V.ndim != 2 or C.ndim != 2 or V.shape[0] == 0 or C.shape[0] == 0:
        return []
    sims = V @ C.T                       # n x k
    best = sims.max(axis=1)
    effective_floor = max(float(floor), _relative_floor(best))
    members: list[list[tuple[int, float]]] = [[] for _ in range(C.shape[0])]
    for idx in range(V.shape[0]):
        for j in range(C.shape[0]):
            s = float(sims[idx, j])
            if s >= float(best[idx]) - slack and s >= effective_floor:
                members[j].append((idx, s))
    for j in range(len(members)):
        members[j].sort(key=lambda item: -item[1])
    return members


# ---------------------------------------------------------------------------
# Nomes por termos (c-TF-IDF) e montagem dos temas
# ---------------------------------------------------------------------------

def ctfidf_terms(cluster_docs: list[list[str]], top: int = TOP_TERMS) -> list[list[str]]:
    """Termos caracteristicos por tema (c-TF-IDF, BERTopic-lite; puro).
    cluster_docs[j] = tokens de todas as passagens do tema j.

    O denominador do idf e a frequencia TOTAL do termo (em tokens, somando
    todos os temas), como no BERTopic — nao o numero de temas em que ele
    aparece. Com a contagem de temas, numerador e denominador ficam em
    unidades diferentes: o log passa a ser dominado por log(avg_len) e, em
    temas grandes (centenas de passagens), a palavra generica comum a todos
    ganha da palavra propria do tema."""
    if not cluster_docs:
        return []
    global_freq: dict[str, int] = {}
    tfs: list[dict[str, int]] = []
    for toks in cluster_docs:
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        tfs.append(tf)
        for t, c in tf.items():
            global_freq[t] = global_freq.get(t, 0) + c
    n_clusters = len(cluster_docs)
    avg_len = max(1.0, sum(sum(tf.values()) for tf in tfs) / n_clusters)
    result = []
    for tf in tfs:
        total = max(1, sum(tf.values()))
        scored = []
        for t, c in tf.items():
            idf = math.log(1.0 + avg_len / max(1, global_freq[t]))
            scored.append((c / total * idf, t))
        scored.sort(reverse=True)
        result.append([t for _score, t in scored[:top]])
    return result


def build_themes(passages: list[dict[str, Any]], vectors, n_themes: int | None = None,
                 min_passages: int = MIN_THEME_PASSAGES) -> dict[str, Any]:
    """Temas a partir de passagens ({interview_id, t_from, t_to, start, text})
    e seus vetores. Devolve {"themes": [...], "outros": [...]} com cada tema:
    {id, name, terms, description, n_passages, n_interviews, passages:
    [{interview_id, t_from, t_to, start, end, text, similarity}]}. Puro."""
    import numpy as np

    V = np.asarray(vectors, dtype=np.float32)
    if len(passages) == 0 or V.shape[0] != len(passages):
        return {"themes": [], "outros": []}
    _centers, members = cluster_vectors(V, n_themes=n_themes)
    themes: list[dict[str, Any]] = []
    covered: set[int] = set()
    docs = [tokens(p.get("text", "")) for p in passages]
    for j, member_list in enumerate(members):
        interviews = {passages[i]["interview_id"] for i, _ in member_list}
        if len(member_list) < min_passages or len(interviews) < 2 and len(member_list) < 2 * min_passages:
            continue
        items = []
        for i, s in member_list:
            p = passages[i]
            items.append({
                "interview_id": p["interview_id"], "t_from": int(p["t_from"]), "t_to": int(p["t_to"]),
                "start": float(p.get("start", 0) or 0), "end": float(p.get("end", 0) or 0),
                "text": str(p.get("text") or ""), "similarity": round(float(s), 4),
            })
            covered.add(i)
        themes.append({
            "id": f"tema_{j + 1:03d}", "name": "", "terms": [], "description": "",
            "n_passages": len(items), "n_interviews": len(interviews), "passages": items,
            "_tokens": [t for i, _ in member_list for t in docs[i]],
        })
    terms = ctfidf_terms([t["_tokens"] for t in themes])
    for theme, term_list in zip(themes, terms):
        theme["terms"] = term_list
        theme["name"] = ", ".join(term_list[:3]) if term_list else theme["id"]
        theme["description"] = theme["passages"][0]["text"][:160] if theme["passages"] else ""
        theme["name_source"] = "termos"
        del theme["_tokens"]
    themes.sort(key=lambda t: (-t["n_interviews"], -t["n_passages"]))
    for n, theme in enumerate(themes, start=1):
        theme["id"] = f"tema_{n:03d}"
    outros = [
        {"interview_id": p["interview_id"], "t_from": int(p["t_from"]), "t_to": int(p["t_to"]),
         "start": float(p.get("start", 0) or 0), "text": str(p.get("text") or "")}
        for i, p in enumerate(passages) if i not in covered
    ]
    return {"themes": themes, "outros": outros}


# ---------------------------------------------------------------------------
# Projeto: coletar passagens, descobrir, persistir
# ---------------------------------------------------------------------------

def themes_path(paths: Paths) -> Path:
    return search.index_dir(paths) / "temas_projeto.json"


def scope_cache_path(paths: Paths) -> Path:
    return search.index_dir(paths) / "temas_escopo.npy"


def scope_signature_path(paths: Paths) -> Path:
    return search.index_dir(paths) / "temas_escopo.json"


def _scope_signature(paths: Paths, interview_ids: list[str], speakers: list[str], n: int) -> dict[str, Any]:
    """Assinatura do cache dos vetores por falante: modelo, escolha de
    falantes e a data de cada transcricao. Qualquer mudanca invalida — sem
    isto, trocar quem entra daria resultado silenciosamente errado (vetores
    velhos com filtro novo)."""
    fontes = []
    for iid in interview_ids:
        source = search.source_path_for(paths, iid)
        fontes.append([iid, source.stat().st_mtime if source is not None else 0.0])
    return {"model": search.active_encoder().repo, "speakers": sorted(speakers),
            "sources": fontes, "n_passages": int(n), "version": search.INDEX_VERSION}


def collect_passages(paths: Paths, interview_ids: list[str],
                     progress_callback: ProgressCallback | None = None,
                     should_cancel: Callable[[], bool] | None = None,
                     speakers: list[str] | None = None):
    """(passagens, vetores, descartadas) dos indices FRESCOS do escopo
    (indexa o que falta).

    Com `speakers`, o vetor de cada passagem passa a sair SO da fala dos
    falantes escolhidos (`search.passage_scope_text`) — o roteiro de quem
    conduz deixa de puxar trechos para o mesmo tema. A passagem continua
    inteira em `text` (para mostrar, codificar e exportar com a pergunta
    como contexto); passagem sem nenhum falante escolhido some. Os vetores
    por escopo ficam em cache (`temas_escopo.npy`), refeitos so quando a
    escolha, o modelo ou alguma transcricao mudam."""
    import numpy as np

    if search.encoder_cached(search.active_encoder().key):
        search.build_indexes(paths, interview_ids, progress_callback=progress_callback,
                             should_cancel=should_cancel)
    passages: list[dict[str, Any]] = []
    blocks = []
    for payload, vectors in search.load_indexes(paths, interview_ids):
        iid = str(payload.get("interview_id"))
        for entry in payload.get("passages") or []:
            passages.append(dict(entry, interview_id=iid))
        blocks.append(np.asarray(vectors, dtype=np.float32))
    if not blocks:
        return [], np.zeros((0, 0), dtype=np.float32), []
    full = np.concatenate(blocks, axis=0)
    if speakers is None:
        return passages, full, []
    return _scoped(paths, interview_ids, passages, list(speakers), progress_callback, should_cancel)


def _scoped(paths: Paths, interview_ids: list[str], passages: list[dict[str, Any]],
            speakers: list[str], progress_callback: ProgressCallback | None,
            should_cancel: Callable[[], bool] | None):
    """Passagens com fala dos escolhidos + seus vetores (cache por assinatura)."""
    import numpy as np

    escolhidos = set(speakers)
    turns_by_id = {iid: search.load_source_turns(paths, iid) for iid in interview_ids}
    mantidas: list[dict[str, Any]] = []
    descartadas: list[dict[str, Any]] = []
    textos: list[str] = []
    for passage in passages:
        turns = turns_by_id.get(str(passage.get("interview_id"))) or []
        texto = search.passage_scope_text(turns, passage, escolhidos)
        # Pouca fala escolhida nao caracteriza o trecho: "tá?", "uhum",
        # "autorizo" respondendo ao roteiro. Sem este piso, TODOS esses
        # trechos se parecem entre si e formam um tema grande de
        # monossilabos (medido na copia real: virava o tema no 1o lugar,
        # com 59 trechos). Vao para "sem tema definido", onde continuam
        # visiveis e codificaveis.
        if len(texto.split()) < MIN_SCOPE_WORDS:
            descartadas.append(passage)
            continue
        mantidas.append(passage)
        textos.append(texto)
    if not mantidas:
        return [], np.zeros((0, 0), dtype=np.float32), descartadas
    assinatura = _scope_signature(paths, interview_ids, speakers, len(mantidas))
    cache, sig_path = scope_cache_path(paths), scope_signature_path(paths)
    if sig_path.exists() and cache.exists():
        try:
            if read_json(sig_path) == assinatura:
                with cache.open("rb") as handle:
                    guardados = np.load(handle, allow_pickle=False)
                if guardados.ndim == 2 and guardados.shape[0] == len(mantidas):
                    return mantidas, np.asarray(guardados, dtype=np.float32), descartadas
        except Exception:  # noqa: BLE001 - cache ilegivel = refazer
            pass
    if progress_callback is not None:
        progress_callback({"event": "themes_progress", "progress": 40,
                           "message": f"Lendo {len(mantidas)} trechos só com a fala escolhida..."})
    if should_cancel is not None and should_cancel():
        return [], np.zeros((0, 0), dtype=np.float32), descartadas
    encoder = search.load_encoder(search.active_encoder().key)
    vectors = search.embed_texts(textos, encoder, kind="passage")
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as handle:
        np.save(handle, np.asarray(vectors, dtype=np.float16), allow_pickle=False)
    write_json(sig_path, assinatura)
    return mantidas, np.asarray(vectors, dtype=np.float32), descartadas


def discover(paths: Paths, interview_ids: list[str], n_themes: int | None = None,
             progress_callback: ProgressCallback | None = None,
             should_cancel: Callable[[], bool] | None = None,
             speakers: list[str] | None = None) -> dict[str, Any]:
    """Descobre os temas do escopo e grava temas_projeto.json."""
    def emit(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": "themes_progress", "progress": progress, "message": message})

    emit(5, "Lendo as passagens das entrevistas...")
    passages, vectors, descartadas = collect_passages(
        paths, interview_ids, progress_callback, should_cancel, speakers=speakers)
    if should_cancel is not None and should_cancel():
        # Cancelar interrompe a indexacao no meio: agrupar e GRAVAR o que
        # sobrou trocaria os temas anteriores (com os nomes que o usuario
        # deu) por uma descoberta parcial, enquanto a janela diz que nada
        # mudou. Nada e gravado.
        emit(100, "Descoberta cancelada.")
        return {"themes": [], "outros": [], "cancelado": True}
    emit(60, f"Agrupando {len(passages)} passagens por semelhança...")
    result = build_themes(passages, vectors, n_themes=n_themes)
    # Os trechos com pouca fala escolhida nao entram no agrupamento, mas
    # continuam VISIVEIS em "sem tema definido" — o usuario ainda pode
    # codifica-los; sumir de vez seria esconder material dele.
    result["outros"] = list(result.get("outros") or []) + [
        {"interview_id": p["interview_id"], "t_from": int(p["t_from"]), "t_to": int(p["t_to"]),
         "start": float(p.get("start", 0) or 0), "text": str(p.get("text") or "")}
        for p in descartadas]
    result["n_themes_requested"] = int(n_themes or default_k(len(passages)))
    result["interview_ids"] = list(interview_ids)
    result["encoder"] = search.active_encoder().repo
    result["n_passages"] = len(passages)
    result["speakers"] = list(speakers) if speakers is not None else None
    from datetime import datetime
    result["created_at"] = datetime.now().isoformat(timespec="minutes")
    target = themes_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, result)
    emit(100, f"{len(result['themes'])} temas encontrados.")
    return result


def load_themes(paths: Paths) -> dict[str, Any] | None:
    target = themes_path(paths)
    if not target.exists():
        return None
    try:
        payload = read_json(target)
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) and "themes" in payload else None


def save_themes(paths: Paths, payload: dict[str, Any]) -> None:
    target = themes_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)


def rename_theme(payload: dict[str, Any], theme_id: str, name: str, description: str | None = None) -> bool:
    """Renomeia um tema (puro; devolve True se achou)."""
    for theme in payload.get("themes") or []:
        if theme.get("id") == theme_id:
            theme["name"] = " ".join(str(name).split()) or theme["name"]
            if description is not None:
                theme["description"] = str(description)
            theme["name_source"] = "usuario"
            return True
    return False


def merge_themes(payload: dict[str, Any], keep_id: str, absorb_id: str) -> bool:
    """Junta absorb em keep (uniao das passagens sem repetir; puro)."""
    themes = payload.get("themes") or []
    keep = next((t for t in themes if t.get("id") == keep_id), None)
    absorb = next((t for t in themes if t.get("id") == absorb_id), None)
    if keep is None or absorb is None or keep is absorb:
        return False
    seen = {(p["interview_id"], p["t_from"], p["t_to"]) for p in keep["passages"]}
    for p in absorb["passages"]:
        key = (p["interview_id"], p["t_from"], p["t_to"])
        if key not in seen:
            keep["passages"].append(p)
            seen.add(key)
    keep["passages"].sort(key=lambda p: -float(p.get("similarity", 0)))
    keep["n_passages"] = len(keep["passages"])
    keep["n_interviews"] = len({p["interview_id"] for p in keep["passages"]})
    keep["terms"] = sorted(set(keep.get("terms") or []) | set(absorb.get("terms") or []))[:TOP_TERMS * 2]
    themes.remove(absorb)
    return True


# ---------------------------------------------------------------------------
# Nomes pela AI (opcional, GPU): materia para o llm_worker
# ---------------------------------------------------------------------------

def naming_batches(payload: dict[str, Any], per_batch: int = 5, central: int = CENTRAL_PASSAGES) -> list[list[dict[str, Any]]]:
    """Lotes de temas para o prompt de nomeacao (puro): cada item tem id,
    termos e as passagens centrais (texto curto)."""
    items = []
    for theme in payload.get("themes") or []:
        items.append({
            "id": theme["id"],
            "terms": list(theme.get("terms") or []),
            "passages": [p["text"][:400] for p in (theme.get("passages") or [])[:central]],
        })
    return [items[i:i + per_batch] for i in range(0, len(items), per_batch)]


def apply_names(payload: dict[str, Any], names: list[dict[str, Any]]) -> int:
    """Aplica nomes/descricoes vindos da AI ({id, nome, descricao}); puro.
    Nao sobrescreve nomes dados pelo usuario. Devolve quantos mudaram."""
    by_id = {t["id"]: t for t in payload.get("themes") or []}
    changed = 0
    for item in names:
        theme = by_id.get(str(item.get("id") or ""))
        if theme is None or theme.get("name_source") == "usuario":
            continue
        nome = " ".join(str(item.get("nome") or "").split())
        if not nome:
            continue
        theme["name"] = nome[:80]
        theme["description"] = " ".join(str(item.get("descricao") or "").split())[:300]
        theme["name_source"] = "ai"
        changed += 1
    return changed


def name_with_llm(paths: Paths, payload: dict[str, Any],
                  progress_callback: ProgressCallback | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Pede nomes ao modelo de analise (llm_worker, tarefa nomear_temas) e
    aplica; {ok, nomeados, nomes} ou {erro}. Nada e gravado aqui — quem chama
    salva (a GUI passa uma COPIA e aplica `nomes` no payload dela ao fim)."""
    import uuid

    from . import ask, runtime
    from .llm_worker import SEM_RESPOSTA  # noqa: F401 - garante o modulo carregado

    motivo = ask._llm_prereqs(progress_callback)
    if motivo:
        return {"erro": motivo}
    batches = naming_batches(payload)
    if not batches:
        return {"ok": True, "nomeados": 0}
    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    temas_path = tmp_dir / f"temas_{token}_lotes.json"
    out_path = tmp_dir / f"temas_{token}_nomes.json"
    temas_path.write_text(json.dumps(batches, ensure_ascii=False), encoding="utf-8")
    command = ask._worker_command("nomear_temas", paths, out_path, "")
    command += ["--temas-file", str(temas_path)]
    result = ask._run_worker(command, out_path, [temas_path], progress_callback, should_cancel)
    if result is None:
        return {"erro": "A AI local falhou ao nomear os temas."}
    nomes = [n for n in (result.get("nomes") or []) if isinstance(n, dict)]
    nomeados = apply_names(payload, nomes)
    return {"ok": True, "nomeados": nomeados, "nomes": nomes}
