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
    # fonte mudou -> stale
    write_json(canonical_path(paths, iid), dict(canonical, extra=1))
    assert se.index_is_fresh(paths, iid) is False
    # versao errada -> stale
    payload["version"] = -1
    payload["source_mtime"] = source.stat().st_mtime
    write_json(target, payload)
    assert se.index_is_fresh(paths, iid) is False
print("PASS: index_is_fresh")

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

print("PASS: toy_search")
