"""Perguntar as entrevistas com AI (fase 2.7; v3 em 2026-09-03): RAG 100% local.

Dois estagios, para a janela mostrar os trechos antes da resposta:

1. `retrieve`  — busca por sentido v3 (search.search_passages: passagens,
   encoder de recuperacao, hibrido com o literal, reordenador quando
   instalado, corte de relevancia). Roda em qualquer maquina, sem LLM.
2. `answer_from_trechos` — o Qwen (llm-venv, subprocesso, GPU) responde
   APENAS com base nos trechos que sobreviveram ao corte, com citacoes [n]
   obrigatorias; resposta sem citacao vira a recusa honesta
   (validate_answer no worker). Nada sai do computador.

Perguntas sobre o CONJUNTO ("do que falam as entrevistas?") nao sao busca:
`question_kind` as reconhece e `run_visao_geral` responde pelos resumos por
entrevista (map-reduce no worker, citando [ID]). Sem resumos, quem chama
oferece "Resumir".

Arquivos temporarios (trechos, resumos e resposta) ficam em
%LOCALAPPDATA%/Transcritorio/tmp — fora do Dropbox de proposito (contem
texto de entrevista) — e sao removidos ao final.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import app_settings, llm_env, runtime, search
from .config import Paths
from .llm_worker import SEM_RESPOSTA, fmt_time, split_resumo_sections
from .research_context import context_path, is_filled, load_research_context
from .summarize import LLM_ASSET_KEY, _optional_asset, resumo_path, summarize_ready
from .utils import RESULT_JSON_PREFIX, parse_prefixed_json_line, parse_progress_json_line, run_command_stream

ProgressCallback = Callable[[dict[str, Any]], None]

ask_ready = summarize_ready  # mesmos requisitos: GPU NVIDIA + Qwen no cache

SEM_TRECHOS = ("Nenhum trecho das entrevistas trata disso de perto — a AI não escreveu "
               "resposta para não inventar. Tente uma situação concreta (\"o recenseador "
               "foi barrado na portaria\") em vez de uma pergunta geral.")

_GLOBAL_PATTERNS = (
    r"\b(do que|sobre o que|de que)\s+(falam|tratam|se trata|trata|se fala)\b",
    r"\bquais\s+(s[aã]o\s+)?(os\s+)?(principais\s+|maiores\s+)?(temas|assuntos|t[oó]picos|pontos)\b",
    r"\bvis[aã]o geral\b",
    r"\bem geral\b",
    r"\bno geral\b",
    r"\bno conjunto\b",
    r"\bde modo geral\b",
    r"\btodas as entrevistas\b",
    r"\bnas entrevistas em geral\b",
    r"\bresum[aoe]\b",
    r"\bpanorama\b",
    r"\bo que (mais|menos) (aparece|se repete)\b",
)
_GLOBAL_RE = re.compile("|".join(_GLOBAL_PATTERNS), re.IGNORECASE)


def question_kind(question: str) -> str:
    """"global" (pergunta sobre o conjunto: resumos) ou "trechos" (puro).

    Padroes de texto baratos; o reordenador cobre o resto ("nada
    responde" -> a janela sugere a visao geral)."""
    text = " ".join(str(question or "").split())
    return "global" if _GLOBAL_RE.search(text) else "trechos"


def build_trechos(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trechos numerados para o worker e para a UI (puro, testavel)."""
    return [
        {
            "n": index + 1,
            "interview_id": str(hit.get("interview_id")),
            "start": float(hit.get("start", 0) or 0),
            "end": float(hit.get("end", 0) or 0),
            "t_from": int(hit.get("t_from", hit.get("turn_index", -1))),
            "t_to": int(hit.get("t_to", hit.get("turn_index", -1))),
            "inicio": fmt_time(float(hit.get("start", 0) or 0)),
            "label": str(hit.get("label") or ""),
            "text": str(hit.get("text") or ""),
            "similarity": float(hit.get("similarity", 0) or 0),
            "score": hit.get("score"),
            "z": hit.get("z"),
        }
        for index, hit in enumerate(hits)
    ]


def answer_worth_trying(result: dict[str, Any]) -> bool:
    """Vale gastar ~1 min de LLM? So quando algum trecho realmente trata do
    tema: com reordenador, existe secao "Respondem"; sem ele, a primeira
    secao nao e fraca (puro)."""
    sections = result.get("sections") or []
    if not sections:
        return False
    if result.get("reranked"):
        return any(s.get("key") == "responde" for s in sections)
    return not bool(sections[0].get("weak"))


def retrieve(
    paths: Paths,
    interview_ids: list[str],
    question: str,
    max_results: int | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Estagio 1: trechos que tratam do tema (ate max_results), com secoes."""
    n = max_results or app_settings.search_max_results()
    result = search.search_passages(
        paths, interview_ids, question, max_results=n,
        progress_callback=progress_callback, should_cancel=should_cancel)
    result["trechos"] = build_trechos(result.get("hits") or [])
    result["kind"] = question_kind(question)
    result["worth_answer"] = answer_worth_trying(result)
    return result


def context_worth_sending(paths: Paths) -> bool:
    """O contexto da pesquisa vale ser mandado a AI?

    Todo projeto nasce com o template de `contexto_pesquisa.md`; mandar o
    template intocado faria a AI ler as INSTRUCOES ao usuario ("Preencha o
    que fizer sentido…") como se fossem o contexto do estudo, em todos os
    prompts. So vai quando ha conteudo escrito por alguem."""
    return context_path(paths).exists() and is_filled(load_research_context(paths))


def _worker_command(task: str, paths: Paths, out_path: Path, question: str) -> list[str]:
    asset = _optional_asset(LLM_ASSET_KEY)
    worker = Path(__file__).resolve().parent / "llm_worker.py"
    command = [
        str(llm_env.llm_python()), "-B", str(worker),
        "--task", task,
        "--question", question,
        "--out", str(out_path),
        "--model-repo", asset.repo_id,
        "--hf-cache", str(runtime.model_cache_dir()),
    ]
    if context_worth_sending(paths):
        command += ["--context-file", str(context_path(paths))]
    return command


def _run_worker(
    command: list[str], out_path: Path, tmp_files: list[Path | None],
    progress_callback: ProgressCallback | None, should_cancel: Callable[[], bool] | None,
    base: int = 10, span: int = 85,
) -> dict[str, Any] | None:
    """Roda o worker e devolve o payload: PRIMEIRO a linha @RESULT do stdout,
    depois o arquivo (reserva). O arquivo sozinho falhava quando o llm-venv
    vinha do Python da Microsoft Store (virtualizacao de %LOCALAPPDATA%):
    o worker gravava numa pasta que o app nao ve."""
    captured: dict[str, Any] = {}

    def on_output(line: str) -> None:
        result = parse_prefixed_json_line(line, RESULT_JSON_PREFIX)
        if result is not None:
            captured.clear()
            captured.update(result)
            return
        detail = parse_progress_json_line(line)
        if detail is not None and progress_callback is not None:
            inner = int(detail.get("progress") or 0)
            progress_callback({
                "event": "ask_progress",
                "progress": base + (inner * span) // 100,
                "message": str(detail.get("message") or ""),
            })

    try:
        completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
        if completed.returncode != 0:
            return None
        if captured:
            return dict(captured)
        if not out_path.exists():
            return None
        return json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        for path in tmp_files + [out_path]:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _llm_prereqs(progress_callback: ProgressCallback | None) -> str:
    """"" quando a AI pode responder; senao o motivo."""
    ready, reason = ask_ready()
    if not ready:
        return reason
    if not llm_env.llm_env_ready():
        if progress_callback is not None:
            progress_callback({"event": "ask_progress", "progress": 2,
                               "message": "Preparando o ambiente de AI (primeira vez)..."})
        if llm_env.create_llm_env(use_cuda=True, progress_callback=progress_callback) != 0:
            return "Nao foi possivel preparar o ambiente de AI local."
    return ""


def answer_from_trechos(
    paths: Paths,
    question: str,
    trechos: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Estagio 2: {resposta} ancorada nos trechos, ou {erro}."""
    motivo = _llm_prereqs(progress_callback)
    if motivo:
        return {"erro": motivo}
    if not trechos:
        return {"resposta": SEM_RESPOSTA}
    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    trechos_path = tmp_dir / f"ask_{token}_trechos.json"
    out_path = tmp_dir / f"ask_{token}_resposta.json"
    trechos_path.write_text(json.dumps(trechos, ensure_ascii=False), encoding="utf-8")
    command = _worker_command("perguntar", paths, out_path, question)
    command += ["--trechos-file", str(trechos_path)]
    # Glossario de nomes (lote 6a): a AI passa a saber que "BGA" e "IBGE"
    # sao a mesma coisa. Opcional — sem glossario, nada muda.
    from .glossario import glossary_prompt_file
    glossario_file = glossary_prompt_file(paths)
    if glossario_file is not None:
        command += ["--glossario-file", str(glossario_file)]
    started = time.time()
    payload = _run_worker(command, out_path, [trechos_path, glossario_file], progress_callback, should_cancel)
    if payload is None:
        return {"erro": "A AI local falhou ao responder."}
    if progress_callback is not None:
        progress_callback({"event": "ask_progress", "progress": 100,
                           "message": f"Resposta pronta ({time.time() - started:.0f}s)."})
    return {"resposta": str(payload.get("resposta") or SEM_RESPOSTA)}


def resumos_for_scope(paths: Paths, interview_ids: list[str], titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Resumos existentes do escopo: [{interview_id, titulo, resumo, indice}]."""
    resumos: list[dict[str, Any]] = []
    for interview_id in interview_ids:
        path = resumo_path(paths, interview_id)
        if not path.exists():
            continue
        try:
            sections = split_resumo_sections(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - resumo ilegivel fica de fora
            continue
        if not (sections.get("resumo") or sections.get("indice")):
            continue
        resumos.append({
            "interview_id": interview_id,
            "titulo": str((titles or {}).get(interview_id) or interview_id),
            "resumo": sections.get("resumo", ""),
            "indice": sections.get("indice", ""),
        })
    return resumos


def run_visao_geral(
    paths: Paths,
    interview_ids: list[str],
    question: str,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Pergunta sobre o conjunto: {resposta, citadas, com_resumo, sem_resumo}
    ou {erro}. Sem nenhum resumo: {sem_resumo: ids} e nada de LLM."""
    resumos = resumos_for_scope(paths, interview_ids, titles)
    com = [r["interview_id"] for r in resumos]
    sem = [iid for iid in interview_ids if iid not in set(com)]
    if not resumos:
        return {"resposta": "", "citadas": [], "com_resumo": [], "sem_resumo": sem}
    motivo = _llm_prereqs(progress_callback)
    if motivo:
        return {"erro": motivo, "com_resumo": com, "sem_resumo": sem}
    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    resumos_path = tmp_dir / f"ask_{token}_resumos.json"
    out_path = tmp_dir / f"ask_{token}_visao.json"
    resumos_path.write_text(json.dumps(resumos, ensure_ascii=False), encoding="utf-8")
    command = _worker_command("visao_geral", paths, out_path, question)
    command += ["--resumos-file", str(resumos_path)]
    payload = _run_worker(command, out_path, [resumos_path], progress_callback, should_cancel)
    if payload is None:
        return {"erro": "A AI local falhou ao responder.", "com_resumo": com, "sem_resumo": sem}
    return {
        "resposta": str(payload.get("resposta") or SEM_RESPOSTA),
        "citadas": [str(c) for c in (payload.get("citadas") or [])],
        "com_resumo": com,
        "sem_resumo": sem,
    }


def run_ask(
    paths: Paths,
    interview_ids: list[str],
    question: str,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Composicao dos dois estagios (CLI e compatibilidade): {resposta,
    trechos, sections, reranked} em sucesso; {erro} em indisponibilidade."""
    if progress_callback is not None:
        progress_callback({"event": "ask_progress", "progress": 5,
                           "message": "Procurando trechos que tratam disso..."})
    result = retrieve(paths, interview_ids, question,
                      progress_callback=progress_callback, should_cancel=should_cancel)
    trechos = result.get("trechos") or []
    out = {"trechos": trechos, "sections": result.get("sections") or [],
           "reranked": bool(result.get("reranked")), "kind": result.get("kind")}
    if not result.get("worth_answer"):
        out["resposta"] = SEM_TRECHOS if trechos else SEM_RESPOSTA
        out["sem_resposta"] = True
        return out
    answer = answer_from_trechos(paths, question, trechos, progress_callback, should_cancel)
    if answer.get("erro"):
        out["erro"] = answer["erro"]
        return out
    out["resposta"] = answer.get("resposta") or SEM_RESPOSTA
    return out
