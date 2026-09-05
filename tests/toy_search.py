"""Toy test: motor de busca (fase 2.3) — partes puras, sem encoder."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import search as se
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
from transcribe_pipeline.utils import write_json

# --- normalize_with_map: acentos mudam comprimento; mapa aponta o original ---
norm, positions = se.normalize_with_map("Ação boa")
assert norm == "acao boa"
assert positions[0] == 0 and positions[3] == 3  # 'o' de Acao -> indice 3 ('o' original)
assert se.normalize("REMUNERAÇÃO") == "remuneracao"
print("PASS: normalize_with_map")

# --- literal_spans: matches com acentos dos dois lados, multiplos, vazio ---
text = "A remuneração era boa. Remuneracao mesmo!"
spans = se.literal_spans(text, "remuneração")
assert len(spans) == 2, spans
start, end = spans[0]
assert text[start:end] == "remuneração"
start2, end2 = spans[1]
assert text[start2:end2] == "Remuneracao"
assert se.literal_spans(text, "") == []
assert se.literal_spans("nada aqui", "pagamento") == []
print("PASS: literal_spans")

# --- search_turns ---
turns = [
    {"text": "o pagamento atrasou"},
    {"text": "nada relevante"},
    {"text": "PAGAMENTO em dia"},
]
hits = se.search_turns(turns, "pagamento")
assert [h["turn_index"] for h in hits] == [0, 2]
print("PASS: search_turns")

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - CI minimo sem numpy
    print(f"SKIP: numpy ausente ({exc}) — partes do indice v3 nao testadas")
    np = None

# --- build_index_payload v3: passagens (metadados) sem vetores ---
passages = se.build_passages([
    {"start": 1.5, "end": 2.5, "human_label": "A", "text": " ola  mundo "},
    {"start": 3.0, "end": 4.0, "human_label": "B", "text": "tudo bem"},
])
payload = se.build_index_payload("X01", passages, source_mtime=123.5, model_repo="repo/x", dim=2)
assert payload["version"] == se.INDEX_VERSION == 3 and payload["model"] == "repo/x" and payload["dim"] == 2
assert payload["passages"][0]["t_from"] == 0 and payload["passages"][0]["text"] == "A: ola mundo B: tudo bem"
assert payload["passages"][0]["start"] == 1.5 and payload["passages"][0]["end"] == 4.0
assert "vectors" not in payload  # vetores vivem no .npy
print("PASS: build_index_payload v3")

# --- write/read + index_is_fresh: ausente/fresco/fonte mudou/versao/modelo ---
if np is not None:
    with tempfile.TemporaryDirectory() as tmp:
        paths = make_paths(load_config(None), base_dir=Path(tmp))
        ensure_directories(paths)
        iid = "X01"
        canonical = {"interview_id": iid, "turns": [{"start": 0, "text": "ola"}]}
        from transcribe_pipeline.review_store import canonical_path
        write_json(canonical_path(paths, iid), canonical)
        assert se.index_is_fresh(paths, iid, "repo/x") is False  # sem indice
        source = se.source_path_for(paths, iid)
        payload = se.build_index_payload(
            iid, se.build_passages(canonical["turns"]), source.stat().st_mtime, "repo/x", 2)
        se.write_index(paths, iid, payload, [[0.6, 0.8]])
        assert se.index_is_fresh(paths, iid, "repo/x") is True
        assert se.index_is_fresh(paths, iid, "repo/outro") is False  # trocou o encoder
        loaded, vectors = se.read_index(paths, iid)
        assert loaded["passages"][0]["text"] == "ola" and vectors.shape == (1, 2)
        assert abs(float(vectors[0][0]) - 0.6) < 1e-3  # float16 no disco, float32 na memoria
        # fonte mudou -> stale (utime explicito: reescrita rapida pode cair
        # no MESMO mtime e o teste flakearia)
        write_json(canonical_path(paths, iid), dict(canonical, extra=1))
        import os
        bumped = source.stat().st_mtime + 2
        os.utime(source, (bumped, bumped))
        assert se.index_is_fresh(paths, iid, "repo/x") is False
        # versao errada -> stale
        payload["version"] = -1
        payload["source_mtime"] = source.stat().st_mtime
        write_json(se.index_path(paths, iid), payload)
        assert se.index_is_fresh(paths, iid, "repo/x") is False
        # .npy ausente -> stale
        payload["version"] = se.INDEX_VERSION
        write_json(se.index_path(paths, iid), payload)
        se.vectors_path(paths, iid).unlink()
        assert se.index_is_fresh(paths, iid, "repo/x") is False
    print("PASS: write_index / read_index / index_is_fresh")

# --- rank_semantic: cosseno em matriz, z-score, exclude por turno, colapso, teto por entrevista ---
if np is not None:
    def _index(iid: str, spans: list[tuple[int, int]], vectors: list[list[float]]):
        payload = {"interview_id": iid, "passages": [
            {"p": n, "t_from": a, "t_to": b, "start": float(a), "end": float(b), "text": f"{iid}-{a}-{b}"}
            for n, (a, b) in enumerate(spans)]}
        return payload, np.asarray(vectors, dtype=np.float32)

    idx_a = _index("A", [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15)],
                   [[1.0, 0.0], [0.95, 0.31], [0.0, 1.0], [0.9, 0.44], [0.7, 0.71]])
    idx_b = _index("B", [(0, 2)], [[0.6, 0.8]])
    ranked = se.rank_semantic([1.0, 0.0], [idx_a, idx_b], top_n=10, per_interview_cap=None)
    # (0,3) e (3,6) compartilham o turno 3: fica so a melhor
    assert [(h["interview_id"], h["t_from"]) for h in ranked][:2] == [("A", 0), ("A", 9)], ranked
    assert ranked[0]["similarity"] == 1.0 and ranked[0]["turn_index"] == 0 and "z" in ranked[0]
    assert ranked[0]["z"] > ranked[-1]["z"]
    # teto por entrevista com escopo > 1
    capped = se.rank_semantic([1.0, 0.0], [idx_a, idx_b], top_n=10, per_interview_cap=2)
    assert sum(1 for h in capped if h["interview_id"] == "A") == 2 and any(h["interview_id"] == "B" for h in capped)
    # exclude por turno: tira toda passagem que contem o turno
    excl = se.rank_semantic([1.0, 0.0], [idx_a], exclude={("A", 0)}, top_n=10, per_interview_cap=None)
    assert all(not (h["t_from"] <= 0 <= h["t_to"]) for h in excl)
    # top_n e piso absoluto opcional
    assert len(se.rank_semantic([1.0, 0.0], [idx_a], top_n=1, per_interview_cap=None)) == 1
    assert se.rank_semantic([1.0, 0.0], [idx_a], min_similarity=1.5) == []
    # vetores com dimensao errada sao ignorados sem explodir
    assert se.rank_semantic([1.0, 0.0, 0.0], [idx_a]) == []
    print("PASS: rank_semantic v3")

# --- rrf_fuse: fusao por posicao, identidade por faixa de turnos ---
dense = [{"interview_id": "A", "t_from": 0, "t_to": 2, "similarity": 0.9},
         {"interview_id": "B", "t_from": 5, "t_to": 7, "similarity": 0.8},
         {"interview_id": "A", "t_from": 9, "t_to": 9, "similarity": 0.7}]
literal = [{"interview_id": "A", "t_from": 9, "t_to": 9, "literal": True},
           {"interview_id": "C", "t_from": 1, "t_to": 3, "literal": True}]
fused = se.rrf_fuse(dense, literal)
keys = [(h["interview_id"], h["t_from"]) for h in fused]
assert keys[0] == ("A", 9), keys          # aparece nas duas listas: sobe
assert set(keys) == {("A", 0), ("B", 5), ("A", 9), ("C", 1)} and all("rrf" in h for h in fused)
assert fused[0].get("similarity") == 0.7  # o hit devolvido e o do primeiro encontro (denso)
assert se.rrf_fuse(dense) == [dict(h, rrf=se.rrf_fuse(dense)[i]["rrf"]) for i, h in enumerate(dense)]
print("PASS: rrf_fuse")

# --- relevance_sections: com e sem reordenador; nada responde -> weak ---
h = lambda score=None, z=0.0: {"score": score, "z": z}  # noqa: E731
# limiares calibrados no gabarito: responde >= -2, relacionado >= -5, abaixo some
secs = se.relevance_sections([h(1.2), h(-1.5), h(-3.0), h(-6.0)], reranked=True)
assert [s["label"] for s in secs] == ["Respondem", "Relacionados"], secs
assert len(secs[0]["hits"]) == 2 and len(secs[1]["hits"]) == 1  # -6 some
assert not secs[0]["weak"]
weak = se.relevance_sections([h(-3.0), h(-4.5)], reranked=True)
assert weak[0]["key"] == "relacionado" and weak[0]["weak"] is True
assert se.relevance_sections([h(-7.0)], reranked=True) == []
secs = se.relevance_sections([h(z=3.4), h(z=2.2), h(z=0.5)], reranked=False)
assert [s["label"] for s in secs] == ["Muito próximos", "Próximos", "Relacionados"]
assert se.relevance_sections([h(z=1.0)], reranked=False)[0]["weak"] is True
assert se.relevance_sections([], reranked=True) == []
print("PASS: relevance_sections")

# --- relevance_label (UI sem numeros; z relativo ao escopo) ---
try:
    from transcribe_pipeline.review_studio_qt import relevance_label
    assert relevance_label({"z": 3.4}) == "muito próximo"
    assert relevance_label({"z": 2.1}) == "próximo"
    assert relevance_label({"z": 0.9}) == "relacionado"
    assert relevance_label({}) == "relacionado"
    print("PASS: relevance_label")
except ImportError:
    print("SKIP: relevance_label (PySide6 ausente)")

# --- search_scope_text (linha de escopo das janelas de busca/AI) ---
try:
    from transcribe_pipeline.review_studio_qt import search_scope_text

    # all: todos / parcial / nenhum / vazio / singular
    assert search_scope_text("all", 12, 12, "A busca") == (
        "A busca lê as transcrições de todos os 12 arquivos do projeto.")
    assert search_scope_text("all", 12, 9, "A AI") == (
        "A AI lê as transcrições de 9 dos 12 arquivos do projeto — "
        "3 ainda sem transcrição ficam de fora.")
    assert "não o áudio" in search_scope_text("all", 5, 0, "A busca")
    assert search_scope_text("all", 0, 0, "A busca") == (
        "Este projeto ainda não tem arquivos.")
    assert search_scope_text("all", 1, 1, "A busca") == (
        "A busca lê a transcrição do único arquivo do projeto.")
    assert "único arquivo" in search_scope_text("all", 1, 0, "A busca")
    # choose: lista interna so tem transcritas (ready == total)
    assert search_scope_text("choose", 2, 2, "A AI") == (
        "A AI lê as transcrições das 2 entrevistas escolhidas.")
    assert search_scope_text("choose", 0, 0, "A busca") == (
        "Marque na lista acima quais entrevistas entram.")
    assert search_scope_text("choose", 1, 1, "A busca") == (
        "A busca lê a transcrição da entrevista escolhida.")
    # open: com e sem transcricao
    assert search_scope_text("open", 1, 1, "A AI") == (
        "A AI lê a transcrição da entrevista aberta.")
    assert "não foi transcrita" in search_scope_text("open", 1, 0, "A busca")
    print("PASS: search_scope_text")
except ImportError:
    print("SKIP: search_scope_text (PySide6 ausente)")

print("PASS: toy_search")
