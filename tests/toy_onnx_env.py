"""Toy test para onnx_env (aceleracao GPU do Parakeet — plano 2026-08-30)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import onnx_env

# --- env_spec e install_command (puros) ---
spec = onnx_env.env_spec()
assert spec["version"] == onnx_env.ONNX_ENV_SPEC_VERSION
assert spec["package"].startswith("onnxruntime-gpu==1.22.")
cmd = onnx_env.install_command("UV", Path("X:/onnx-gpu"), spec)
assert cmd[0] == "UV" and cmd[1:3] == ["pip", "install"]
assert "--target" in cmd, "sem --target o pacote iria para o site-packages do app"
assert "--no-deps" in cmd, ("sem --no-deps o numpy/protobuf do target "
                            "sombreariam os do app dentro do worker")
assert cmd[cmd.index("--target") + 1] == str(Path("X:/onnx-gpu"))
assert cmd[-1] == spec["package"]
print("PASS: env_spec + install_command")

# --- onnx_env_ready: canario fisico + marcador versionado ---
with tempfile.TemporaryDirectory() as tmp:
    env_dir = Path(tmp) / "onnx-gpu"
    assert onnx_env.onnx_env_ready(env_dir) is False  # nada existe
    canary = env_dir / "onnxruntime" / "capi" / "onnxruntime_providers_cuda.dll"
    canary.parent.mkdir(parents=True)
    canary.write_bytes(b"")
    assert onnx_env.onnx_env_ready(env_dir) is False  # DLL sem marcador
    onnx_env.marker_path(env_dir).write_text(
        json.dumps({"version": onnx_env.ONNX_ENV_SPEC_VERSION}), encoding="utf-8")
    assert onnx_env.onnx_env_ready(env_dir) is True
    onnx_env.marker_path(env_dir).write_text(
        json.dumps({"version": onnx_env.ONNX_ENV_SPEC_VERSION - 1}), encoding="utf-8")
    assert onnx_env.onnx_env_ready(env_dir) is False  # spec antigo = refazer
    onnx_env.marker_path(env_dir).write_text("{corrompido", encoding="utf-8")
    assert onnx_env.onnx_env_ready(env_dir) is False
    # marcador ok mas DLL sumiu (instalacao mutilada)
    onnx_env.marker_path(env_dir).write_text(
        json.dumps({"version": onnx_env.ONNX_ENV_SPEC_VERSION}), encoding="utf-8")
    canary.unlink()
    assert onnx_env.onnx_env_ready(env_dir) is False
print("PASS: onnx_env_ready")

# --- remove_onnx_env ---
with tempfile.TemporaryDirectory() as tmp:
    env_dir = Path(tmp) / "onnx-gpu"
    assert onnx_env.remove_onnx_env(env_dir) is True  # inexistente = ok
    (env_dir / "sub").mkdir(parents=True)
    (env_dir / "sub" / "x.txt").write_text("x", encoding="utf-8")
    assert onnx_env.remove_onnx_env(env_dir) is True
    assert not env_dir.exists()
print("PASS: remove_onnx_env")

# --- torch_lib_dir nao explode (Path existente ou None) ---
lib = onnx_env.torch_lib_dir()
assert lib is None or lib.is_dir()
print("PASS: torch_lib_dir")

print("PASS: toy_onnx_env")
