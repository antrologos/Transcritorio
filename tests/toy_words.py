"""Toy test: indice de palavras (fase 3) — partes puras, sem Qt."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import words as wo
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
from transcribe_pipeline.utils import write_json

# --- flatten_words: WhisperX (com score), ordenacao, lixo ignorado ---
payload = {
    "segments": [
        {"words": [
            {"word": " ola ", "start": 5.0, "end": 5.4, "score": 0.9},
            {"word": "mundo", "start": 5.5, "end": 6.0, "score": 0.2},
        ]},
        {"words": [
            {"word": "antes", "start": 1.0, "end": 1.5, "score": 0.8},
            {"word": "", "start": 2.0, "end": 2.5},            # vazio: fora
            {"word": "ruim", "start": 3.0, "end": 2.0},        # end<=start: fora
            "lixo",                                              # nao-dict: fora
        ]},
        "lixo",
    ],
}
flat = wo.flatten_words(payload)
assert [w["text"] for w in flat] == ["antes", "ola", "mundo"]  # ordenado por start
assert flat[0]["score"] == 0.8 and flat[1]["score"] == 0.9
# MLX: sem score -> None
mlx = wo.flatten_words({"segments": [{"words": [{"word": "oi", "start": 0.0, "end": 0.3}]}]})
assert mlx[0]["score"] is None
assert wo.flatten_words({}) == []
print("PASS: flatten_words")

# --- words_in_range: bisect, bordas [start, end) ---
index = [
    {"start": 1.0, "end": 1.4, "text": "a", "score": None},
    {"start": 2.0, "end": 2.4, "text": "b", "score": None},
    {"start": 3.0, "end": 3.4, "text": "c", "score": None},
]
assert [w["text"] for w in wo.words_in_range(index, 1.0, 3.0)] == ["a", "b"]
assert [w["text"] for w in wo.words_in_range(index, 2.0, 10.0)] == ["b", "c"]
assert wo.words_in_range(index, 5.0, 9.0) == []
assert wo.words_in_range(index, 3.0, 3.0) == []  # intervalo vazio
assert wo.words_in_range([], 0.0, 10.0) == []
print("PASS: words_in_range")

# --- word_time_for_char: exato (tokens == palavras) ---
turn_words = [
    {"start": 10.0, "end": 10.4, "text": "o"},
    {"start": 10.5, "end": 11.0, "text": "pagamento"},
    {"start": 12.0, "end": 12.6, "text": "atrasou"},
]
text = "o pagamento atrasou"
t, exact = wo.word_time_for_char(turn_words, text, 0)      # sobre "o"
assert (t, exact) == (10.0, True)
t, exact = wo.word_time_for_char(turn_words, text, 5)      # dentro de "pagamento"
assert (t, exact) == (10.5, True)
t, exact = wo.word_time_for_char(turn_words, text, 12)     # espaco antes de "atrasou"
assert (t, exact) == (12.0, True)
t, exact = wo.word_time_for_char(turn_words, text, len(text))  # fim
assert (t, exact) == (12.0, True)
# editado (contagens divergem) -> fracao de tokens, exato=False
edited = "o pagamento realmente atrasou muito"
t, exact = wo.word_time_for_char(turn_words, edited, len(edited))
assert exact is False and t == 12.0                        # ultimo -> ultima palavra
t, exact = wo.word_time_for_char(turn_words, edited, 0)
assert exact is False and t == 10.0                        # primeiro -> primeira
# degenerados
assert wo.word_time_for_char([], text, 3) == (None, False)
assert wo.word_time_for_char(turn_words, "   ", 1) == (None, False)
print("PASS: word_time_for_char")

# --- uncertain_threshold: decil inferior; sem scores -> None ---
scored = [{"start": float(i), "end": float(i) + 0.5, "text": "w",
           "score": i / 100.0} for i in range(100)]
cut = wo.uncertain_threshold(scored)
assert cut == 0.10, cut                                     # decil inferior
assert wo.uncertain_threshold(scored, percentile=0) == 0.0
assert wo.uncertain_threshold([{"start": 0.0, "end": 1.0, "text": "w",
                                "score": None}]) is None
assert wo.uncertain_threshold([]) is None
print("PASS: uncertain_threshold")

# --- load_word_index: arquivo real via find_whisperx_json; ausente -> [] ---
with tempfile.TemporaryDirectory() as tmp:
    paths = make_paths(load_config(None), base_dir=Path(tmp))
    ensure_directories(paths)
    assert wo.load_word_index(paths, "X01") == []          # sem ASR: silencio
    write_json(paths.asr_dir / "X01.json", payload)
    loaded = wo.load_word_index(paths, "X01")
    assert [w["text"] for w in loaded] == ["antes", "ola", "mundo"]
    # JSON corrompido -> [] sem excecao
    (paths.asr_dir / "X02.json").write_text("{quebrado", encoding="utf-8")
    assert wo.load_word_index(paths, "X02") == []
print("PASS: load_word_index")

print("PASS: toy_words")
