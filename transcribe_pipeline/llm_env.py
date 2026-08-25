"""Ambiente dedicado da analise local (LLM/GLiNER) — fase 2.0.c.

Por que um ambiente separado: transformers>=5.13 (necessario para o
Qwen3.5) exige huggingface-hub>=1.5, e o whisperx 3.8.5 do app trava
hub<1.0 — irreconciliavel no mesmo ambiente (uv lock, 2026-08-25). Como a
analise ja roda em SUBPROCESSO por desenho (um OOM nunca derruba o app),
o subprocesso ganha o proprio venv, criado sob demanda com o uv — o mesmo
uv pelo qual o app foi instalado (canal oficial), no padrao do cuda_pack
baixado sob demanda.

Pinos validados no PoC 2026-08-25: transformers 5.13.1 reconhece a
arquitetura qwen3_5 E respeita o teto imposto pelo gliner; torch cu128
2.8.0 e o mesmo do app. Os modelos ficam no cache HF compartilhado do app
(runtime.model_cache_dir) — o ambiente carrega, nao baixa.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from . import runtime

LLM_ENV_SPEC_VERSION = 1
CU128_INDEX = "https://download.pytorch.org/whl/cu128"

_COMMON_PACKAGES = [
    "transformers==5.13.1",
    "accelerate>=1.0",
    "gliner==0.2.28",
]
_CUDA_PACKAGES = ["torch==2.8.0+cu128", "bitsandbytes>=0.48"]
_CPU_PACKAGES = ["torch>=2.8"]

MARKER_FILENAME = "transcritorio-llm-env.json"


def llm_env_dir() -> Path:
    return runtime.app_data_dir() / "llm-venv"


def llm_python(env_dir: Path | None = None) -> Path:
    base = env_dir or llm_env_dir()
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def marker_path(env_dir: Path | None = None) -> Path:
    return (env_dir or llm_env_dir()) / MARKER_FILENAME


def env_spec(use_cuda: bool) -> dict[str, Any]:
    """Especificacao declarativa do ambiente (pura, testavel)."""
    packages = list(_COMMON_PACKAGES)
    packages += _CUDA_PACKAGES if use_cuda else _CPU_PACKAGES
    return {
        "version": LLM_ENV_SPEC_VERSION,
        "use_cuda": bool(use_cuda),
        "packages": packages,
        "index": CU128_INDEX if use_cuda else None,
    }


def install_commands(uv: str, env_dir: Path, spec: dict[str, Any]) -> list[list[str]]:
    """Comandos uv para criar e popular o ambiente (puros, testaveis)."""
    python = llm_python(env_dir)
    pip_cmd = [uv, "pip", "install", "--python", str(python), *spec["packages"]]
    if spec.get("index"):
        pip_cmd += ["--index", str(spec["index"]), "--index-strategy", "unsafe-best-match"]
    return [[uv, "venv", str(env_dir)], pip_cmd]


def find_uv() -> str | None:
    """uv no PATH ou nos locais conhecidos de instalacao no Windows."""
    found = shutil.which("uv")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".local" / "bin" / "uv",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def llm_env_ready(env_dir: Path | None = None) -> bool:
    """Pronto = python do venv existe + marcador da versao atual do spec."""
    base = env_dir or llm_env_dir()
    if not llm_python(base).exists():
        return False
    try:
        marker = json.loads(marker_path(base).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - marcador ausente/corrompido = recriar
        return False
    return int(marker.get("version", -1)) == LLM_ENV_SPEC_VERSION


def create_llm_env(
    use_cuda: bool,
    env_dir: Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Cria/recria o ambiente; retorna 0 em sucesso, 1 em falha."""
    base = env_dir or llm_env_dir()
    uv = find_uv()
    if uv is None:
        print("uv nao encontrado — instale o uv (canal oficial do app) e tente de novo.")
        return 1
    spec = env_spec(use_cuda)
    started = time.time()
    for index, command in enumerate(install_commands(uv, base, spec)):
        if progress_callback is not None:
            progress_callback({
                "event": "llm_env_progress",
                "progress": 10 + 80 * index // 2,
                "message": "Preparando ambiente de analise..." if index == 0
                else "Instalando componentes de analise (pode levar minutos)...",
            })
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "")[-2000:]
            print(f"Falha ao preparar o ambiente de analise (passo {index + 1}): {tail}")
            return 1
    marker = dict(spec)
    marker["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    marker_path(base).write_text(
        json.dumps(marker, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress_callback is not None:
        progress_callback({
            "event": "llm_env_progress", "progress": 100,
            "message": f"Ambiente de analise pronto ({time.time()-started:.0f}s).",
        })
    return 0
