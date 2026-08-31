"""Toy R0: ui_tokens — paleta aprovada (dossie RD 2026-08-31), pureza e helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pureza: importar ui_tokens NAO pode puxar PySide6 (o gui_launcher o
# importa antes do splash; Qt pesado ali atrasa a abertura do app).
antes = set(sys.modules)
from transcribe_pipeline import ui_tokens as tk

novos = set(sys.modules) - antes
assert not any("PySide6" in m for m in novos), \
    f"ui_tokens puxou Qt: {[m for m in novos if 'PySide6' in m]}"
print("PASS: modulo puro (sem PySide6)")

# 12 tokens, todos #rrggbb validos e distintos
assert len(tk.ALL_COLORS) == 12
for nome, cor in tk.ALL_COLORS.items():
    r, g, b = tk.hex_to_rgb(cor)
    assert all(0 <= c <= 255 for c in (r, g, b)), (nome, cor)
assert len(set(tk.ALL_COLORS.values())) == 12, "tokens com valores repetidos"
print("PASS: 12 tokens validos e distintos")

# Paleta EXATA aprovada no dossie (mudanca de valor exige novo aceite)
assert tk.BG_BASE == "#1b1e23" and tk.ACCENT == "#44d7b6"
assert tk.AI == "#b197fc" and tk.DANGER == "#e5534b"
print("PASS: valores da paleta aprovada")

# Contraste WCAG: texto sobre os tres fundos >= 7:1 (AAA);
# muted sobre fundo base >= 4.5:1 (AA)
for fundo in (tk.BG_BASE, tk.BG_RAISED, tk.BG_OVERLAY):
    ratio = tk.contrast_ratio(tk.TEXT, fundo)
    assert ratio >= 7.0, f"TEXT sobre {fundo}: {ratio:.2f}"
assert tk.contrast_ratio(tk.TEXT_MUTED, tk.BG_BASE) >= 4.5
print("PASS: contraste WCAG da paleta")

# Semanticas legiveis sobre o fundo dos paineis (>= 3:1, texto grande/chip)
for cor in (tk.ACCENT, tk.INFO, tk.WARN, tk.DANGER, tk.AI):
    assert tk.contrast_ratio(cor, tk.BG_RAISED) >= 3.0, cor
print("PASS: semanticas legiveis")

# Helpers
assert tk.rgba("#4dabf7", 0.14) == "rgba(77, 171, 247, 0.14)"
estilo = tk.banner_style(tk.INFO)
assert "rgba(77, 171, 247, 0.14)" in estilo and "rgba(77, 171, 247, 0.45)" in estilo
assert "border-radius" in estilo
try:
    tk.hex_to_rgb("azul")
    raise AssertionError("hex invalido deveria levantar ValueError")
except ValueError:
    pass
print("PASS: helpers rgba/banner_style/hex_to_rgb")

# Escalas
assert (tk.SP_1, tk.SP_2, tk.SP_3, tk.SP_4, tk.SP_5) == (4, 8, 12, 16, 24)
assert tk.FONT_CAPTION < tk.FONT_BODY < tk.FONT_TITLE < tk.FONT_HERO
print("PASS: escalas de espaco e fonte")

print("PASS: toy_ui_tokens")
