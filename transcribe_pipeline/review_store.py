from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import re

from .config import Paths
from .render import write_docx_if_available, write_markdown, write_nvivo_tsv, write_srt, write_turns_csv, write_turns_tsv, write_vtt
from .utils import now_utc, read_json, write_json


REVIEW_SCHEMA_VERSION = 1
TURN_ID_RE = re.compile(r"^turn_(\d+)$")


def review_path(paths: Paths, interview_id: str) -> Path:
    return paths.review_dir / "edits" / f"{interview_id}.review.json"


def canonical_path(paths: Paths, interview_id: str) -> Path:
    return paths.canonical_dir / "json" / f"{interview_id}.canonical.json"


def load_canonical_transcript(paths: Paths, interview_id: str) -> dict[str, Any]:
    path = canonical_path(paths, interview_id)
    if not path.exists():
        raise FileNotFoundError(f"Missing canonical transcript: {path}")
    return read_json(path)


def create_review_from_canonical(paths: Paths, interview_id: str, reviewer: str = "") -> dict[str, Any]:
    canonical = load_canonical_transcript(paths, interview_id)
    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_status": "draft",
        "reviewer": reviewer,
        "source": {
            "canonical_path": str(canonical_path(paths, interview_id)),
            "interview_id": interview_id,
        },
        "transcript": deepcopy(canonical),
        "edits": [],
    }
    normalize_review(review)
    save_review_transcript(paths, interview_id, review)
    return review


def load_review_transcript(paths: Paths, interview_id: str, create: bool = True) -> dict[str, Any]:
    path = review_path(paths, interview_id)
    if path.exists():
        review = read_json(path)
        before = deepcopy(review)
        normalized = normalize_review(review)
        if normalized != before:
            write_json(path, normalized)
        return normalized
    if create:
        return create_review_from_canonical(paths, interview_id)
    raise FileNotFoundError(f"Missing review transcript: {path}")


def save_review_transcript(paths: Paths, interview_id: str, review: dict[str, Any]) -> None:
    normalize_review(review)
    review["updated_at"] = now_utc()
    path = review_path(paths, interview_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, review)


def review_to_canonical(review: dict[str, Any]) -> dict[str, Any]:
    normalize_review(review)
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        raise ValueError("Review file does not contain a transcript object.")
    return transcript


def export_review_outputs(paths: Paths, interview_id: str, formats: list[str] | None = None) -> list[Path]:
    formats = [item.lower() for item in (formats or ["md", "docx", "srt", "vtt", "csv", "tsv", "nvivo"])]
    review = load_review_transcript(paths, interview_id, create=False)
    canonical = review_to_canonical(review)
    output_dir = paths.review_dir / "final"
    exported: list[Path] = []

    if "md" in formats:
        path = output_dir / "md" / f"{interview_id}.reviewed.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(path, canonical)
        exported.append(path)
    if "docx" in formats:
        path = output_dir / "docx" / f"{interview_id}.reviewed.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_docx_if_available(path, canonical)
        if path.exists():
            exported.append(path)
    if "srt" in formats:
        path = output_dir / "srt" / f"{interview_id}.reviewed.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_srt(path, canonical)
        exported.append(path)
    if "vtt" in formats:
        path = output_dir / "vtt" / f"{interview_id}.reviewed.vtt"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_vtt(path, canonical)
        exported.append(path)
    if "csv" in formats:
        path = output_dir / "csv" / f"{interview_id}.reviewed.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_turns_csv(path, canonical)
        exported.append(path)
    if "tsv" in formats:
        path = output_dir / "tsv" / f"{interview_id}.reviewed.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_turns_tsv(path, canonical)
        exported.append(path)
    if "nvivo" in formats:
        path = output_dir / "nvivo" / f"{interview_id}.reviewed_nvivo.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_nvivo_tsv(path, canonical)
        exported.append(path)
    return exported


def normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    review.setdefault("schema_version", REVIEW_SCHEMA_VERSION)
    review.setdefault("review_status", "draft")
    review.setdefault("edits", [])
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        return review
    turns = transcript.get("turns", [])
    if not isinstance(turns, list):
        transcript["turns"] = []
        return review
    seen: set[str] = set()
    next_id = 1
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            turns[index] = {}
            turn = turns[index]
        turn_id = str(turn.get("id") or "").strip()
        if not turn_id or turn_id in seen:
            while f"turn_{next_id:06d}" in seen:
                next_id += 1
            turn_id = f"turn_{next_id:06d}"
        turn["id"] = turn_id
        seen.add(turn_id)
        if "flags" not in turn or not isinstance(turn.get("flags"), list):
            turn["flags"] = []
        if "notes" not in turn:
            turn["notes"] = ""
        if "edited" not in turn:
            turn["edited"] = False
    return review


def find_turn_index(review: dict[str, Any], turn_id: str) -> int:
    turns = review_turns(review)
    for index, turn in enumerate(turns):
        if turn.get("id") == turn_id:
            return index
    raise KeyError(f"Turn not found: {turn_id}")


