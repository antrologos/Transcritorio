"""Toy tests for Iteration B pure helpers.

Validates:
- _sanitize_rename_title: strip + isprintable + truncate 200
- _reorder_move: swap preservando hidden ao fim
- _merge_interview_order: keep ordering, append new ids
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline.review_studio_qt import (
    MAX_TITLE_CHARS,
    _sanitize_rename_title,
    _reorder_move,
    _merge_interview_order,
)


def test_rename_strip_whitespace() -> None:
    assert _sanitize_rename_title("   ") == ("", False)
    assert _sanitize_rename_title("  joao  ") == ("joao", False)
    assert _sanitize_rename_title("\t\n entrevista \t") == ("entrevista", False)
    print("PASS rename: whitespace tratado")


def test_rename_rejects_nonprintable() -> None:
    title, truncated = _sanitize_rename_title("nome\x00zero")
    assert title == "nomezero", title
    assert not truncated
    title, _ = _sanitize_rename_title("a\x07b\x1fc")
    assert title == "abc", title
    print("PASS rename: controles removidos")


def test_rename_unicode_acentos() -> None:
    title, _ = _sanitize_rename_title("Joao - entrevista 2026-04-19")
    assert title == "Joao - entrevista 2026-04-19", title
    title, _ = _sanitize_rename_title("sessao piloto 2a")
    assert title == "sessao piloto 2a", title
    print("PASS rename: unicode e numeros preservados")


def test_rename_truncate() -> None:
    raw = "a" * 300
    title, truncated = _sanitize_rename_title(raw)
    assert len(title) == MAX_TITLE_CHARS == 200, (len(title), MAX_TITLE_CHARS)
    assert truncated is True
    title_ok, truncated_ok = _sanitize_rename_title("b" * 150)
    assert len(title_ok) == 150
    assert truncated_ok is False
    print("PASS rename: truncate em 200 com flag")


def test_rename_empty_and_none() -> None:
    assert _sanitize_rename_title("") == ("", False)
    assert _sanitize_rename_title(None) == ("", False)
    print("PASS rename: None/vazio -> reset")


def test_reorder_up_middle() -> None:
    r = _reorder_move(["a", "b", "c", "d"], "c", -1)
    assert r == ["a", "c", "b", "d"], r
    print("PASS reorder: up no meio")


def test_reorder_down_middle() -> None:
    r = _reorder_move(["a", "b", "c", "d"], "b", +1)
    assert r == ["a", "c", "b", "d"], r
    print("PASS reorder: down no meio")


def test_reorder_noop_at_top() -> None:
    r = _reorder_move(["a", "b", "c"], "a", -1)
    assert r == ["a", "b", "c"], r
    print("PASS reorder: up no topo = no-op")


def test_reorder_noop_at_bottom() -> None:
    r = _reorder_move(["a", "b", "c"], "c", +1)
    assert r == ["a", "b", "c"], r
    print("PASS reorder: down no fim = no-op")


def test_reorder_skips_hidden() -> None:
    # hidden "b" separa a e c; mover c para cima deve trocar com a (pular b oculto)
    r = _reorder_move(["a", "b", "c"], "c", -1, hidden_ids={"b"})
    assert r == ["c", "b", "a"], r
    print("PASS reorder: pula oculto no swap")


def test_reorder_noop_when_only_visible() -> None:
    # So "b" visivel, resto oculto: nao ha para onde mover
    r = _reorder_move(["a", "b", "c"], "b", -1, hidden_ids={"a", "c"})
    assert r == ["a", "b", "c"], r
    print("PASS reorder: unico visivel = no-op")


def test_reorder_moving_hidden_is_noop() -> None:
    r = _reorder_move(["a", "b", "c"], "b", -1, hidden_ids={"b"})
    assert r == ["a", "b", "c"], r
    print("PASS reorder: mover id oculto = no-op")


def test_reorder_missing_id() -> None:
    r = _reorder_move(["a", "b", "c"], "z", -1)
    assert r == ["a", "b", "c"], r
    print("PASS reorder: id inexistente = no-op")


def test_merge_preserve_ordering() -> None:
    r = _merge_interview_order(["a", "b", "c"], ["c", "b", "a"])
    assert r == ["a", "b", "c"], r
    print("PASS merge: preserva ordem existente")


def test_merge_appends_new() -> None:
    r = _merge_interview_order(["a", "b"], ["a", "c", "b", "d"])
    assert r == ["a", "b", "c", "d"], r
    print("PASS merge: novos ids no fim na ordem atual")


def test_merge_drops_removed() -> None:
    r = _merge_interview_order(["a", "b", "c"], ["a", "c"])
    assert r == ["a", "c"], r
    print("PASS merge: ids removidos somem")


def test_merge_empty_existing() -> None:
    r = _merge_interview_order([], ["a", "b", "c"])
    assert r == ["a", "b", "c"], r
    print("PASS merge: existing vazio = ordem atual")


if __name__ == "__main__":
    test_rename_strip_whitespace()
    test_rename_rejects_nonprintable()
    test_rename_unicode_acentos()
    test_rename_truncate()
    test_rename_empty_and_none()
    test_reorder_up_middle()
    test_reorder_down_middle()
    test_reorder_noop_at_top()
    test_reorder_noop_at_bottom()
    test_reorder_skips_hidden()
    test_reorder_noop_when_only_visible()
    test_reorder_moving_hidden_is_noop()
    test_reorder_missing_id()
    test_merge_preserve_ordering()
    test_merge_appends_new()
    test_merge_drops_removed()
    test_merge_empty_existing()
    print()
    print("PASS: toy_rename_reorder")
