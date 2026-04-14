"""Stamp or restore __build__ in transcribe_pipeline/__init__.py.

Usage:
    python stamp_build.py stamp   → writes current timestamp
    python stamp_build.py restore → writes "dev"
    python stamp_build.py check   → prints current __build__ value
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

INIT_FILE = Path(__file__).resolve().parent.parent / "transcribe_pipeline" / "__init__.py"
MARKER = '__build__ = '


def stamp() -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = INIT_FILE.read_text(encoding="utf-8")
    text = text.replace('__build__ = "dev"', f'__build__ = "{ts}"')
    INIT_FILE.write_text(text, encoding="utf-8")
    return ts


def restore() -> None:
    text = INIT_FILE.read_text(encoding="utf-8")
    import re
    text = re.sub(r'__build__ = "[^"]*"', '__build__ = "dev"', text)
    INIT_FILE.write_text(text, encoding="utf-8")


def check() -> str:
    for line in INIT_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(MARKER):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "stamp":
        ts = stamp()
        print(ts)
    elif cmd == "restore":
        restore()
        print("restored to dev")
    elif cmd == "check":
        print(check())
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
