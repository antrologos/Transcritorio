"""Toy test: escolha de midia do player + contagem do banner de trocas.

(plano sincronia+banner, 2026-08-25). Sem Qt: helpers puros.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import (
        BOUNDARY_NOTE_MARKER,
        boundary_flagged_rows,
        preferred_media_index,
    )
except ImportError as exc:  # PySide6 ausente no CI minimo
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

# --- preferred_media_index ---
# Audio original + WAV preparado -> WAV (indice 1)
assert preferred_media_index([Path("a.mp3"), Path("a.wav")]) == 1
# So o original de audio (WAV ainda nao preparado) -> original
assert preferred_media_index([Path("a.m4a")]) == 0
# VIDEO -> original sempre (painel de video precisa da imagem)
assert preferred_media_index([Path("grupo.mp4"), Path("grupo.wav")]) == 0
# Original sumiu, sobrou o WAV -> WAV (indice 0)
assert preferred_media_index([Path("a.wav")]) == 0
# Lista vazia nao explode
assert preferred_media_index([]) == 0
print("PASS: preferred_media_index")

# --- boundary_flagged_rows: flag E marcador; desmarcar Duvida remove ---
nota_auto = f"A voz deste bloco e a do seguinte {BOUNDARY_NOTE_MARKER} (semelhanca 44%)."
turns = [
    {"flags": ["duvida"], "notes": nota_auto},          # 0: conta
    {"flags": [], "notes": nota_auto},                   # 1: usuario desmarcou -> fora
    {"flags": ["duvida"], "notes": "nota manual"},      # 2: duvida manual, sem marcador -> fora
    {"flags": ["duvida", "inaudivel"], "notes": nota_auto},  # 3: conta
    {"flags": ["sobreposicao"], "notes": nota_auto},    # 4: sem duvida -> fora
    {},                                                   # 5: vazio
]
assert boundary_flagged_rows(turns) == [0, 3]
assert boundary_flagged_rows([]) == []
print("PASS: boundary_flagged_rows")

print("PASS: toy_preferred_media")
