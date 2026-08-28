"""Glossario de nomes do projeto (fase 2.6 / lote 6a).

Quando a transcricao escreve "Celso" onde se falou "Censo", ou "BGA"
por IBGE, o sentido aparente muda: piora a busca por significado,
confunde a AI e polui a leitura. Este modulo varre as entrevistas com
o GLiNER (subprocesso no llm-venv), agrupa as variantes de grafia da
mesma entidade e monta um glossario do projeto.

O glossario tem dois destinos:
  1. injetado nos prompts do Qwen (resumo, perguntar), para a AI tratar
     as variantes como a mesma entidade — ganho imediato, sem tocar no
     texto das entrevistas;
  2. relatorio legivel com a secao "Grafias a conferir", onde o
     pesquisador julga caso a caso.

APLICAR a correcao no texto NAO acontece aqui: substituir "Celso" por
"Censo" altera o registro da entrevista e pode destruir um nome real
de pessoa. Isso e o lote 6b, com dialogo de julgamento caso a caso.

O nucleo (normalizacao, dobra fonetica, agrupamento, formatacao) e
puro e stdlib — roda no CI minimo, sem torch nem gliner.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .utils import now_utc, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

GLOSSARY_VERSION = 1
NER_ASSET_KEY = "ner_gliner"
# 0,75 calibrado contra o gabarito da PoC: pega Vistosa/Vicosa (0,769) e
# Cefete/CEFET, sem fundir nada indevido. "Maria"/"Mario" da 0,90 e so a
# trava de frequencia os separa — por isso ela e obrigatoria.
SIMILARITY_THRESHOLD = 0.75
MINORITY_RATIO = 0.25         # garble de ASR e minoria clara da forma correta
MIN_FORM_CHARS = 3
MAX_PROMPT_CHARS = 1200
MAX_EXAMPLES = 3


# ---------------------------------------------------------------------------
# Nucleo puro: normalizacao e semelhanca
# ---------------------------------------------------------------------------

def normalize_key(text: str) -> str:
    """Minusculo, sem acento e sem pontuacao (receita da PoC)."""
    decomposed = unicodedata.normalize("NFD", str(text))
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", "", stripped.lower()).strip()


_PHONETIC_RULES: tuple[tuple[str, str], ...] = (
    (r"ph", "f"),
    (r"ch", "x"),
    # lh/nh viram simbolos PROPRIOS: sao fonemas distintos em portugues.
    # Colapsar em l/n faria "Penha" == "Pena" e "Rocinha" == "Rocina".
    (r"lh", "L"),
    (r"nh", "N"),
    (r"qu", "k"),
    (r"gu([ei])", r"g\1"),
    (r"c([ei])", r"s\1"),
    (r"g([ei])", r"j\1"),
    (r"ss", "s"),
    (r"sc", "s"),
    (r"[cq]", "k"),
    (r"z", "s"),
    (r"y", "i"),
    (r"w", "v"),
    (r"h", ""),
    (r"m\b", "n"),
    (r"(.)\1+", r"\1"),
)


def phonetic_key(text: str) -> str:
    """Dobra fonetica de PT-BR sobre a chave normalizada (pura).

    "Celso" -> "selso" e "Censo" -> "senso" ficam a uma letra de
    distancia; e assim que garble de ASR se parece.
    """
    key = normalize_key(text)
    for pattern, replacement in _PHONETIC_RULES:
        key = re.sub(pattern, replacement, key)
    return key


def consonant_skeleton(text: str) -> str:
    """So as consoantes — casa garble de sigla ("BGA" e "IBGE" -> "bg")."""
    return re.sub(r"[aeiou0-9 ]", "", normalize_key(text))


def similarity(left: str, right: str) -> float:
    """Semelhanca grafica/fonetica em [0, 1] (pura, stdlib)."""
    left_key, right_key = normalize_key(left), normalize_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    scores = [
        SequenceMatcher(None, left_key, right_key).ratio(),
        SequenceMatcher(None, phonetic_key(left), phonetic_key(right)).ratio(),
    ]
    left_skeleton, right_skeleton = consonant_skeleton(left), consonant_skeleton(right)
    # Esqueleto igual e pista forte de sigla mutilada, mas so com corpo:
    # nomes curtos ("Ana"/"Ono") colidiriam por acaso.
    if len(left_skeleton) >= 2 and left_skeleton == right_skeleton:
        scores.append(0.9)
    return max(scores)


# ---------------------------------------------------------------------------
# Nucleo puro: agrupamento das mencoes
# ---------------------------------------------------------------------------

def aggregate_mentions(mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Menções -> formas distintas com contagem e exemplos (pura).

    Variacoes de caixa/acento contam como a MESMA forma; a grafia
    exibida e a mais frequente.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for mention in mentions:
        texto = " ".join(str(mention.get("texto") or "").split())
        key = normalize_key(texto)
        if len(key) < MIN_FORM_CHARS:
            continue
        bucket = buckets.setdefault(key, {
            "key": key, "surfaces": Counter(), "tipos": Counter(),
            "total": 0, "exemplos": [],
        })
        bucket["surfaces"][texto] += 1
        bucket["tipos"][str(mention.get("tipo") or "")] += 1
        bucket["total"] += 1
        if len(bucket["exemplos"]) < MAX_EXAMPLES:
            bucket["exemplos"].append({
                "interview_id": str(mention.get("interview_id") or ""),
                "turn_id": str(mention.get("turn_id") or ""),
                "start": float(mention.get("start", 0) or 0),
                "trecho": " ".join(str(mention.get("trecho") or "").split())[:200],
            })
    forms = []
    for bucket in buckets.values():
        forms.append({
            "key": bucket["key"],
            "texto": bucket["surfaces"].most_common(1)[0][0],
            "tipo": bucket["tipos"].most_common(1)[0][0],
            "total": bucket["total"],
            "exemplos": bucket["exemplos"],
        })
    forms.sort(key=lambda form: (-form["total"], form["texto"]))
    return forms


def group_variants(
    forms: list[dict[str, Any]],
    known: list[str] | tuple[str, ...] = (),
    threshold: float = SIMILARITY_THRESHOLD,
    minority_ratio: float = MINORITY_RATIO,
) -> list[dict[str, Any]]:
    """Agrupa formas parecidas sob um canonico (pura, testavel).

    Duas travas contra falso agrupamento:
    - **frequencia**: so absorve forma que seja minoria clara da
      canonica (garble de ASR e raro; "Maria" e "Mario", ambos
      frequentes, nunca se fundem);
    - **nomes declarados**: forma que o pesquisador declarou em
      "## Nomes conhecidos" e canonica por definicao e nunca e
      absorvida por outra.

    Nome declarado que o ASR NUNCA acertou entra como canonico proprio
    (total 0) e absorve as variantes sem a trava de frequencia — e o
    caso "so aparece 'Celso' porque a transcricao sempre errou".
    """
    known_keys = {normalize_key(name): str(name).strip() for name in known if normalize_key(name)}
    groups: list[dict[str, Any]] = []
    for name_key, name in known_keys.items():
        groups.append({
            "canonico": name, "key": name_key, "tipo": "", "total": 0,
            "conhecido": True, "membros": [], "exemplos": [],
        })
    for form in forms:
        if form["key"] in known_keys:
            group = next(g for g in groups if g["key"] == form["key"])
            group["total"] += form["total"]
            group["tipo"] = group["tipo"] or form["tipo"]
            group["membros"].append(form)
            group["exemplos"] = (group["exemplos"] + form["exemplos"])[:MAX_EXAMPLES]
            continue
        best = None
        best_score = 0.0
        for group in groups:
            score = similarity(form["texto"], group["canonico"])
            if score < threshold or score <= best_score:
                continue
            # Declarado pelo pesquisador dispensa a trava de frequencia.
            if not group["conhecido"] and form["total"] > max(1, minority_ratio * group["total"]):
                continue
            best, best_score = group, score
        if best is None:
            groups.append({
                "canonico": form["texto"], "key": form["key"], "tipo": form["tipo"],
                "total": form["total"], "conhecido": False,
                "membros": [form], "exemplos": list(form["exemplos"]),
            })
        else:
            best["total"] += form["total"]
            best["tipo"] = best["tipo"] or form["tipo"]
            best["membros"].append(form)
    return [group for group in groups if group["membros"]]


def build_glossary(
    mentions: list[dict[str, Any]],
    known: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Glossario completo a partir das mencoes brutas (pura)."""
    groups = group_variants(aggregate_mentions(mentions), known)
    entradas = []
    for group in groups:
        canonical_key = normalize_key(group["canonico"])
        variantes = [
            {"texto": membro["texto"], "total": membro["total"], "exemplos": membro["exemplos"]}
            for membro in group["membros"] if membro["key"] != canonical_key
        ]
        variantes.sort(key=lambda item: (-item["total"], item["texto"]))
        exemplos = group["exemplos"] or (group["membros"][0]["exemplos"] if group["membros"] else [])
        entradas.append({
            "canonico": group["canonico"],
            "tipo": group["tipo"],
            "total": group["total"],
            "conhecido": bool(group["conhecido"]),
            "variantes": variantes,
            "exemplos": exemplos[:MAX_EXAMPLES],
        })
    entradas.sort(key=lambda entrada: (-entrada["total"], entrada["canonico"]))
    return {
        "version": GLOSSARY_VERSION,
        "updated_at": now_utc(),
        "total_mencoes": len(mentions),
        "entradas": entradas,
    }


