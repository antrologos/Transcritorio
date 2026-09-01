"""Toy: cascata de prioridades do tempo ao dividir bloco (choose_split_time).

Gate da correcao 2026-09-01: o texto divide no cursor de TEXTO e o tempo
deve ser coerente com isso. Clique deliberado na onda vence; a palavra sob
o cursor de texto e o padrao; player pausado dentro do bloco e fallback sem
palavras; player TOCANDO nunca decide (era a causa de fronteiras
incoerentes: o audio avancava durante o dialogo de confirmacao).

Depende de PySide6 (importa review_studio_qt); o CI ja o instala.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import choose_split_time
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

TEXT = "alfa beta gama delta"          # 4 tokens, len 20
WORDS = [
    {"start": 10.0, "end": 11.5},
    {"start": 12.0, "end": 14.0},
    {"start": 15.0, "end": 17.0},
    {"start": 18.0, "end": 19.5},
]
S, E = 10.0, 20.0
CHAR_GAMA = TEXT.index("gama")          # 10

# 1. Clique deliberado na onda vence tudo (ate player tocando).
t, note = choose_split_time(S, E, 13.0, True, 17.0, True, WORDS, TEXT, CHAR_GAMA)
assert t == 13.0 and "clique" in note, (t, note)

# 2. Cursor programatico (from_click=False) e ignorado -> palavra.
t, note = choose_split_time(S, E, 13.0, False, None, False, WORDS, TEXT, CHAR_GAMA)
assert t == 15.0 and "exato" in note, (t, note)

# 3. Player TOCANDO dentro do bloco e ignorado -> palavra.
t, note = choose_split_time(S, E, None, False, 17.0, True, WORDS, TEXT, CHAR_GAMA)
assert t == 15.0 and "exato" in note, (t, note)

# 4. Sem palavras + player PAUSADO dentro -> tempo do player.
t, note = choose_split_time(S, E, None, False, 17.0, False, [], TEXT, CHAR_GAMA)
assert t == 17.0 and "pausado" in note, (t, note)

# 5. Sem palavras + player tocando -> interpolacao (nota "estimado").
t, note = choose_split_time(S, E, None, False, 17.0, True, [], TEXT, CHAR_GAMA)
assert abs(t - 15.0) < 1e-9 and "estimado" in note, (t, note)

# 6. Texto editado (5 tokens vs 4 palavras) -> nota "aproximado", tempo no bloco.
TEXT_ED = "alfa beta hmm gama delta"
t, note = choose_split_time(S, E, None, False, None, False, WORDS, TEXT_ED, TEXT_ED.index("gama"))
assert S < t < E and "aproximado" in note, (t, note)

# 7a. Clique fora do intervalo ABERTO (antes/na borda) cai para a palavra.
t, note = choose_split_time(S, E, 9.0, True, None, False, WORDS, TEXT, CHAR_GAMA)
assert t == 15.0 and "exato" in note, (t, note)
t, note = choose_split_time(S, E, 10.0, True, None, False, WORDS, TEXT, CHAR_GAMA)
assert t == 15.0 and "exato" in note, (t, note)

# 7b. Palavra no exato inicio do bloco (nao estritamente dentro) cai adiante;
#     sem player -> interpolacao, sempre dentro do intervalo.
t, note = choose_split_time(S, E, None, False, None, False, WORDS, TEXT, 2)
assert abs(t - 11.0) < 1e-9 and "estimado" in note, (t, note)

# 7c. Player pausado FORA do bloco nao decide -> interpolacao.
t, note = choose_split_time(S, E, None, False, 25.0, False, [], TEXT, CHAR_GAMA)
assert abs(t - 15.0) < 1e-9 and "estimado" in note, (t, note)

# 8. Clamp da interpolacao: nunca colapsa na borda do bloco.
t, _ = choose_split_time(S, E, None, False, None, False, [], TEXT, 0)
assert abs(t - 10.1) < 1e-9, t
t, _ = choose_split_time(S, E, None, False, None, False, [], TEXT, len(TEXT))
assert abs(t - 19.9) < 1e-9, t

# Invariante: resultado sempre dentro do intervalo aberto.
for args in [
    (S, E, 13.0, True, 17.0, True, WORDS, TEXT, CHAR_GAMA),
    (S, E, None, False, None, False, [], TEXT, 0),
    (S, E, None, False, 17.0, False, [], TEXT, len(TEXT)),
]:
    t, _ = choose_split_time(*args)
    assert S < t < E, (t, args)

print("PASS: toy_split_time_priority (12 casos)")
