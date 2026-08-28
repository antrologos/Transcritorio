"""Sumario com indice tematico (fase 2.1 do plano-programa v0.2.0).

Orquestra o worker de analise local (llm_worker.py) DENTRO do ambiente
dedicado (llm_env): o app dispara o subprocesso, acompanha o progresso
(@PROGRESS) e recebe o markdown final em
05_transcripts_review/final/md/{id}.resumo.md — pasta que ja espelha para
Resultados/ no export. Prefere a transcricao REVISADA (review) e cai para
a canonica quando nao ha revisao.

Requisitos de execucao: GPU NVIDIA (LLM local 4-bit; em CPU seria de
horas) e o modelo Qwen3.5-4B no cache do app (registro _OPTIONAL_MODELS).
Falhas viram mensagens instrutivas, nunca crash.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import llm_env, model_manager, runtime
from .config import Paths
from .manifest import selected_rows
from .research_context import context_path
from .review_store import canonical_path, review_path
from .utils import parse_progress_json_line, run_command_stream

ProgressCallback = Callable[[dict[str, Any]], None]

LLM_ASSET_KEY = "llm_qwen"


def resumo_path(paths: Paths, interview_id: str) -> Path:
    return paths.review_dir / "final" / "md" / f"{interview_id}.resumo.md"


def _optional_asset(key: str) -> model_manager.ModelAsset:
    for asset in model_manager._OPTIONAL_MODELS:
        if asset.key == key:
            return asset
    raise KeyError(f"Modelo opcional desconhecido: {key}")


def llm_model_cached(key: str = LLM_ASSET_KEY) -> bool:
    asset = _optional_asset(key)
    try:
        snapshot = model_manager.cached_snapshot_path(
            asset.repo_id, runtime.model_cache_dir(), revision=asset.revision)
    except Exception:  # noqa: BLE001 - cache ilegivel = tratar como ausente
        return False
    return snapshot is not None


def summarize_ready() -> tuple[bool, str]:
    """(pronto, motivo-se-nao). Nao cria nada; so diagnostica."""
    if not runtime.has_nvidia_gpu():
        return False, ("A analise local precisa de uma placa NVIDIA (o modelo roda na GPU). "
                       "Este computador nao tem uma disponivel.")
    if not llm_model_cached():
        asset = _optional_asset(LLM_ASSET_KEY)
        return False, (f"O modelo de analise ({asset.label}, ~{asset.estimated_gb:.1f} GB) "
                       "ainda nao foi baixado neste computador.")
    return True, ""


def run_summarize(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    rows_to_run = selected_rows(rows, ids)

    if dry_run:
        for row in rows_to_run:
            print(f"summarize {row['interview_id']}")
        return 0

    def emit(event: str, progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "progress": progress, "message": message})

    ready, reason = summarize_ready()
    if not ready:
        print(reason)
        return len(rows_to_run) or 1

    if not llm_env.llm_env_ready():
        emit("summarize_progress", 2, "Preparando o ambiente de analise (primeira vez)...")
        if llm_env.create_llm_env(use_cuda=True, progress_callback=progress_callback) != 0:
            return len(rows_to_run) or 1

    asset = _optional_asset(LLM_ASSET_KEY)
    worker = Path(__file__).resolve().parent / "llm_worker.py"
    # Glossario de nomes (lote 6a): faz a AI tratar "BGA" e "IBGE" como a
    # mesma entidade. Opcional — sem glossario, nada muda.
    from .glossario import glossary_prompt_file
    glossario_file = glossary_prompt_file(paths)

    failures = 0
    total = len(rows_to_run)
    for index, row in enumerate(rows_to_run):
        if should_cancel is not None and should_cancel():
            print("Cancelado.")
            break
        interview_id = row["interview_id"]
        source = review_path(paths, interview_id)
        if not source.exists():
            source = canonical_path(paths, interview_id)
        if not source.exists():
            print(f"{interview_id}: ainda sem transcricao; transcreva antes de resumir.")
            continue
        base = int(100 * index / max(1, total))
        span = max(1, 100 // max(1, total))

        def on_output(line: str, _base: int = base, _span: int = span) -> None:
            detail = parse_progress_json_line(line)
            if detail is not None and progress_callback is not None:
                inner = int(detail.get("progress") or 0)
                progress_callback({
                    "event": "summarize_progress",
                    "progress": _base + (inner * _span) // 100,
                    "message": str(detail.get("message") or ""),
                })

        command = [
            str(llm_env.llm_python()), "-B", str(worker),
            "--task", "sumario",
            "--review", str(source),
            "--out", str(resumo_path(paths, interview_id)),
            "--model-repo", asset.repo_id,
            "--hf-cache", str(runtime.model_cache_dir()),
        ]
        ctx = context_path(paths)
        if ctx.exists():
            command += ["--context-file", str(ctx)]
        if glossario_file is not None:
            command += ["--glossario-file", str(glossario_file)]
        completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
        if completed.returncode != 0:
            failures += 1
            print(f"{interview_id}: falha ao gerar o resumo (codigo {completed.returncode}).")
        else:
            print(f"{interview_id}: resumo em {resumo_path(paths, interview_id)}")
    if glossario_file is not None:
        try:
            glossario_file.unlink(missing_ok=True)
        except OSError:
            pass
    emit("summarize_progress", 100, "Resumos concluidos.")
    return failures
