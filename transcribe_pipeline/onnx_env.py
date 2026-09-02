"""Pacote de aceleracao GPU do motor Parakeet (onnxruntime-gpu isolado).

Por que um diretorio separado: o app depende do onnxruntime (CPU) nas
dependencias base, e instalar onnxruntime-gpu NO MESMO ambiente quebra o
CUDA em silencio — medido em 2026-08-30: com os dois pacotes instalados,
get_available_providers() lista so CPU/Azure mesmo com a DLL CUDA no
disco. Por isso o onnxruntime-gpu vive num diretorio proprio (pip
--target), que o worker de transcricao GPU (parakeet_worker.py) enxerga
PRIMEIRO via PYTHONPATH — sombreando apenas o pacote onnxruntime.

`--no-deps` e essencial: sem ele o target receberia numpy/protobuf/etc.,
que tambem sombreariam os do app dentro do worker. As dependencias do
onnxruntime-gpu==1.22.0 sao as mesmas do onnxruntime CPU ja instalado.

Pino 1.22.0: e a serie do CUDA 12.x, compativel com as DLLs
(cublas/cudnn9) que o torch cu128 do extra [cuda] ja traz — o worker as
carrega via add_dll_directory(torch/lib). A serie 1.29+ exige CUDA 13.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import runtime
from .llm_env import find_uv

ONNX_ENV_SPEC_VERSION = 1
PACKAGE = "onnxruntime-gpu==1.22.0"
MARKER_FILENAME = "transcritorio-onnx-env.json"
ESTIMATED_GB = 0.3

# Canario fisico: a DLL do CUDA EP e o motivo de existir deste diretorio.
_CANARY = Path("onnxruntime") / "capi" / "onnxruntime_providers_cuda.dll"


def onnx_env_dir() -> Path:
    return runtime.app_data_dir() / "onnx-gpu"


def marker_path(env_dir: Path | None = None) -> Path:
    return (env_dir or onnx_env_dir()) / MARKER_FILENAME


def python_tag(version_info: tuple[int, int] | None = None) -> str:
    """Tag ABI do interpretador ("cp312"): a wheel do onnxruntime-gpu e por
    versao de Python e o .pyd nao carrega em outra (pura)."""
    major, minor = version_info or (sys.version_info.major, sys.version_info.minor)
    return f"cp{major}{minor}"


def env_spec() -> dict[str, Any]:
    """Especificacao declarativa (pura, testavel)."""
    return {"version": ONNX_ENV_SPEC_VERSION, "package": PACKAGE, "python": python_tag()}


def installed_python_tag(env_dir: Path) -> str:
    """Tag de Python da wheel instalada no diretorio, pelo WHEEL do
    dist-info ("cp313" de "Tag: cp313-cp313-win_amd64"); "" se ilegivel."""
    for wheel in env_dir.glob("onnxruntime_gpu-*.dist-info/WHEEL"):
        try:
            for line in wheel.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("tag:"):
                    return line.split(":", 1)[1].strip().split("-", 1)[0]
        except OSError:
            return ""
    return ""


def onnx_env_stale_reason(env_dir: Path | None = None) -> str:
    """Por que o diretorio existente NAO serve para este interpretador; ""
    quando serve (ou quando nao ha como saber). Caso real 2026-09-02: o
    pacote instalado no Python 3.13 (v0.2.2) ficou mudo quando o app passou
    ao 3.12 (v0.2.3) — o worker falhava com "DLL load failed" e o TAGARELA
    caia para a CPU em silencio numa maquina com GPU."""
    base = env_dir or onnx_env_dir()
    atual = python_tag()
    try:
        marker = json.loads(marker_path(base).read_text(encoding="utf-8"))
        gravado = str(marker.get("python") or "")
    except Exception:  # noqa: BLE001 - marcador ausente: usa a wheel
        gravado = ""
    instalado = gravado or installed_python_tag(base)
    if instalado and instalado != atual:
        return (f"a aceleração foi instalada para o Python {instalado[2]}.{instalado[3:]} e o "
                f"Transcritório agora usa o {atual[2]}.{atual[3:]}")
    return ""


def install_command(uv: str, env_dir: Path, spec: dict[str, Any], python: str | None = None) -> list[str]:
    """Comando uv para popular o diretorio (puro, testavel).

    `--python` = o interpretador DO APP: sem ele o uv escolhe qualquer Python
    da maquina (ou o cache) e instala a wheel de outra versao — que nao
    carrega (2026-09-02: wheel cp313 num app em 3.12, "pronta em 1 s")."""
    cmd = [uv, "pip", "install", "--target", str(env_dir), "--no-deps"]
    if python:
        cmd += ["--python", str(python)]
    return cmd + [str(spec["package"])]


def onnx_env_ready(env_dir: Path | None = None) -> bool:
    """Pronto = canario fisico (DLL do CUDA EP) + marcador da versao atual."""
    base = env_dir or onnx_env_dir()
    if not (base / _CANARY).is_file():
        return False
    try:
        marker = json.loads(marker_path(base).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - marcador ausente/corrompido = recriar
        return False
    if int(marker.get("version", -1)) != ONNX_ENV_SPEC_VERSION:
        return False
    # Wheel de outro Python nao carrega (2026-09-02): tratar como nao pronto
    # para o app oferecer a reinstalacao em vez de cair para a CPU calado.
    return not onnx_env_stale_reason(base)


def create_onnx_env(
    env_dir: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Cria/recria o diretorio; retorna 0 em sucesso, 1 em falha.

    rmtree ANTES de instalar: diferente do `uv venv` (que recria), o
    `--target` acumula — um download interrompido ou um bump de
    ONNX_ENV_SPEC_VERSION deixariam residuos misturados. O download do
    uv nao e cancelavel no meio (mesma limitacao aceita no llm_env).
    """
    base = env_dir or onnx_env_dir()
    uv = find_uv()
    if uv is None:
        print("uv nao encontrado — instale o uv (canal oficial do app) e tente de novo.")
        return 1
    spec = env_spec()
    started = time.time()
    if progress_callback is not None:
        progress_callback({
            "event": "onnx_env_progress", "progress": 10,
            "message": "Baixando a aceleração GPU do Parakeet (~300 MB)...",
        })
    if base.exists():
        try:
            shutil.rmtree(base)
        except OSError as exc:
            print(f"Falha ao limpar o diretorio da aceleracao GPU: {exc}")
            return 1
    completed = subprocess.run(install_command(uv, base, spec, python=sys.executable),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-2000:]
        print(f"Falha ao instalar a aceleracao GPU do Parakeet: {tail}")
        return 1
    if not (base / _CANARY).is_file():
        print("Instalacao terminou sem a DLL do CUDA — pacote inesperado.")
        return 1
    instalado = installed_python_tag(base)
    if instalado and instalado != python_tag():
        print(f"Instalacao veio para outro Python ({instalado} != {python_tag()}) — pacote inesperado.")
        return 1
    marker = dict(spec)
    marker["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    marker_path(base).write_text(
        json.dumps(marker, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress_callback is not None:
        progress_callback({
            "event": "onnx_env_progress", "progress": 100,
            "message": f"Aceleração GPU pronta ({time.time()-started:.0f}s).",
        })
    return 0


def remove_onnx_env(env_dir: Path | None = None) -> bool:
    """Remove o diretorio inteiro. True se nao existe mais ao final."""
    base = env_dir or onnx_env_dir()
    if not base.exists():
        return True
    try:
        shutil.rmtree(base)
    except OSError as exc:
        print(f"Falha ao remover a aceleracao GPU: {exc}")
    return not base.exists()


def torch_lib_dir() -> Path | None:
    """Diretorio torch/lib do ambiente do app (DLLs CUDA), sem importar torch."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("torch")
    except Exception:  # noqa: BLE001 - ambiente sem torch
        return None
    if spec is None or not spec.origin:
        return None
    lib = Path(spec.origin).parent / "lib"
    return lib if lib.is_dir() else None