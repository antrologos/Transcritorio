from __future__ import annotations

from typing import Any
import csv

from .config import Paths
from .manifest import selected_rows
from .utils import read_json


QC_COLUMNS = [
    "interview_id",
    "canonical_exists",
    "raw_json_exists",
    "wav_exists",
    "source_duration_sec",
    "turn_count",
    "speaker_count",
    "speaker_time_ratio_max",
    "last_timestamp_sec",
    "duration_delta_sec",
    "coverage_ratio",
    "max_gap_sec",
    "long_turns",
    "empty_turns",
    "timestamp_regressions",
    "unknown_speakers",
    "missing_human_labels",
    "raw_segment_count",
    "raw_segments_without_speaker",
    "raw_word_count",
    "raw_word_score_mean",
    "raw_avg_logprob_mean",
    "diarization_regular_exists",
    "diarization_exclusive_exists",
    "notes",
]


def run_qc(rows: list[dict[str, str]], config: dict, paths: Paths, ids: list[str] | None = None) -> int:
    output_rows: list[dict[str, str]] = []
    failures = 0
    max_turn = float(config["max_turn_seconds"])

    for row in selected_rows(rows, ids):
        interview_id = row["interview_id"]
        canonical_path = paths.canonical_dir / "json" / f"{interview_id}.canonical.json"
        raw_path = find_raw_json(paths, interview_id)
        wav_path = paths.project_root / row.get("wav_path", "")
        if not canonical_path.exists():
            failures += 1
            output_rows.append(_missing_row(interview_id, raw_path is not None, wav_path.exists(), row))
            continue

        canonical: dict[str, Any] = read_json(canonical_path)
        turns = canonical.get("turns", [])
        speakers = {turn.get("speaker", "") for turn in turns if turn.get("speaker")}
        long_turns = [turn for turn in turns if float(turn.get("end", 0)) - float(turn.get("start", 0)) > max_turn]
        missing_human = [
            turn
            for turn in turns
            if str(turn.get("human_label", "")).startswith("SPEAKER_") or not str(turn.get("human_label", ""))
        ]
        last_timestamp = max([float(turn.get("end", 0) or 0) for turn in turns] or [0.0])
        source_duration = parse_float(row.get("duration_sec", ""))
        duration_delta = source_duration - last_timestamp if source_duration is not None and last_timestamp else None
        coverage_ratio = last_timestamp / source_duration if source_duration and source_duration > 0 else None
        max_gap = max_turn_gap(turns)
        timestamp_regressions = count_timestamp_regressions(turns)
        empty_turns = [turn for turn in turns if not str(turn.get("text", "")).strip()]
        unknown_speakers = [
            turn for turn in turns if not str(turn.get("speaker", "")).strip() or str(turn.get("speaker", "")) == "SPEAKER_UNKNOWN"
        ]
        speaker_time_ratio = max_speaker_time_ratio(turns)
        raw_metrics = read_raw_metrics(raw_path) if raw_path else {}
        regular_path = paths.diarization_dir / "json" / f"{interview_id}.regular.json"
        exclusive_path = paths.diarization_dir / "json" / f"{interview_id}.exclusive.json"
        notes = qc_notes(
            speaker_count=len(speakers),
            expected_min=int(config["min_speakers"]),
            expected_max=int(config["max_speakers"]),
            duration_delta=duration_delta,
            missing_human_count=len(missing_human),
            timestamp_regressions=timestamp_regressions,
            unknown_speakers=len(unknown_speakers),
        )
        output_rows.append(
            {
                "interview_id": interview_id,
                "canonical_exists": "true",
                "raw_json_exists": str(raw_path is not None).lower(),
                "wav_exists": str(wav_path.exists()).lower(),
                "source_duration_sec": format_optional_float(source_duration),
                "turn_count": str(len(turns)),
                "speaker_count": str(len(speakers)),
                "speaker_time_ratio_max": format_optional_float(speaker_time_ratio),
                "last_timestamp_sec": f"{last_timestamp:.3f}",
                "duration_delta_sec": format_optional_float(duration_delta),
                "coverage_ratio": format_optional_float(coverage_ratio, digits=4),
                "max_gap_sec": format_optional_float(max_gap),
                "long_turns": str(len(long_turns)),
                "empty_turns": str(len(empty_turns)),
                "timestamp_regressions": str(timestamp_regressions),
                "unknown_speakers": str(len(unknown_speakers)),
                "missing_human_labels": str(len(missing_human)),
                "raw_segment_count": str(raw_metrics.get("raw_segment_count", "")),
                "raw_segments_without_speaker": str(raw_metrics.get("raw_segments_without_speaker", "")),
                "raw_word_count": str(raw_metrics.get("raw_word_count", "")),
                "raw_word_score_mean": format_optional_float(raw_metrics.get("raw_word_score_mean")),
                "raw_avg_logprob_mean": format_optional_float(raw_metrics.get("raw_avg_logprob_mean")),
                "diarization_regular_exists": str(regular_path.exists()).lower(),
                "diarization_exclusive_exists": str(exclusive_path.exists()).lower(),
                "notes": "; ".join(notes),
            }
        )

    output_path = paths.qc_dir / "qc_metrics.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=QC_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)
    return failures


