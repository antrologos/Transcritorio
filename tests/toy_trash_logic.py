"""Toy tests for Iteration C pure helpers.

Cobre:
- CollisionError + _find_collisions
- _build_undo_entry / formato undo.json
- Stack state machine (undo/redo push/pop/clear_redo)
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline import project_store


def test_find_collisions_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        moved = [
            {"original": str(Path(tmp) / "a.mp3"), "size": 100},
            {"original": str(Path(tmp) / "b.mp3"), "size": 200},
        ]
        conflicts = project_store._find_collisions(moved)
        assert conflicts == [], f"esperava vazio, got {conflicts}"
        print("PASS collision: paths inexistentes = sem conflito")


def test_find_collisions_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.mp3"
        a.write_bytes(b"x" * 50)
        b = Path(tmp) / "b.mp3"
        moved = [
            {"original": str(a), "size": 100},
            {"original": str(b), "size": 200},
        ]
        conflicts = project_store._find_collisions(moved)
        assert len(conflicts) == 1, f"esperava 1 conflito, got {len(conflicts)}"
        assert conflicts[0]["original"] == str(a)
        assert conflicts[0]["size_now"] == 50
        assert conflicts[0]["size_was"] == 100
        assert "mtime_now" in conflicts[0]
        print("PASS collision: detecta arquivo existente com size/mtime")


def test_undo_entry_minimal() -> None:
    entry = project_store._build_undo_entry(
        trash_id="20260420T120000_abcd",
        interview_ids=["iv1", "iv2"],
        csv_mtimes={"manifest.csv": 1000.0},
        snapshots={"manifest_rows": [{"interview_id": "iv1"}]},
        moved_files=[{"original": "path/a.mp3", "trashed": "files/a.mp3", "size": 100}],
    )
    assert entry["trash_id"] == "20260420T120000_abcd"
    assert entry["status"] == "complete"
    assert entry["interview_ids"] == ["iv1", "iv2"]
    assert "created_at" in entry
    assert entry["csv_mtimes"]["manifest.csv"] == 1000.0
    print("PASS undo_entry: formato completo com defaults")


def test_undo_entry_mark_partial() -> None:
    entry = project_store._build_undo_entry(
        trash_id="x",
        interview_ids=["iv1"],
        csv_mtimes={},
        snapshots={},
        moved_files=[],
        status="partial",
        pending_deletes=["orig/a.mp3"],
    )
    assert entry["status"] == "partial"
    assert entry["pending_deletes"] == ["orig/a.mp3"]
    print("PASS undo_entry: status partial preserva pending_deletes")


class TrashStackSim:
    """Espelha a logica da GUI: _trash_undo e _trash_redo + regras de clear."""
    def __init__(self) -> None:
        self.undo: list[str] = []
        self.redo: list[str] = []

    def push_trash(self, tid: str) -> None:
        self.undo.append(tid)
        self.redo.clear()

    def do_undo(self) -> str | None:
        if not self.undo:
            return None
        tid = self.undo.pop()
        self.redo.append(tid)
        return tid

    def do_redo(self) -> str | None:
        if not self.redo:
            return None
        tid = self.redo.pop()
        self.undo.append(tid)
        return tid

    def invalidate_redo(self) -> None:
        self.redo.clear()


def test_stack_push_clears_redo() -> None:
    s = TrashStackSim()
    s.push_trash("t1")
    s.do_undo()  # redo = ['t1']
    assert s.redo == ["t1"]
    s.push_trash("t2")  # deve limpar redo
    assert s.redo == []
    assert s.undo == ["t2"]
    print("PASS stack: push limpa redo")


def test_stack_undo_redo_roundtrip() -> None:
    s = TrashStackSim()
    s.push_trash("t1")
    s.push_trash("t2")
    assert s.undo == ["t1", "t2"]
    assert s.do_undo() == "t2"
    assert s.undo == ["t1"] and s.redo == ["t2"]
    assert s.do_undo() == "t1"
    assert s.undo == [] and s.redo == ["t2", "t1"]
    assert s.do_redo() == "t1"
    assert s.undo == ["t1"] and s.redo == ["t2"]
    print("PASS stack: undo/redo preserva ordem LIFO")


def test_stack_undo_empty() -> None:
    s = TrashStackSim()
    assert s.do_undo() is None
    assert s.do_redo() is None
    print("PASS stack: empty returns None")


def test_stack_invalidate_redo() -> None:
    s = TrashStackSim()
    s.push_trash("t1")
    s.do_undo()  # redo = ['t1']
    s.invalidate_redo()  # outra acao (rename, move) deve limpar
    assert s.redo == []
    print("PASS stack: invalidate_redo por outra acao")


if __name__ == "__main__":
    test_find_collisions_empty()
    test_find_collisions_exists()
    test_undo_entry_minimal()
    test_undo_entry_mark_partial()
    test_stack_push_clears_redo()
    test_stack_undo_redo_roundtrip()
    test_stack_undo_empty()
    test_stack_invalidate_redo()
    print()
    print("PASS: toy_trash_logic")
