"""Perguntar as entrevistas com AI (fase 2.7): RAG 100% local.

pergunta -> recuperacao pelo indice da busca por sentido (search.py) ->
Qwen (llm-venv, subprocesso) responde APENAS com base nos trechos, com
citacoes [n] obrigatorias; resposta sem citacao vira a recusa honesta
(validate_answer no worker). Nada sai do computador.

Arquivos temporarios (trechos e resposta) ficam em
%LOCALAPPDATA%/Transcritorio/tmp — fora do Dropbox de proposito (contem
texto de entrevista) — e sao removidos ao final.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import llm_env, runtime, search
from .config import Paths
from .llm_worker import SEM_RESPOSTA, fmt_time
from .research_context import context_path
from .summarize import LLM_ASSET_KEY, _optional_asset, summarize_ready
from .utils import parse_progress_json_line, run_command_stream

ProgressCallback = Callable[[dict[str, Any]], None]

RETRIEVAL_TOP_N = 8
RETRIEVAL_MIN_SIMILARITY = 0.30

ask_ready = summarize_ready  # mesmos requisitos: GPU NVIDIA + Qwen no cache


def build_trechos(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trechos numerados para o worker e para a UI (puro, testavel)."""
    return [
        {
            "n": index + 1,
            "interview_id": str(hit.get("interview_id")),
            "start": float(hit.get("start", 0) or 0),
            "inicio": fmt_time(float(hit.get("start", 0) or 0)),
            "label": str(hit.get("label") or ""),
            "text": str(hit.get("text") or ""),
            "similarity": float(hit.get("similarity", 0) or 0),
        }
        for index, hit in enumerate(hits)
    ]


def run_ask(
    paths: Paths,
    interview_ids: list[str],
    question: str,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """{resposta, trechos} em sucesso; {erro} em indisponibilidade/falha."""

    def emit(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": "ask_progress", "progress": progress, "message": message})

    ready, reason = ask_ready()
    if not ready:
        return {"erro": reason}
    if not llm_env.llm_env_ready():
        emit(2, "Preparando o ambiente de AI (primeira vez)...")
        if llm_env.create_llm_env(use_cuda=True, progress_callback=progress_callback) != 0:
            return {"erro": "Nao foi possivel preparar o ambiente de AI local."}

    emit(10, "Procurando trechos relevantes nas transcricoes (busca semantica)...")
    hits = search.project_semantic_search(
        paths, interview_ids, question,
        min_similarity=RETRIEVAL_MIN_SIMILARITY, top_n=RETRIEVAL_TOP_N)
    trechos = build_trechos(hits)
    if not trechos:
        return {"resposta": SEM_RESPOSTA, "trechos": []}

    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    trechos_path = tmp_dir / f"ask_{token}_trechos.json"
    out_path = tmp_dir / f"ask_{token}_resposta.json"
    trechos_path.write_text(json.dumps(trechos, ensure_ascii=False), encoding="utf-8")

    asset = _optional_asset(LLM_ASSET_KEY)
    worker = Path(__file__).resolve().parent / "llm_worker.py"
    command = [
        str(llm_env.llm_python()), "-B", str(worker),
        "--task", "perguntar",
        "--question", question,
        "--trechos-file", str(trechos_path),
        "--out", str(out_path),
        "--model-repo", asset.repo_id,
        "--hf-cache", str(runtime.model_cache_dir()),
    ]
    ctx = context_path(paths)
    if ctx.exists():
        command += ["--context-file", str(ctx)]
    # Glossario de nomes (lote 6a): a AI passa a saber que "BGA" e "IBGE"
    # sao a mesma coisa. Opcional — sem glossario, nada muda.
    from .glossario import glossary_prompt_file
    glossario_file = glossary_prompt_file(paths)
    if glossario_file is not None:
        command += ["--glossario-file", str(glossario_file)]

    def on_output(line: str) -> None:
        detail = parse_progress_json_line(line)
        if detail is not None and progress_callback is not None:
            inner = int(detail.get("progress") or 0)
            progress_callback({
                "event": "ask_progress",
                "progress": 10 + (inner * 85) // 100,
                "message": str(detail.get("message") or ""),
            })

    started = time.time()
    try:
        completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
        if completed.returncode != 0 or not out_path.exists():
            return {"erro": f"A AI local falhou ao responder (codigo {completed.returncode})."}
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        for path in (trechos_path, out_path, glossario_file):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    emit(100, f"Resposta pronta ({time.time() - started:.0f}s).")
    return {"resposta": str(payload.get("resposta") or SEM_RESPOSTA), "trechos": trechos}
