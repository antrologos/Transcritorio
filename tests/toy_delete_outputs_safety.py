"""Toy test: "Limpar transcricao gerada" segura (SL-C3). Sem Qt.

Bugs originais: o delete apagava tambem edits/backups/ (as copias de
seguranca morriam junto), nao fazia backup previo da review editada e
deixava o indice de busca 07_index como orfao eterno. E o
collect_trash_files procurava waveforms/{id}.wf, mas o cache real e
{id}.waveform.json — a lixeira nunca o movia.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_service


def _paths(root: Path) -> SimpleNamespace:
    p = SimpleNamespace(
        project_root=root,
        output_root=root / "Transcricoes",
        asr_dir=root / "Transcricoes" / "02_asr_raw",
        asr_variants_dir=root / "Transcricoes" / "02_asr_variants",
        diarization_dir=root / "Transcricoes" / "03_diarization",
        canonical_dir=root / "Transcricoes" / "04_canonical",
        review_dir=root / "Transcricoes" / "05_transcripts_review",
        qc_dir=root / "Transcricoes" / "06_qc",
        wav_dir=root / "Transcricoes" / "01_audio_wav16k_mono",
    )
    for d in (p.asr_dir, p.asr_variants_dir, p.diarization_dir / "json",
              p.canonical_dir / "json", p.review_dir / "edits" / "backups",
              p.qc_dir, p.wav_dir, p.output_root / "07_index",
              p.output_root / "00_project" / "waveforms"):
        d.mkdir(parents=True, exist_ok=True)
    return p


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    paths = _paths(root)
    # Artefatos de E1 (com review EDITADA) e vizinho E10 (nao pode ser tocado)
    (paths.asr_dir / "E1.json").write_text("{}", encoding="utf-8")
    (paths.asr_dir / "E10.json").write_text("{}", encoding="utf-8")
    (paths.canonical_dir / "json" / "E1.canonical.json").write_text("{}", encoding="utf-8")
    review = {"schema_version": 1, "transcript": {"turns": []},
              "edits": [{"action": "set_text", "turn_id": "turn_0"}]}
    (paths.review_dir / "edits" / "E1.review.json").write_text(
        json.dumps(review), encoding="utf-8")
    backup_antigo = paths.review_dir / "edits" / "backups" / "E1.review.20250101-000000.json"
    backup_antigo.write_text("{}", encoding="utf-8")
    (paths.output_root / "07_index" / "E1.index.json").write_text("{}", encoding="utf-8")

    jobs_gravados: list[tuple[str, dict]] = []
    contexto = SimpleNamespace(paths=paths, metadata={}, jobs={})

    def _fake_update_job(ctx, iid, data):
        jobs_gravados.append((iid, dict(data)))
        return ctx

    with patch.object(app_service, "update_job", _fake_update_job):
        deleted, contexto = app_service.delete_transcription_outputs(contexto, ["E1"])

    edits = paths.review_dir / "edits"
    assert not (edits / "E1.review.json").exists(), "review deveria ser apagada"
    assert not (paths.asr_dir / "E1.json").exists()
    assert not (paths.canonical_dir / "json" / "E1.canonical.json").exists()
    assert (paths.asr_dir / "E10.json").exists(), "vizinho E10 foi apagado!"
    # backups: o antigo sobrevive E um novo foi criado (review tinha edicoes)
    assert backup_antigo.exists(), "backup antigo morreu junto"
    novos = [f for f in (edits / "backups").glob("E1.review.*.json") if f != backup_antigo]
    assert novos, "nenhum backup novo da review editada"
    # indice de busca orfao apagado
    assert not (paths.output_root / "07_index" / "E1.index.json").exists(), "indice orfao ficou"
    # job resetado
    assert jobs_gravados and jobs_gravados[-1][1]["status"] == "Pendente"
    print("PASS: delete preserva backups, faz backup previo e limpa o indice")

    # --- collect_trash_files acha o waveform cache REAL ---
    wf = paths.output_root / "00_project" / "waveforms" / "E1.waveform.json"
    wf.write_text("{}", encoding="utf-8")
    achados = app_service.collect_trash_files(contexto, ["E1"])
    originais = {Path(item["original"]).name for item in achados}
    assert "E1.waveform.json" in originais, originais
    print("PASS: lixeira enxerga o waveform cache real")

print("PASS: toy_delete_outputs_safety")