def review_turns(review: dict[str, Any]) -> list[dict[str, Any]]:
    normalize_review(review)
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        raise ValueError("Review file does not contain a transcript object.")
    turns = transcript.get("turns")
    if not isinstance(turns, list):
        raise ValueError("Review transcript does not contain a turns list.")
    return turns


def set_turn_text(review: dict[str, Any], turn_id: str, text: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["text"] = str(text).strip()
    turn["edited"] = True
    record_edit(review, "set_text", turn_id)


def set_turn_speaker_label(review: dict[str, Any], turn_id: str, human_label: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["human_label"] = human_label
    turn["edited"] = True
    record_edit(review, "set_speaker", turn_id)


def set_turn_times(review: dict[str, Any], turn_id: str, start: float, end: float) -> None:
    if start < 0:
        raise ValueError("O tempo inicial nao pode ser negativo.")
    if end <= start:
        raise ValueError("O tempo final precisa ser maior que o tempo inicial.")
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["start"] = round(float(start), 3)
    turn["end"] = round(float(end), 3)
    turn["edited"] = True
    record_edit(review, "set_times", turn_id)


def toggle_turn_flag(review: dict[str, Any], turn_id: str, flag: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    flags = set(str(item) for item in turn.get("flags", []))
    if flag in flags:
        flags.remove(flag)
    else:
        flags.add(flag)
    turn["flags"] = sorted(flags)
    turn["edited"] = True
    record_edit(review, "toggle_flag", turn_id)


def set_turn_flags(review: dict[str, Any], turn_id: str, flags: list[str]) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["flags"] = sorted({str(flag).strip() for flag in flags if str(flag).strip()})
    turn["edited"] = True
    record_edit(review, "set_flags", turn_id)


def merge_turn_with_next(review: dict[str, Any], turn_id: str) -> str:
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    if index >= len(turns) - 1:
        raise ValueError("Cannot merge the last turn with a next turn.")
    current = turns[index]
    following = turns.pop(index + 1)
    if turn_speaker_key(current) != turn_speaker_key(following):
        turns.insert(index + 1, following)
        raise ValueError("Nao e possivel fundir turnos de falantes diferentes. Troque o falante primeiro, se essa for a correcao desejada.")
    current["end"] = max(float(current.get("end", 0) or 0), float(following.get("end", 0) or 0))
    current["text"] = " ".join([str(current.get("text", "")).strip(), str(following.get("text", "")).strip()]).strip()
    current["flags"] = sorted(set(current.get("flags", [])) | set(following.get("flags", [])))
    current["notes"] = " ".join([str(current.get("notes", "")).strip(), str(following.get("notes", "")).strip()]).strip()
    current["edited"] = True
    record_edit(review, "merge_next", str(current["id"]))
    return str(current["id"])


def split_turn(review: dict[str, Any], turn_id: str, split_time: float | None = None, split_char: int | None = None) -> str:
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    current = turns[index]
    text = str(current.get("text", "")).strip()
    split_char = choose_split_char(text, split_char)
    left_text = text[:split_char].strip()
    right_text = text[split_char:].strip()
    if not left_text or not right_text:
        raise ValueError("Choose a split point inside the text.")

    start = float(current.get("start", 0) or 0)
    end = float(current.get("end", start) or start)
    if split_time is None or split_time <= start or split_time >= end:
        split_time = start + ((end - start) * (split_char / max(1, len(text))))

    next_id = next_turn_id(turns)
    new_turn = deepcopy(current)
    current["end"] = split_time
    current["text"] = left_text
    current["edited"] = True
    new_turn["id"] = next_id
    new_turn["start"] = split_time
    new_turn["text"] = right_text
    new_turn["edited"] = True
    turns.insert(index + 1, new_turn)
    record_edit(review, "split", str(current["id"]))
    return next_id


def choose_split_char(text: str, requested: int | None) -> int:
    if requested is not None and 0 < requested < len(text):
        return requested
    midpoint = len(text) // 2
    left = text.rfind(" ", 0, midpoint)
    right = text.find(" ", midpoint)
    if left <= 0 and right <= 0:
        return midpoint
    if left <= 0:
        return right
    if right <= 0:
        return left
    return left if midpoint - left <= right - midpoint else right


def next_turn_id(turns: list[dict[str, Any]]) -> str:
    highest = 0
    for turn in turns:
        match = TURN_ID_RE.match(str(turn.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"turn_{highest + 1:06d}"


def turn_speaker_key(turn: dict[str, Any]) -> str:
    label = str(turn.get("human_label") or "").strip()
    if label:
        return " ".join(label.split()).upper()
    return str(turn.get("speaker", "SPEAKER_UNKNOWN"))


def record_edit(review: dict[str, Any], action: str, turn_id: str) -> None:
    review.setdefault("edits", []).append({"at": now_utc(), "action": action, "turn_id": turn_id})
