"""Toy R0 (catraca): cores hex literais na GUI so podem DIMINUIR.

Programa R: toda cor visivel deve vir de ui_tokens. Este teste le o
FONTE dos arquivos de GUI e conta literais hex fora do modulo de
tokens. O teto comeca no valor medido em 2026-08-31 e cada commit de
migracao o abaixa — subir e proibido (codigo novo nao pode inventar
cor). Ao final da reforma (R4), os tetos chegam a zero.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAIZ = Path(__file__).resolve().parents[1] / "transcribe_pipeline"

# Teto por arquivo (medido em 2026-08-31, inicio da R0). SO PODE CAIR.
TETOS = {
    "review_studio_qt.py": 41,
    "gui_launcher.py": 3,
}

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![0-9a-fA-F])")

for nome, teto in TETOS.items():
    src = (RAIZ / nome).read_text(encoding="utf-8")
    achados = HEX_RE.findall(src)
    n = len(achados)
    assert n <= teto, (
        f"{nome}: {n} cores hex literais (teto: {teto}). Cores novas devem "
        f"vir de ui_tokens; se voce migrou cores, ABAIXE o teto aqui.")
    print(f"PASS: {nome} com {n} hex literais (teto {teto})")

# ui_tokens e a UNICA casa das cores — e deve continuar puro (sem
# IMPORT de Qt; o docstring pode citar o nome).
tokens_src = (RAIZ / "ui_tokens.py").read_text(encoding="utf-8")
assert not re.search(r"^\s*(import PySide6|from PySide6)", tokens_src, re.M), \
    "ui_tokens deve permanecer puro (sem import de Qt)"
print("PASS: ui_tokens sem import de Qt")

print("PASS: toy_ui_color_ratchet")
