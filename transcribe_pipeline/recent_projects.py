"""Manage the user-level recent projects list (cross-platform via runtime.app_data_dir)."""
from __future__ import annotations

import json
from pathlib import Path

MAX_RECENT = 10
CONFIG_FILENAME = "recent_projects.json"


def _config_path() -> Path:
    from . import runtime
    return runtime.app_data_dir() / CONFIG_FILENAME


def load_recent() -> list[Path]:
    path = _config_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Path(p) for p in data.get("recent", []) if Path(p).exists()]
    except Exception:
        return []


def save_recent(project_root: Path) -> None:
    config_dir = _config_path().parent
    config_dir.mkdir(parents=True, exist_ok=True)
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    recent = data.get("recent", [])
    root_str = str(project_root.resolve())
    if root_str in recent:
        recent.remove(root_str)
    recent.insert(0, root_str)
    data["recent"] = recent[:MAX_RECENT]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
