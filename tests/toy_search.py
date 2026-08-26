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

# --- build_index_payload: arredondamento e entradas ---
payload = se.build_index_payload(
    "X01", [{"start": 1.5, "human_label": "A", "text": " ola  mundo "}],
    [[0.123456, -0.98765]], source_mtime=123.5)
assert payload["vectors"] == [[0.1235, -0.9877]]
assert payload["turns"][0] == {"t": 0, "start": 1.5, "label": "A", "text": "ola mundo"}
assert payload["dim"] == 2 and payload["version"] == se.INDEX_VERSION
print("PASS: build_index_payload")

# --- index_is_fresh: ausente/fresco/fonte mudou/versao errada ---
with tempfile.TemporaryDirectory() as tmp:
    paths = make_paths(load_config(None), base_dir=Path(tmp))
    ensure_directories(paths)
    iid = "X01"
    canonical = {"interview_id": iid, "turns": [{"start": 0, "text": "ola"}]}
    from transcribe_pipeline.review_store import canonical_path
    write_json(canonical_path(paths, iid), canonical)
    assert se.index_is_fresh(paths, iid) is False  # sem indice
    source = se.source_path_for(paths, iid)
    payload = se.build_index_payload(iid, canonical["turns"], [[1.0]], source.stat().st_mtime)
    target = se.index_path(paths, iid)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, payload)
    assert se.index_is_fresh(paths, iid) is True
    # fonte mudou -> stale (utime explicito: reescrita rapida pode cair
    # no MESMO mtime e o teste flakearia)
    write_json(canonical_path(paths, iid), dict(canonical, extra=1))
    import os
    bumped = source.stat().st_mtime + 2
    os.utime(source, (bumped, bumped))
    assert se.index_is_fresh(paths, iid) is False
    # versao errada -> stale
    payload["version"] = -1
    payload["source_mtime"] = source.stat().st_mtime
    write_json(target, payload)
    assert se.index_is_fresh(paths, iid) is False
print("PASS: index_is_fresh")

# --- context_window_text: fragmentos herdam o tema da vizinhanca ---
ctx_turns = [
    {"text": "Falamos sobre a remuneração dos recenseadores no Censo."},
    {"text": "Mas isso"},
    {"text": "  atrasou   muito  o pagamento das equipes. "},
]
window = se.context_window_text(ctx_turns, 1)
assert "remuneração" in window and "Mas isso" in window and "atrasou" in window
# bordas: primeiro e ultimo turno nao explodem
assert se.context_window_text(ctx_turns, 0).startswith("Falamos")
assert se.context_window_text(ctx_turns, 2).endswith("equipes.")
# caps de cauda/cabeca respeitados
long_turns = [{"text": "a" * 500}, {"text": "meio"}, {"text": "b" * 500}]
capped = se.context_window_text(long_turns, 1, tail_chars=10, head_chars=5)
assert capped == ("a" * 10) + " meio " + ("b" * 5)
print("PASS: context_window_text")

# --- collapse_adjacent: melhor hit representa o momento ---
adj = [
    {"interview_id": "A", "turn_index": 6, "similarity": 0.9},
    {"interview_id": "A", "turn_index": 5, "similarity": 0.8},   # vizinho do 6
    {"interview_id": "A", "turn_index": 7, "similarity": 0.7},   # vizinho do 6
    {"interview_id": "B", "turn_index": 6, "similarity": 0.6},   # outra entrevista
    {"interview_id": "A", "turn_index": 20, "similarity": 0.5},  # longe
]
collapsed = se.collapse_adjacent(adj)
assert [(h["interview_id"], h["turn_index"]) for h in collapsed] == [
    ("A", 6), ("B", 6), ("A", 20)]
print("PASS: collapse_adjacent")

# --- rank_semantic: cosseno, exclude, corte e top_n ---
indexes = [{
    "interview_id": "A",
    "turns": [{"t": 0, "start": 1.0, "label": "X", "text": "perto"},
              {"t": 1, "start": 2.0, "label": "X", "text": "longe"},
              {"t": 2, "start": 3.0, "label": "X", "text": "excluido"}],
    "vectors": [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]],
}]
ranked = se.rank_semantic([1.0, 0.0], indexes, exclude={("A", 2)}, min_similarity=0.5, top_n=10)
assert [r["turn_index"] for r in ranked] == [0]
assert ranked[0]["similarity"] == 1.0
ranked_all = se.rank_semantic([1.0, 0.0], indexes, min_similarity=-1.0, top_n=2)
assert len(ranked_all) == 2 and ranked_all[0]["similarity"] >= ranked_all[1]["similarity"]
print("PASS: rank_semantic")

# --- similarity_label (UI sem numeros; precisa de PySide6 no ambiente) ---
try:
    from transcribe_pipeline.review_studio_qt import similarity_label
    assert similarity_label(0.64) == "muito proximo"
    assert similarity_label(0.50) == "proximo"
    assert similarity_label(0.36) == "relacionado"
    print("PASS: similarity_label")
except ImportError:
    print("SKIP: similarity_label (PySide6 ausente)")

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
