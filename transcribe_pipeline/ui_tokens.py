"""Tokens visuais do Transcritorio — fonte unica de cor/tipo/espaco.

Programa R (reforma de UI), paleta aprovada pelo autor em 2026-08-31
(dossie docs/UI_REFORMA_DESIGN.md, secao 5). Regras:

- Modulo PURO: nenhum import de PySide6 — o gui_launcher pode importa-lo
  antes do splash sem custo, e ele e testavel sem Qt (toy_ui_tokens
  vigia a pureza via sys.modules).
- Toda cor visivel do app deve vir daqui. O teste-catraca
  (toy_ui_color_ratchet) conta hex literais fora deste modulo e o
  numero so pode CAIR.
- As 4 cores semanticas (INFO/WARN/DANGER/SUCCESS) sao separadas do
  ACCENT; AI e exclusiva do que e ✨ AI assistiva.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Cores (12 tokens; tema escuro unico — decisao de projeto)
# ---------------------------------------------------------------------------

BG_BASE = "#1b1e23"      # fundo da janela
BG_RAISED = "#23272e"    # paineis, tabelas
BG_OVERLAY = "#2b3038"   # popovers, linhas hover
BORDER = "#3a4048"       # bordas
TEXT = "#e6e8eb"         # texto principal
TEXT_MUTED = "#9aa0a8"   # texto secundario
ACCENT = "#44d7b6"       # acao primaria (teal)
INFO = "#4dabf7"         # banners informativos
WARN = "#ffa94d"         # avisos
DANGER = "#e5534b"       # destrutivas / erro
SUCCESS = "#2ea043"      # pronto / salvo / acelerado
AI = "#b197fc"           # tudo que e ✨ AI assistiva

ALL_COLORS: dict[str, str] = {
    "BG_BASE": BG_BASE, "BG_RAISED": BG_RAISED, "BG_OVERLAY": BG_OVERLAY,
    "BORDER": BORDER, "TEXT": TEXT, "TEXT_MUTED": TEXT_MUTED,
    "ACCENT": ACCENT, "INFO": INFO, "WARN": WARN, "DANGER": DANGER,
    "SUCCESS": SUCCESS, "AI": AI,
}

# ---------------------------------------------------------------------------
# Derivadas e paletas especiais (nao sao "tokens novos": sao derivacoes
# de componente e conjuntos de data-viz que acompanham a paleta)
# ---------------------------------------------------------------------------

ACCENT_HOVER = "#5ae0c4"   # hover do botao primario
ON_ACCENT = "#0f1419"      # texto sobre fundo ACCENT
# Variantes para TEXTO PEQUENO sobre fundo escuro: SUCCESS/DANGER puros
# ficam abaixo de WCAG AA (4,5:1) como texto de 13px; estas clareadas
# passam com folga. Os tokens-base seguem para chips, bordas e fundos.
SUCCESS_TEXT = "#3fb950"
DANGER_TEXT = "#f47067"
VIDEO_BG = "#111111"       # moldura do video (quase-preto proposital)
HIGHLIGHT_BG = "#fff3bf"   # realce de busca (fundo claro)
HIGHLIGHT_TEXT = "#1f2933" # texto sobre o realce

# Cores de VOZ (chips por falante) — conjunto de matizes distintos,
# estavel por indice de voz; separado dos 12 tokens semanticos.
VOICE_COLORS = ["#4dabf7", "#69db7c", "#ffa94d", "#e599f7",
                "#ffd43b", "#63e6be", "#ff8787", "#a5d8ff"]

# Paleta do waveform (grafico): herdada do desenho atual, ja harmonica
# com a familia INFO. Centralizada aqui para a catraca chegar a zero.
WAVEFORM = {
    "bg": "#0f1720",
    "ruler_text": "#9aa4ad",
    "grid": "#56616d",
    "label": "#c7d0d9",
    "cursor_line": "#5cb7ee",
    "cursor_fill": "#2f9bd3",
    "tick": "#4d5d6c",
    "tick_uncertain": "#e0a83c",
    "block_dash": "#ffffff",
    "range": "#ffcc33",
    "time_text": "#d8dee9",
}

# ---------------------------------------------------------------------------
# Espacamento e tipografia
# ---------------------------------------------------------------------------

SP_1, SP_2, SP_3, SP_4, SP_5 = 4, 8, 12, 16, 24

FONT_CAPTION = 11   # dicas, rodapes
FONT_BODY = 13      # padrao
FONT_TITLE = 16     # titulos de painel/aba (peso 600)
FONT_HERO = 18      # empty-states (peso 700)


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#rrggbb' -> (r, g, b). Levanta ValueError para formato invalido."""
    value = color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"cor fora do formato #rrggbb: {color!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(color: str, alpha: float) -> str:
    """'#rrggbb' + alpha -> 'rgba(r, g, b, a)' para uso em QSS."""
    r, g, b = hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def banner_style(color: str) -> str:
    """QSS de banner pela formula unica do dossie: fundo 14%, borda 45%.

    Substitui os rgba() repetidos a mao nos tres banners antigos.
    """
    return (f"background: {rgba(color, 0.14)}; "
            f"border: 1px solid {rgba(color, 0.45)}; "
            "border-radius: 6px;")


def relative_luminance(color: str) -> float:
    """Luminancia relativa WCAG (0=preto, 1=branco) — usada nos testes
    de contraste da paleta."""
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = hex_to_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """Razao de contraste WCAG entre duas cores."""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
