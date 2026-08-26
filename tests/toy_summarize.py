"""Toy test: funcoes puras do sumario (llm_worker + summarize), fase 2.1."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import llm_worker as lw
from transcribe_pipeline.config import load_config, make_paths
from transcribe_pipeline.summarize import resumo_path

# --- fmt_time / turn_line ---
assert lw.fmt_time(0) == "00:00:00"
assert lw.fmt_time(3725.9) == "01:02:05"
line = lw.turn_line({"start": 65.0, "human_label": "Entrevistado", "text": "  ola   mundo "})
assert line == "[00:01:05] Entrevistado: ola mundo", line

# --- load_turns aceita review E canonical; ignora vazios ---
turns = [{"start": 0, "text": "a"}, {"start": 1, "text": "  "}, {"start": 2, "text": "b"}]
assert len(lw.load_turns({"transcript": {"turns": turns}})) == 2
assert len(lw.load_turns({"turns": turns})) == 2
print("PASS: load_turns/turn_line")

# --- build_windows: respeita o limite sem cortar turno; preserva ordem ---
big = [{"start": i * 10.0, "human_label": "X", "text": "palavra " * 50} for i in range(30)]
windows = lw.build_windows(big, max_chars=1000)
assert len(windows) > 1
assert all(len(w) <= 1000 + 500 for w in windows)  # nunca corta turno: 1 turno pode exceder
joined = "\n".join(windows)
assert joined.count("[00:0") + joined.count("[00:1") + joined.count("[00:2") + joined.count("[00:3") + joined.count("[00:4") >= 30
one = lw.build_windows(big[:2], max_chars=999999)
assert len(one) == 1
print("PASS: build_windows")

# --- extract_json_list / merge_notes ---
answer = 'blabla [{"tema": "recusa de moradores", "inicio": "00:07:17", "resumo": "pessoas receosas"}] fim'
notes = lw.extract_json_list(answer)
assert notes and notes[0]["tema"] == "recusa de moradores"
assert lw.extract_json_list("nada aqui") == []
assert lw.extract_json_list("[invalido") == []
merged = lw.merge_notes([notes, [{"tema": "pagamento", "inicio": "", "resumo": "ok"}], ["lixo"]])
assert "- [00:07:17] recusa de moradores: pessoas receosas" in merged
assert "- [??:??:??] pagamento: ok" in merged
print("PASS: extract/merge notes")

# --- resumo_path ---
paths = make_paths(load_config(None), base_dir=Path("X:/proj"))
assert resumo_path(paths, "D05R_0822").as_posix().endswith(
    "Transcricoes/05_transcripts_review/final/md/D05R_0822.resumo.md")
print("PASS: resumo_path")

print("PASS: toy_summarize")
