"""Toy R2: BannerArea — slot unico com prioridade."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QFrame

from transcribe_pipeline.ui_banners import BannerArea

app = QApplication.instance() or QApplication([])

area = BannerArea()
urgente, medio, leve = QFrame(), QFrame(), QFrame()
area.add_banner("urgente", urgente, prioridade=0)
area.add_banner("medio", medio, prioridade=1)
area.add_banner("leve", leve, prioridade=2)
area.show()

# ninguem quer aparecer: tudo oculto, inclusive a area
assert not urgente.isVisible() and not medio.isVisible() and not leve.isVisible()
print("PASS: inicio tudo oculto")

# so o leve quer: aparece
area.set_wanted("leve", True)
assert leve.isVisible() and not medio.isVisible()
print("PASS: unico interessado aparece")

# o urgente entra: ganha o slot; o leve espera a vez
area.set_wanted("urgente", True)
assert urgente.isVisible() and not leve.isVisible() and not medio.isVisible()
print("PASS: prioridade vence (um por vez)")

# o urgente sai: o leve volta sozinho
area.set_wanted("urgente", False)
assert leve.isVisible() and not urgente.isVisible()
assert area.wanted("leve") and not area.wanted("urgente")
print("PASS: fila anda quando o vencedor sai")

# todos saem: area some
area.set_wanted("leve", False)
assert not area.isVisible()
print("PASS: area some sem banners")

# chave desconhecida nao explode
area.set_wanted("inexistente", True)
print("PASS: chave desconhecida ignorada")

print("PASS: toy_ui_banners")
