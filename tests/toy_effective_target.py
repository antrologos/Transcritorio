"""Toy test: _compute_effective_target_ids (regras Windows Explorer).

Cenarios:
1. So checkbox -> retorna checkboxes em ordem visual
2. So selecao visual -> retorna selecao em ordem visual
3. Cursor fora de ambos (checkbox + selecao) -> retorna so cursor
4. Cursor dentro da selecao visual -> retorna selecao visual inteira
5. Checkbox e selecao visual divergem, sem cursor -> checkbox vence
6. Nada selecionado -> []

Executar:
    "%LOCALAPPDATA%\\Transcritorio\\transcricao-venv\\Scripts\\python.exe" -B tests/toy_effective_target.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline.review_studio_qt import _compute_effective_target_ids


ALL = ["a", "b", "c", "d", "e"]


def test_only_checkbox() -> None:
    r = _compute_effective_target_ids(ALL, checked={"a", "c"}, visually_selected=set())
    assert r == ["a", "c"], r
    print("PASS only checkbox:", r)


def test_only_visual() -> None:
    r = _compute_effective_target_ids(ALL, checked=set(), visually_selected={"b", "d"})
    assert r == ["b", "d"], r
    print("PASS only visual:", r)


def test_cursor_outside_both() -> None:
    r = _compute_effective_target_ids(
        ALL, checked={"a"}, visually_selected={"b"}, cursor_row_id="c"
    )
    assert r == ["c"], f"explorer: cursor fora vence, got {r}"
    print("PASS cursor fora de ambos:", r)


def test_cursor_inside_visual() -> None:
    r = _compute_effective_target_ids(
        ALL, checked=set(), visually_selected={"b", "c", "d"}, cursor_row_id="c"
    )
    assert r == ["b", "c", "d"], r
    print("PASS cursor dentro da selecao:", r)


def test_cursor_inside_checkbox_but_no_visual() -> None:
    r = _compute_effective_target_ids(
        ALL, checked={"a", "b"}, visually_selected=set(), cursor_row_id="a"
    )
    assert r == ["a", "b"], f"cursor dentro do checkbox sem visual -> checkbox inteiro, got {r}"
    print("PASS cursor dentro do checkbox sem visual:", r)


def test_conflict_no_cursor() -> None:
    r = _compute_effective_target_ids(
        ALL, checked={"a"}, visually_selected={"b"}
    )
    assert r == ["a"], f"checkbox vence selecao visual quando nao ha cursor, got {r}"
    print("PASS conflito sem cursor (checkbox vence):", r)


def test_empty() -> None:
    r = _compute_effective_target_ids(ALL, checked=set(), visually_selected=set())
    assert r == [], r
    print("PASS vazio:", r)


def test_order_preserved() -> None:
    r = _compute_effective_target_ids(
        ["z", "a", "m", "b"], checked={"a", "b"}, visually_selected=set()
    )
    assert r == ["a", "b"], f"ordem visual preservada (a antes de b), got {r}"
    print("PASS ordem visual preservada:", r)


if __name__ == "__main__":
    test_only_checkbox()
    test_only_visual()
    test_cursor_outside_both()
    test_cursor_inside_visual()
    test_cursor_inside_checkbox_but_no_visual()
    test_conflict_no_cursor()
    test_empty()
    test_order_preserved()
    print()
    print("PASS: toy_effective_target")
