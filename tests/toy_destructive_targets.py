"""Toy test: escopo de acoes destrutivas (S5, incidente 2026-08-25).

A Lixeira NUNCA pode usar os checkboxes como alvo — so selecao visual/cursor.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline.review_studio_qt import (
        _compute_destructive_target_ids,
        _compute_effective_target_ids,
    )
except ImportError as exc:  # PySide6 ausente no CI minimo
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

ALL = ["A", "B", "C", "D", "E"]

# CENARIO DO INCIDENTE: todos os checkboxes marcados (default de transcricao),
# usuario mira UMA linha (selecao visual = cursor = B).
checked_all = set(ALL)
# O escopo antigo (effective) devolvia TODOS os marcados quando o cursor
# estava dentro da selecao... conferir o comportamento historico:
old = _compute_effective_target_ids(ALL, checked_all, {"B"}, "B")
# (documenta o perigo: com selecao visual coincidindo com cursor, devolvia so
# a visual; mas com cursor None — atalho Del — caia nos CHECKBOXES:)
old_no_cursor = _compute_effective_target_ids(ALL, checked_all, set(), None)
assert old_no_cursor == ALL, old_no_cursor  # <- era assim que TODAS sumiam

# O escopo novo NUNCA olha checkboxes:
new = _compute_destructive_target_ids(ALL, {"B"}, "B")
assert new == ["B"], new
new_no_cursor = _compute_destructive_target_ids(ALL, set(), None)
assert new_no_cursor == [], new_no_cursor  # nada selecionado = nada a apagar
print("PASS: checkboxes nunca viram alvo de delecao")

# Cursor fora da selecao visual -> so o cursor (padrao Explorer)
assert _compute_destructive_target_ids(ALL, {"A", "B"}, "D") == ["D"]
# Cursor dentro da selecao -> a selecao inteira, em ordem visual
assert _compute_destructive_target_ids(ALL, {"C", "A"}, "A") == ["A", "C"]
# Sem cursor, com selecao visual -> a selecao
assert _compute_destructive_target_ids(ALL, {"E", "B"}, None) == ["B", "E"]
print("PASS: precedencia visual/cursor")

print("PASS: toy_destructive_targets")
