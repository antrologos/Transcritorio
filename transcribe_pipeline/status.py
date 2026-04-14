from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import csv

from .config import Paths


@dataclass(frozen=True)
class InterviewStatus:
    interview_id: str
    person_folder: str
    source_ext: str
    duration_sec: str
    source_audio_channels: str
    source_path: str
    wav_exists: bool
    asr_exists: bool
    diarization_regular_exists: bool
    diarization_exclusive_exists: bool
    canonical_exists: bool
    review_exists: bool
    markdown_exists: bool
    docx_exists: bool
    qc_notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def collect_status(rows: list[dict[str, str]], paths: Paths, ids: list[str] | None = None) -> list[InterviewStatus]:
    wanted = set(ids or [])
    qc_notes = read_qc_notes(paths.qc_dir / "qc_metrics.csv")
    selected_rows = [row for row in rows if row.get("selected") == "true"]
    if wanted:
        selected_rows = [row for row in selected_rows if row.get("interview_id") in wanted]

    statuses: list[InterviewStatus] = []
    for row in selected_rows:
        interview_id = row["interview_id"]
        statuses.append(
            InterviewStatus(
                interview_id=interview_id,
                person_folder=row.get("person_folder", ""),
                source_ext=row.get("source_ext", ""),
                duration_sec=row.get("duration_sec", ""),
                source_audio_channels=row.get("source_audio_channels", ""),
                source_path=row.get("source_path", ""),
                wav_exists=(paths.project_root / row.get("wav_path", "")).exists(),
                asr_exists=asr_json_exists(paths, interview_id),
                diarization_regular_exists=(paths.diarization_dir / "json" / f"{interview_id}.regular.json").exists(),
                diarization_exclusive_exists=(paths.diarization_dir / "json" / f"{interview_id}.exclusive.json").exists(),
                canonical_exists=(paths.canonical_dir / "json" / f"{interview_id}.canonical.json").exists(),
                review_exists=(paths.review_dir / "edits" / f"{interview_id}.review.json").exists(),
                markdown_exists=(paths.review_dir / "md" / f"{interview_id}.md").exists(),
                docx_exists=(paths.review_dir / "docx" / f"{interview_id}.docx").exists(),
                qc_notes=qc_notes.get(interview_id, ""),
            )
        )
    return statuses


def asr_json_exists(paths: Paths, interview_id: str) -> bool:
    candidates = [
        paths.asr_dir / f"{interview_id}.json",
        paths.asr_dir / "json" / f"{interview_id}.json",
        paths.asr_dir / "json" / f"{interview_id}.whisperx.json",
    ]
    return any(candidate.exists() for candidate in candidates)


def read_qc_notes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {row.get("interview_id", ""): row.get("notes", "") for row in csv.DictReader(handle)}