def format_glossary_prompt(glossary: dict[str, Any], max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Bloco compacto para os prompts do Qwen (pura); "" se nao ha o que dizer."""
    lines = []
    for entrada in glossary.get("entradas") or []:
        variantes = [v["texto"] for v in entrada.get("variantes") or []]
        if not variantes:
            continue
        lines.append(f"- {entrada['canonico']} (tambem transcrito como: {', '.join(variantes)})")
    if not lines:
        return ""
    block = ""
    for line in lines:
        if len(block) + len(line) + 1 > max_chars:
            break
        block += line + "\n"
    if not block:
        return ""
    return (
        "=== GLOSSARIO DE NOMES ===\n"
        "Estes nomes aparecem transcritos de formas diferentes; trate as "
        "variantes como a mesma entidade.\n"
        f"{block}\n"
    )


def format_glossary_report(glossary: dict[str, Any], project_name: str = "") -> str:
    """Relatorio markdown legivel (pura)."""
    entradas = glossary.get("entradas") or []
    suspeitas = [e for e in entradas if e.get("variantes")]
    titulo = f"# Glossario de nomes — {project_name}" if project_name else "# Glossario de nomes"
    lines = [
        titulo, "",
        f"Gerado pela AI local em {glossary.get('updated_at', '')} — "
        f"{len(entradas)} nomes em {glossary.get('total_mencoes', 0)} mencoes.",
        "",
        "Nada foi alterado nas transcricoes: este documento e para leitura e conferencia.",
        "",
    ]
    if suspeitas:
        lines += [
            "## Grafias a conferir", "",
            "A mesma entidade aparece escrita de formas diferentes. Confira os "
            "trechos e, se for erro de transcricao, corrija com a busca de palavras.",
            "",
        ]
        for entrada in suspeitas:
            variantes = ", ".join(
                f"**{v['texto']}** ({v['total']}x)" for v in entrada["variantes"])
            lines.append(f"- **{entrada['canonico']}** ({entrada['total']}x) — variantes: {variantes}")
            for variante in entrada["variantes"]:
                for exemplo in variante.get("exemplos") or []:
                    trecho = exemplo.get("trecho") or ""
                    if trecho:
                        lines.append(
                            f"    - {exemplo.get('interview_id', '')} "
                            f"{_clock(exemplo.get('start', 0))}: \"{trecho}\"")
        lines.append("")
    by_type: dict[str, list[dict[str, Any]]] = {}
    for entrada in entradas:
        by_type.setdefault(entrada.get("tipo") or "outros", []).append(entrada)
    rotulos = {"pessoa": "Pessoas", "lugar": "Lugares", "instituicao": "Instituicoes"}
    for tipo, itens in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        lines += [f"## {rotulos.get(tipo, tipo.capitalize() or 'Outros')}", ""]
        for entrada in itens:
            marca = " *(declarado no contexto da pesquisa)*" if entrada.get("conhecido") else ""
            lines.append(f"- {entrada['canonico']} — {entrada['total']}x{marca}")
        lines.append("")
    return "\n".join(lines)


def _clock(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Caminhos e orquestracao
# ---------------------------------------------------------------------------

def glossary_path(paths: Paths) -> Path:
    from .project_store import INTERNAL_PROJECT_DIR

    return paths.output_root / INTERNAL_PROJECT_DIR / "glossario.json"


def glossary_report_path(paths: Paths) -> Path:
    return paths.review_dir / "final" / "md" / "glossario_do_projeto.md"


def load_glossary(paths: Paths) -> dict[str, Any]:
    """Glossario gravado; {} quando ausente ou ilegivel."""
    from .utils import read_json

    target = glossary_path(paths)
    if not target.exists():
        return {}
    try:
        payload = read_json(target)
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001 - glossario e opcional
        return {}


def glossary_prompt_file(paths: Paths) -> Path | None:
    """Grava o bloco de glossario num temporario para o worker; None se
    nao ha variantes a declarar (nada a acrescentar ao prompt).

    O chamador apaga o arquivo ao terminar. Fica fora do Dropbox pelo
    mesmo motivo dos outros temporarios de AI (texto de entrevista).
    """
    import uuid

    from . import runtime

    block = format_glossary_prompt(load_glossary(paths))
    if not block:
        return None
    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / f"glossario_{uuid.uuid4().hex[:12]}.txt"
    target.write_text(block, encoding="utf-8")
    return target


def glossary_ready() -> tuple[bool, str]:
    """(pronto, motivo) — GLiNER roda em CPU; nao exige placa NVIDIA."""
    from . import model_manager, runtime

    asset = model_manager.optional_model(NER_ASSET_KEY)
    cached = model_manager.cached_snapshot_path(
        asset.repo_id, runtime.model_cache_dir(), revision=asset.revision)
    if cached is None:
        return False, (
            f"O modelo de nomes ({asset.label}, ~{asset.estimated_gb:.1f} GB) "
            "ainda nao foi baixado neste computador.")
    return True, ""


def run_glossario(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Varre as entrevistas transcritas e grava o glossario; 0 = sucesso."""
    import json
    import uuid

    from . import llm_env, model_manager, research_context, runtime
    from .review_store import canonical_path, review_path
    from .utils import parse_progress_json_line, run_command_stream

    def emit(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": "glossario_progress", "progress": progress,
                               "message": message})

    ready, reason = glossary_ready()
    if not ready:
        print(reason)
        return 1
    if not llm_env.llm_env_ready():
        emit(2, "Preparando o ambiente de AI (primeira vez)...")
        if llm_env.create_llm_env(
                use_cuda=runtime.has_nvidia_gpu(), progress_callback=progress_callback) != 0:
            print("Nao foi possivel preparar o ambiente de AI local.")
            return 1

    from .manifest import selected_rows
    targets = []
    for row in selected_rows(rows, ids):
        interview_id = row["interview_id"]
        source = review_path(paths, interview_id)
        if not source.exists():
            source = canonical_path(paths, interview_id)
        if source.exists():
            targets.append({"interview_id": interview_id, "path": str(source)})
    if not targets:
        print("Nenhuma entrevista transcrita para ler.")
        return 1

    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    alvos_path = tmp_dir / f"glossario_{token}_alvos.json"
    out_path = tmp_dir / f"glossario_{token}_mencoes.json"
    alvos_path.write_text(json.dumps(targets, ensure_ascii=False), encoding="utf-8")

    asset = model_manager.optional_model(NER_ASSET_KEY)
    worker = Path(__file__).resolve().parent / "llm_worker.py"
    command = [
        str(llm_env.llm_python()), "-B", str(worker),
        "--task", "entidades",
        "--alvos-file", str(alvos_path),
        "--out", str(out_path),
        "--model-repo", asset.repo_id,
        "--hf-cache", str(runtime.model_cache_dir()),
    ]

    def on_output(line: str) -> None:
        detail = parse_progress_json_line(line)
        if detail is not None and progress_callback is not None:
            inner = int(detail.get("progress") or 0)
            progress_callback({
                "event": "glossario_progress",
                "progress": 5 + (inner * 85) // 100,
                "message": str(detail.get("message") or ""),
            })

    try:
        completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
        if completed.returncode != 0 or not out_path.exists():
            print(f"A leitura de nomes falhou (codigo {completed.returncode}).")
            return 1
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    finally:
        for path in (alvos_path, out_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    emit(92, "Agrupando as variantes de grafia...")
    # O contexto da pesquisa e a declaracao humana do que e canonico; se
    # nao existe, deixamos o modelo pronto para o usuario preencher.
    research_context.write_template_if_missing(paths)
    known = research_context.known_names(research_context.load_research_context(paths))
    glossary = build_glossary(payload.get("mencoes") or [], known)
    target = glossary_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, glossary)
    report = glossary_report_path(paths)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        format_glossary_report(glossary, paths.project_root.name), encoding="utf-8")
    emit(100, f"Glossario pronto: {len(glossary['entradas'])} nomes.")
    return 0
