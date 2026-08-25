"""Toy test para llm_env + registro _OPTIONAL_MODELS (fase 2.0.c)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import llm_env, model_manager

# --- env_spec: cuda vs cpu ---
cuda_spec = llm_env.env_spec(True)
cpu_spec = llm_env.env_spec(False)
assert cuda_spec["version"] == llm_env.LLM_ENV_SPEC_VERSION
assert any(p.startswith("torch==") and "cu128" in p for p in cuda_spec["packages"])
assert any(p.startswith("bitsandbytes") for p in cuda_spec["packages"])
assert cuda_spec["index"] == llm_env.CU128_INDEX
assert cpu_spec["index"] is None
assert not any("bitsandbytes" in p for p in cpu_spec["packages"])
assert any(p.startswith("transformers==5.13.1") for p in cuda_spec["packages"])
assert any(p.startswith("gliner==") for p in cuda_spec["packages"])
print("PASS: env_spec")

# --- install_commands: venv + pip, indice so no cuda ---
commands = llm_env.install_commands("UV", Path("X:/env"), cuda_spec)
assert commands[0][:2] == ["UV", "venv"]
assert "--index" in commands[1] and "--index-strategy" in commands[1]
commands_cpu = llm_env.install_commands("UV", Path("X:/env"), cpu_spec)
assert "--index" not in commands_cpu[1]
print("PASS: install_commands")

# --- llm_env_ready: python + marcador com versao atual ---
with tempfile.TemporaryDirectory() as tmp:
    env_dir = Path(tmp) / "llm-venv"
    assert llm_env.llm_env_ready(env_dir) is False  # nada existe
    python = llm_env.llm_python(env_dir)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert llm_env.llm_env_ready(env_dir) is False  # sem marcador
    llm_env.marker_path(env_dir).write_text(
        json.dumps({"version": llm_env.LLM_ENV_SPEC_VERSION}), encoding="utf-8")
    assert llm_env.llm_env_ready(env_dir) is True
    llm_env.marker_path(env_dir).write_text(
        json.dumps({"version": llm_env.LLM_ENV_SPEC_VERSION - 1}), encoding="utf-8")
    assert llm_env.llm_env_ready(env_dir) is False  # spec antigo = recriar
    llm_env.marker_path(env_dir).write_text("{corrompido", encoding="utf-8")
    assert llm_env.llm_env_ready(env_dir) is False
print("PASS: llm_env_ready")

# --- find_uv nao explode (retorna str ou None) ---
assert llm_env.find_uv() is None or isinstance(llm_env.find_uv(), str)
print("PASS: find_uv")

# --- registro: opcionais conhecidos, nunca obrigatorios, nunca orfaos ---
known = model_manager._known_repos()
assert "Qwen/Qwen3.5-4B" in known and "urchade/gliner_multi_pii-v1" in known
required = {a.repo_id for a in model_manager.get_required_models()}
assert "Qwen/Qwen3.5-4B" not in required and "urchade/gliner_multi_pii-v1" not in required
for asset in model_manager._OPTIONAL_MODELS:
    assert asset.revision, f"SHA obrigatoria ausente em {asset.key}"

with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    (cache / "models--Qwen--Qwen3.5-4B").mkdir()
    (cache / "models--alguem--desconhecido").mkdir()
    orphans = model_manager.orphan_repos(cache)
    assert "Qwen/Qwen3.5-4B" not in orphans
    assert "alguem/desconhecido" in orphans
print("PASS: registro de modelos opcionais")

print("PASS: toy_llm_env")
