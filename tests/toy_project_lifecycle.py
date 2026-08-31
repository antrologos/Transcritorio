"""Toy test: ciclo de vida do projeto (lote primeiro-contato).

Cobre os defeitos do 1o teste real (2026-08-30): projeto novo herdando
as escolhas do assistente, descritor sem mangling, LEIA-MEs, e
open_project sem criar projeto em pasta alheia.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp_home = tempfile.mkdtemp(prefix="proj_lifecycle_home_")
os.environ["TRANSCRITORIO_HOME"] = _tmp_home
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")

from transcribe_pipeline import app_service, app_settings, project_store
from transcribe_pipeline.config import load_config, make_paths

# --- app_settings.asr_model_default: valida contra o registro ---
assert app_settings.asr_model_default() == "large-v3-turbo"   # sem escolha: fabrica
app_settings.save({"asr_model_default": "tiny"})
assert app_settings.asr_model_default() == "tiny"
app_settings.save({"asr_model_default": "modelo-que-nao-existe"})
assert app_settings.asr_model_default() == "large-v3-turbo"   # invalido: fabrica
app_settings.save({"asr_model_default": "tiny", "diarize_default": False})
print("PASS: asr_model_default")

# --- create_project herda as escolhas da maquina ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Meu Projeto.transcricao"
    context = app_service.create_project(root, project_name="Meu Projeto")
    assert context.config["asr_model"] == "tiny", context.config["asr_model"]
    # Tri-state 2026-08-31: False do wizard antigo vira "auto" — o
    # projeto passa a separar falantes assim que o modelo for instalado,
    # em vez de congelar "sem falantes" para sempre.
    assert context.config["diarize"] == "auto", context.config["diarize"]
    # o run_config gravado tambem (nao so o objeto em memoria)
    texto = (root / "Transcricoes" / "00_config" / "run_config.yaml").read_text(encoding="utf-8")
    assert "asr_model: tiny" in texto and "diarize: auto" in texto.lower()

    # descritor sem mangling: "Meu Projeto.transcritorio"
    descritores = sorted(p.name for p in root.glob("*.transcritorio"))
    assert descritores == ["Meu Projeto.transcritorio"], descritores

    # LEIA-MEs: raiz (modelo mental) e Transcricoes (pasta tecnica)
    raiz = (root / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert "NÃO são copiados" in raiz and "outro computador" in raiz
    tecnica = (root / "Transcricoes" / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert "NÃO precisa abrir" in tecnica and "Resultados" in tecnica
    # nunca sobrescrever edicao do usuario
    (root / "LEIA-ME.txt").write_text("minhas notas", encoding="utf-8")
    project_store.write_project_readme_if_missing(
        make_paths(load_config(root / "Transcricoes" / "00_config" / "run_config.yaml"), base_dir=root))
    assert (root / "LEIA-ME.txt").read_text(encoding="utf-8") == "minhas notas"
print("PASS: create_project herda escolhas + descritor + LEIA-MEs")

# --- open_project: nunca criar projeto em pasta alheia ---
with tempfile.TemporaryDirectory() as tmp:
    alheia = Path(tmp) / "Fotos de Familia"
    alheia.mkdir()
    (alheia / "foto.jpg").write_bytes(b"x")
    try:
        app_service.open_project(alheia)
        raise AssertionError("deveria ter recusado a pasta alheia")
    except FileNotFoundError as exc:
        assert "não é um projeto" in str(exc)
    assert not (alheia / "Transcricoes").exists(), "criou projeto sem pedir!"

    # projeto legitimo abre pelo descritor E pela pasta
    root = Path(tmp) / "Legitimo.transcricao"
    app_service.create_project(root, project_name="Legitimo")
    # resolve() dos DOIS lados: no macOS o tempdir e /var -> /private/var
    # e no Windows do CI aparecem nomes 8.3 — igualdade textual flakeia.
    reaberto = app_service.open_project(root / "Legitimo.transcritorio")
    assert reaberto.paths.project_root.resolve() == root.resolve()
    reaberto2 = app_service.open_project(root)
    assert reaberto2.paths.project_root.resolve() == root.resolve()
print("PASS: open_project sem auto-criacao")

# --- run_config.yaml sumido: recriar com os defaults da MAQUINA (F8) ---
# Os defaults de fabrica ressetavam um projeto essencial para turbo +
# diarizacao, e o usuario so descobria no gate de download.
import tempfile as _tf2

from unittest.mock import patch as _patch

with _tf2.TemporaryDirectory() as tmp:
    root = Path(tmp) / "Sumiu.transcricao"
    app_service.create_project(root, project_name="Sumiu")
    config_path = root / "Transcricoes" / "00_config" / "run_config.yaml"
    assert config_path.exists()
    config_path.unlink()  # sync/limpeza levou o arquivo
    with _patch("transcribe_pipeline.app_settings.asr_model_default", lambda: "tiny"), \
         _patch("transcribe_pipeline.app_settings.diarize_default", lambda: False):
        ctx = app_service.load_project(config_path=config_path)
    assert ctx.config.get("asr_model") == "tiny", ctx.config.get("asr_model")
    assert ctx.config.get("diarize") is False
    assert config_path.exists(), "config nao foi regravada"
print("PASS: config sumida herda os defaults da maquina")

print("PASS: toy_project_lifecycle")
