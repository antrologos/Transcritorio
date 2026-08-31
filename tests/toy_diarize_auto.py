"""Toy: semantica tri-state de `diarize` ("instalado => aplicado", 2026-08-31).

Cobre model_manager.diarize_effective / local_pyannote_cached,
o tri-state de app_settings.diarize_default e o round-trip YAML do
valor "auto". Sem modelos reais: cache simulado em tempdir.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import model_manager
from transcribe_pipeline.config import DEFAULT_CONFIG, _load_simple_yaml, write_config


def fake_pyannote_cache(root: Path) -> Path:
    """Snapshot minimo que passa em _snapshot_has_weights (blob >= 4 MB)."""
    repo_dir = root / "models--pyannote--speaker-diarization-community-1"
    snap = repo_dir / "snapshots" / model_manager.LOCAL_PYANNOTE_REVISION
    snap.mkdir(parents=True)
    (repo_dir / "blobs").mkdir()
    peso = repo_dir / "blobs" / "abc123"
    peso.write_bytes(b"\0" * (5 * 1024 * 1024))
    link = snap / "pytorch_model.bin"
    link.write_bytes(peso.read_bytes())
    (snap / "config.yaml").write_text("x", encoding="utf-8")
    return root


with tempfile.TemporaryDirectory() as td:
    vazio = Path(td) / "vazio"
    vazio.mkdir()
    cheio = fake_pyannote_cache(Path(td) / "cheio")

    assert model_manager.local_pyannote_cached(vazio) is False
    assert model_manager.local_pyannote_cached(cheio) is True
    print("PASS: local_pyannote_cached")

    # --- matriz do resolver ---
    # explicitos passam direto, com ou sem modelo
    for cache in (vazio, cheio):
        assert model_manager.diarize_effective({"diarize": True}, cache) == (True, "")
        assert model_manager.diarize_effective({"diarize": False}, cache) == (False, "")
    # strings vindas de YAML antigo/manual
    assert model_manager.diarize_effective({"diarize": "true"}, vazio)[0] is True
    assert model_manager.diarize_effective({"diarize": "false"}, cheio)[0] is False
    # auto: segue a instalacao NO MOMENTO
    ok, motivo = model_manager.diarize_effective({"diarize": "auto"}, cheio)
    assert ok is True and motivo == ""
    ok, motivo = model_manager.diarize_effective({"diarize": "auto"}, vazio)
    assert ok is False and "Gerenciar modelos" in motivo
    # ausente/None/config vazia = auto
    assert model_manager.diarize_effective({}, cheio)[0] is True
    assert model_manager.diarize_effective({"diarize": None}, vazio)[0] is False
    assert model_manager.diarize_effective(None, cheio)[0] is True
    print("PASS: diarize_effective (matriz completa)")

# --- app_settings tri-state (HOME redirecionado) ---
with tempfile.TemporaryDirectory() as td:
    os.environ["TRANSCRITORIO_HOME"] = td
    try:
        import importlib
        from transcribe_pipeline import runtime as _rt, app_settings
        importlib.reload(_rt)
        importlib.reload(app_settings)
        # sem arquivo: auto
        assert app_settings.diarize_default() == "auto"
        # true persistido (padrao/completo antigos): continua true
        settings_path = Path(td) / "app_settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"diarize_default": True}), encoding="utf-8")
        assert app_settings.diarize_default() is True
        # false persistido era ARTEFATO do perfil Essencial ("nao instalado
        # agora"), nunca preferencia — vira auto
        settings_path.write_text(json.dumps({"diarize_default": False}), encoding="utf-8")
        assert app_settings.diarize_default() == "auto"
        # "auto" persistido (wizard novo)
        settings_path.write_text(json.dumps({"diarize_default": "auto"}), encoding="utf-8")
        assert app_settings.diarize_default() == "auto"
    finally:
        del os.environ["TRANSCRITORIO_HOME"]
        importlib.reload(_rt)
        importlib.reload(app_settings)
print("PASS: app_settings.diarize_default tri-state")

# --- default novo + round-trip YAML ---
assert DEFAULT_CONFIG["diarize"] == "auto"
with tempfile.TemporaryDirectory() as td:
    cfg_path = Path(td) / "run_config.yaml"
    cfg = dict(DEFAULT_CONFIG)
    write_config(cfg_path, cfg, header=["# toy"])
    lido = _load_simple_yaml(cfg_path.read_text(encoding="utf-8"))
    assert lido["diarize"] == "auto", lido.get("diarize")
    # explicitos tambem sobrevivem
    cfg["diarize"] = False
    write_config(cfg_path, cfg, header=["# toy"])
    lido = _load_simple_yaml(cfg_path.read_text(encoding="utf-8"))
    assert lido["diarize"] is False
print("PASS: 'auto' sobrevive ao round-trip do YAML")

print("PASS: toy_diarize_auto")
