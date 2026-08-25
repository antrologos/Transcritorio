"""Toy test para review_store.backup_review_file (plano-programa v0.2.0, Fase 1).

Backup automatico antes de recriar a revisao: com trabalho humano ->
backup byte-identico; pristina -> sem backup; corrompida -> backup dos
bytes; colisao no mesmo segundo -> sufixo.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import review_store as rs
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
from transcribe_pipeline.utils import write_json

with tempfile.TemporaryDirectory() as tmp:
    config = load_config(None)
    paths = make_paths(config, base_dir=Path(tmp))
    ensure_directories(paths)
    iid = "TESTE_0001"
    canonical = {
        "interview_id": iid, "source_path": "x.wav", "source_sha256": "",
        "asr_model": "m", "diarization_model": "d",
        "diarization_source": "pyannote_exclusive", "speaker_labels": [],
        "turns": [
            {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00",
             "human_label": "A", "text": "ola tudo bem"},
        ],
    }
    write_json(rs.canonical_path(paths, iid), canonical)
    backup_dir = rs.review_path(paths, iid).parent / "backups"

    # 1) primeira criacao: nao ha arquivo -> sem backup
    review = rs.create_review_from_canonical(paths, iid)
    assert not backup_dir.exists() or not list(backup_dir.iterdir())
    print("PASS: primeira criacao sem backup")

    # 2) revisao pristina recriada -> sem backup
    rs.create_review_from_canonical(paths, iid)
    assert not backup_dir.exists() or not list(backup_dir.iterdir())
    print("PASS: pristina recriada sem backup")

    # 3) revisao com trabalho humano -> backup byte-identico
    review = rs.load_review_transcript(paths, iid)
    rs.set_turn_text(review, review["transcript"]["turns"][0]["id"], "texto editado")
    rs.save_review_transcript(paths, iid, review)
    original_bytes = rs.review_path(paths, iid).read_bytes()
    rs.create_review_from_canonical(paths, iid)
    backups = sorted(backup_dir.iterdir())
    assert len(backups) == 1, backups
    assert backups[0].read_bytes() == original_bytes
    fresh = rs.load_review_transcript(paths, iid)
    assert fresh["edits"] == [] and fresh["transcript"]["turns"][0]["text"] == "ola tudo bem"
    print("PASS: backup byte-identico + revisao nova pristina")

    # 4) turno so com edited=true (sem edits no log) tambem conta
    review = rs.load_review_transcript(paths, iid)
    review["transcript"]["turns"][0]["edited"] = True
    rs.save_review_transcript(paths, iid, review)
    rs.create_review_from_canonical(paths, iid)
    assert len(list(backup_dir.iterdir())) == 2
    print("PASS: turno edited gera backup")

    # 5) colisao no mesmo segundo -> sufixo, nada sobrescrito
    review = rs.load_review_transcript(paths, iid)
    review["edits"].append({"at": "2026-01-01T00:00:00Z", "action": "set_text", "turn_id": "turn_000001"})
    rs.save_review_transcript(paths, iid, review)
    first = rs.backup_review_file(paths, iid)
    second = rs.backup_review_file(paths, iid)
    assert first is not None and second is not None and first != second
    assert first.exists() and second.exists()
    print("PASS: colisao vira sufixo")

    # 6) review.json corrompido -> backup dos bytes antes de substituir
    rs.review_path(paths, iid).write_bytes(b"{corrompido!!!")
    count_before = len(list(backup_dir.iterdir()))
    rs.create_review_from_canonical(paths, iid)
    backups_now = sorted(backup_dir.iterdir())
    assert len(backups_now) == count_before + 1
    corrupted = [p for p in backups_now if p.read_bytes() == b"{corrompido!!!"]
    assert corrupted, "bytes corrompidos nao preservados"
    assert json.loads(rs.review_path(paths, iid).read_text(encoding="utf-8"))["edits"] == []
    print("PASS: corrompida preservada em backup")

print("PASS: toy_review_backup")
