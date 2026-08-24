"""Toy test para o fix D1.1: rota CLI aplica metadados per-arquivo.

Bug (confirmado 2026-08-23): cmd_diarize/cmd_transcribe/cmd_render usavam o
config global do projeto, ignorando metadados.csv ('Aplicar falantes', idioma,
contexto, rotulos). Como a GUI diariza via subprocesso CLI desde a v0.2, um
grupo focal configurado com 6 falantes rodava com o default do projeto (2).

Valida per_file_configs e o roteamento per-entrevista do cmd_diarize com o
runner monkeypatchado (sem torch/pyannote). Depende de numpy (import
transitivo do pacote); skip condicional se ausente (CI minimo).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline import cli, project_store
    from transcribe_pipeline.config import ensure_directories, load_config, make_paths, write_default_config
except ImportError as exc:  # CI minimo sem numpy etc.
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = load_config(None)
    config["project_root"] = "."
    paths = make_paths(config, base_dir=root)
    ensure_directories(paths)
    write_default_config(paths.config_dir / "run_config.yaml")

    rows = [
        {"interview_id": "GF01", "selected": "true", "wav_path": "a.wav", "source_path": "a.mp3"},
        {"interview_id": "EN02", "selected": "true", "wav_path": "b.wav", "source_path": "b.mp3"},
        {"interview_id": "DUP0", "selected": "false", "wav_path": "c.wav", "source_path": "c.mp3"},
    ]

    # Grupo focal: 6 falantes exatos + rotulos proprios, so no GF01
    project_store.write_file_metadata(
        project_store.metadata_path(paths),
        {
            "GF01": {
                "file_id": "GF01",
                "speaker_mode": "exact",
                "speaker_count": "6",
                "speaker_labels": "Moderador | P1 | P2 | P3 | P4 | P5",
            }
        },
    )

    pairs = cli.per_file_configs(config, paths, rows, None)
    assert [p[0] for p in pairs] == ["GF01", "EN02"], pairs  # DUP0 nao-selecionado fica fora
    by_id = dict(pairs)
    assert by_id["GF01"]["diarization_num_speakers"] == 6, by_id["GF01"]
    assert by_id["GF01"]["min_speakers"] == 6 and by_id["GF01"]["max_speakers"] == 6
    assert by_id["GF01"]["speaker_labels"][0] == "Moderador"
    # Sem metadado: preserva o config do projeto e os rotulos default
    assert by_id["EN02"]["diarization_num_speakers"] == config["diarization_num_speakers"]
    assert by_id["EN02"]["speaker_labels"] == ["Entrevistador", "Entrevistado"]
    # ids explicitos filtram
    only = cli.per_file_configs(config, paths, rows, ["EN02"])
    assert [p[0] for p in only] == ["EN02"], only
    print("PASS: per_file_configs aplica metadados por arquivo")

    # cmd_diarize passa um config POR ENTREVISTA ao runner (o bug era um
    # unico config global para todas)
    captured: list[tuple[tuple[str, ...], object]] = []

    def fake_runner(rows_arg, cfg, paths_arg, ids=None, dry_run=False, progress_callback=None, **kw):
        captured.append((tuple(ids or []), cfg.get("diarization_num_speakers")))
        return 0

    original_runner = cli.run_pyannote_diarization
    original_loader = cli.load_manifest_or_exit
    cli.run_pyannote_diarization = fake_runner
    cli.load_manifest_or_exit = lambda _paths: rows
    try:
        args = argparse.Namespace(
            project=root, config=None, ids=None, dry_run=False,
            diarization_num_speakers=None, min_speakers=None, max_speakers=None,
            diarize_model=None, progress_json=False,
        )
        rc = cli.cmd_diarize(args)
    finally:
        cli.run_pyannote_diarization = original_runner
        cli.load_manifest_or_exit = original_loader

    assert rc == 0, rc
    assert captured == [(("GF01",), 6), (("EN02",), 2)], captured
    print("PASS: cmd_diarize roteia config per-arquivo (GF01=6, EN02=2)")

print()
print("PASS: toy_cli_per_file_metadata")
