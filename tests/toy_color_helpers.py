"""Toy test: helpers de cor para dark theme.

Valida:
- _style_ok / _style_warn / _style_err / _style_muted retornam string
- Cores tem contrast ratio adequado contra Fusion dark bg (#2d2d2d)
- Contrast ratio WCAG AA (>=4.5:1) para texto normal
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline.review_studio_qt import (
    _style_ok, _style_warn, _style_err, _style_muted,
)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c + c for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(_hex_to_rgb(fg_hex))
    l2 = _relative_luminance(_hex_to_rgb(bg_hex))
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def _extract_color(style: str) -> str:
    # "color: #xxx;" ou "color: #xxx; font-weight: 700;"
    for part in style.split(";"):
        part = part.strip()
        if part.startswith("color:"):
            return part.split(":", 1)[1].strip()
    raise ValueError(f"Nao encontrou color em: {style!r}")


DARK_BG = "#2d2d2d"


def test_helpers_retornam_string() -> None:
    for fn in (_style_ok, _style_warn, _style_err, _style_muted):
        s = fn()
        assert isinstance(s, str) and "color:" in s, (fn.__name__, s)
    print("PASS: 4 helpers retornam string com color:")


def test_contrast_vs_dark_bg() -> None:
    """Fusion dark Window=#2d2d2d. Cores devem ter contrast >=4.5:1."""
    for name, fn in [("ok", _style_ok), ("warn", _style_warn),
                     ("err", _style_err), ("muted", _style_muted)]:
        color = _extract_color(fn())
        ratio = contrast_ratio(color, DARK_BG)
        assert ratio >= 4.5, f"{name} {color}: contrast {ratio:.2f} < 4.5 em bg {DARK_BG}"
        print(f"  PASS {name}: {color} sobre {DARK_BG} = {ratio:.2f}:1")


def test_no_legibilidade_original() -> None:
    """As cores originais hardcoded eram ilegiveis em dark — confirmar."""
    # #555 sobre #2d2d2d: contrast ~1.5, muito baixo
    c = contrast_ratio("#555", DARK_BG)
    assert c < 3.0, f"#555 deveria ser ilegivel em dark, got {c}"
    # #c00 sobre #2d2d2d: tambem baixo
    c = contrast_ratio("#c00", DARK_BG)
    assert c < 4.5, f"#c00 deveria ser <4.5 em dark, got {c}"
    print(f"PASS: cores antigas (#555, #c00) confirmadas ilegiveis em dark")


def test_cores_diferentes() -> None:
    """Os 4 helpers devem ter cores DIFERENTES entre si."""
    colors = {fn.__name__: _extract_color(fn()) for fn in (_style_ok, _style_warn, _style_err, _style_muted)}
    assert len(set(colors.values())) == 4, f"cores nao sao unicas: {colors}"
    print(f"PASS: 4 cores distintas {colors}")


if __name__ == "__main__":
    test_helpers_retornam_string()
    test_contrast_vs_dark_bg()
    test_no_legibilidade_original()
    test_cores_diferentes()
    print()
    print("PASS: toy_color_helpers")