def find_raw_json(paths: Paths, interview_id: str):
    candidates = [
        paths.asr_dir / f"{interview_id}.json",
        paths.asr_dir / "json" / f"{interview_id}.json",
        paths.asr_dir / "json" / f"{interview_id}.whisperx.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for candidate in paths.asr_dir.rglob(f"*{interview_id}*.json"):
        return candidate
    return None


def read_raw_metrics(path) -> dict[str, float | int]:
    raw: dict[str, Any] = read_json(path)
    segments = raw.get("segments", [])
    words: list[dict[str, Any]] = []
    avg_logprobs: list[float] = []
    for segment in segments:
        if isinstance(segment, dict):
            words.extend([word for word in segment.get("words", []) if isinstance(word, dict)])
            value = parse_float(segment.get("avg_logprob"))
            if value is not None:
                avg_logprobs.append(value)

    word_scores = [score for word in words if (score := parse_float(word.get("score"))) is not None]
    return {
        "raw_segment_count": len(segments),
        "raw_segments_without_speaker": sum(
            1 for segment in segments if not isinstance(segment, dict) or not str(segment.get("speaker", "")).strip()
        ),
        "raw_word_count": len(words),
        "raw_word_score_mean": mean(word_scores),
        "raw_avg_logprob_mean": mean(avg_logprobs),
    }


def max_turn_gap(turns: list[dict[str, Any]]) -> float | None:
    if len(turns) < 2:
        return None
    max_gap = 0.0
    previous_end = float(turns[0].get("end", 0) or 0)
    for turn in turns[1:]:
        start = float(turn.get("start", previous_end) or previous_end)
        max_gap = max(max_gap, start - previous_end)
        previous_end = float(turn.get("end", start) or start)
    return max_gap


def count_timestamp_regressions(turns: list[dict[str, Any]]) -> int:
    count = 0
    previous_start = -1.0
    for turn in turns:
        start = float(turn.get("start", 0) or 0)
        if start < previous_start:
            count += 1
        previous_start = start
    return count


def max_speaker_time_ratio(turns: list[dict[str, Any]]) -> float | None:
    durations: dict[str, float] = {}
    for turn in turns:
        speaker = str(turn.get("speaker", ""))
        duration = max(0.0, float(turn.get("end", 0) or 0) - float(turn.get("start", 0) or 0))
        durations[speaker] = durations.get(speaker, 0.0) + duration
    total = sum(durations.values())
    return max(durations.values()) / total if total > 0 and durations else None


def qc_notes(
    speaker_count: int,
    expected_min: int,
    expected_max: int,
    duration_delta: float | None,
    missing_human_count: int,
    timestamp_regressions: int,
    unknown_speakers: int,
) -> list[str]:
    notes: list[str] = []
    if speaker_count < expected_min or speaker_count > expected_max:
        notes.append(f"speaker_count_outside_expected_{expected_min}_{expected_max}")
    if duration_delta is not None and abs(duration_delta) > 10:
        notes.append("duration_delta_gt_10s")
    if missing_human_count:
        notes.append("missing_human_labels")
    if timestamp_regressions:
        notes.append("timestamp_regressions")
    if unknown_speakers:
        notes.append("unknown_speakers")
    return notes


def parse_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_optional_float(value, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _missing_row(interview_id: str, raw_exists: bool, wav_exists: bool, row: dict[str, str]) -> dict[str, str]:
    return {
        "interview_id": interview_id,
        "canonical_exists": "false",
        "raw_json_exists": str(raw_exists).lower(),
        "wav_exists": str(wav_exists).lower(),
        "source_duration_sec": row.get("duration_sec", ""),
        "turn_count": "0",
        "speaker_count": "0",
        "speaker_time_ratio_max": "",
        "last_timestamp_sec": "0",
        "duration_delta_sec": "",
        "coverage_ratio": "",
        "max_gap_sec": "",
        "long_turns": "0",
        "empty_turns": "0",
        "timestamp_regressions": "0",
        "unknown_speakers": "0",
        "missing_human_labels": "0",
        "raw_segment_count": "",
        "raw_segments_without_speaker": "",
        "raw_word_count": "",
        "raw_word_score_mean": "",
        "raw_avg_logprob_mean": "",
        "diarization_regular_exists": "false",
        "diarization_exclusive_exists": "false",
        "notes": "missing canonical JSON",
    }
