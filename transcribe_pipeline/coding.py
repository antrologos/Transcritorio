"""Codificacao (rotulos) das entrevistas e exportacao (Parte B, 2026-09-03).

Armazenamento proprio, separado da transcricao, em
Transcricoes/08_codificacao/: codebook.json (codigos: id, nome, descricao,
cor `hue_deg`, `parent_id`) e codings.json (uma linha por codigo aplicado a um
trecho: entrevista, faixa de turnos, `quote` para conferencia, camada,
autor, origem "tema" | "busca" | "manual"). Um trecho pode ter varios
codigos (uma coding por codigo). JSON direto, sem rename (regra Dropbox).

Exportacao:
- `.qualilab` (formato nativo do QualiLab, github.com/LuizPF42/QualiLab):
  um documento por entrevista com `content` = texto plano deterministico
  ("[hh:mm:ss] Falante: texto" por bloco), `codes` com cores e `codings`
  com `span_start`/`span_end` calculados nesse texto — a invariante
  `quote == content[span_start:span_end]` e garantida por construcao
  (offsets de string Python == JS quando nao ha emojis fora do BMP; os
  raros sao trocados por U+FFFD no content para manter a conta).
- CSV (entrevista, inicio, fim, codigo(s), trecho).
REFI-QDA (.qdpx) fica para uma fase seguinte (validar em importadores reais).
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .utils import read_json, write_json

GOLDEN_ANGLE = 137.508
LAYER_DEFAULT = "individual"
AUTHOR_DEFAULT = "Transcritório"


# ---------------------------------------------------------------------------
# Caminhos e leitura/escrita
# ---------------------------------------------------------------------------

def coding_dir(paths: Paths) -> Path:
    return paths.output_root / "08_codificacao"


def codebook_path(paths: Paths) -> Path:
    return coding_dir(paths) / "codebook.json"


def codings_path(paths: Paths) -> Path:
    return coding_dir(paths) / "codings.json"


def _load(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:  # noqa: BLE001 - arquivo corrompido = vazio (nunca crash)
        return []
    items = payload.get(key) if isinstance(payload, dict) else None
    return [i for i in (items or []) if isinstance(i, dict)]


def load_codebook(paths: Paths) -> list[dict[str, Any]]:
    return _load(codebook_path(paths), "codes")


def save_codebook(paths: Paths, codes: list[dict[str, Any]]) -> None:
    target = codebook_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, {"version": 1, "codes": codes})


def load_codings(paths: Paths) -> list[dict[str, Any]]:
    return _load(codings_path(paths), "codings")


def save_codings(paths: Paths, codings: list[dict[str, Any]]) -> None:
    target = codings_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, {"version": 1, "codings": codings})


# ---------------------------------------------------------------------------
# Codebook (puro sobre listas)
# ---------------------------------------------------------------------------

def hue_for(position: int) -> int:
    """Matiz distinta por posicao (angulo dourado), em graus (puro)."""
    return int(round((position * GOLDEN_ANGLE) % 360))


def find_code(codes: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = " ".join(str(name or "").split()).casefold()
    for code in codes:
        if " ".join(str(code.get("name") or "").split()).casefold() == wanted:
            return code
    return None


def add_code(codes: list[dict[str, Any]], name: str, description: str = "",
             parent_id: str | None = None) -> dict[str, Any]:
    """Cria (ou devolve o existente de mesmo nome, sem repetir). Puro."""
    existing = find_code(codes, name)
    if existing is not None:
        if description and not existing.get("description"):
            existing["description"] = description
        return existing
    code = {
        "id": f"code_{uuid.uuid4().hex[:8]}",
        "name": " ".join(str(name).split()),
        "description": " ".join(str(description or "").split()),
        "parent_id": parent_id,
        "hue_deg": hue_for(len(codes)),
        "position": len(codes),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    codes.append(code)
    return code


def rename_code(codes: list[dict[str, Any]], code_id: str, name: str, description: str | None = None) -> bool:
    for code in codes:
        if code.get("id") == code_id:
            code["name"] = " ".join(str(name).split()) or code["name"]
            if description is not None:
                code["description"] = " ".join(str(description).split())
            return True
    return False


def parse_codebook_seed(markdown: str) -> list[tuple[str, str]]:
    """Linhas '- codigo: descricao' da secao '## Codebook inicial' do
    contexto_pesquisa.md -> [(nome, descricao)] (puro)."""
    seeds: list[tuple[str, str]] = []
    inside = False
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            inside = stripped[3:].strip().casefold().startswith("codebook")
            continue
        if not inside or not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if not body or body.startswith("("):
            continue                       # linha-modelo do template, entre parenteses
        name, _, desc = body.partition(":")
        name = name.strip().strip("`").strip()
        if name and not name.startswith("("):
            seeds.append((name, desc.strip()))
    return seeds


# ---------------------------------------------------------------------------
# Codings (puro sobre listas)
# ---------------------------------------------------------------------------

def coding_key(coding: dict[str, Any]) -> tuple[str, int, int, str]:
    return (str(coding.get("interview_id")), int(coding.get("t_from", -1)),
            int(coding.get("t_to", -1)), str(coding.get("code_id")))


def add_coding(codings: list[dict[str, Any]], interview_id: str, t_from: int, t_to: int,
               code_id: str, quote: str = "", origem: str = "manual",
               author: str = AUTHOR_DEFAULT, layer: str = LAYER_DEFAULT) -> dict[str, Any] | None:
    """Aplica um codigo a uma faixa de turnos; None se ja existia (puro)."""
    candidate = {
        "id": f"cod_{uuid.uuid4().hex[:8]}",
        "interview_id": str(interview_id),
        "t_from": int(t_from),
        "t_to": int(t_to),
        "code_id": str(code_id),
        "quote": " ".join(str(quote or "").split())[:600],
        "origem": origem,
        "layer": layer,
        "author_name": author,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    key = coding_key(candidate)
    if any(coding_key(c) == key for c in codings):
        return None
    codings.append(candidate)
    return candidate


def remove_coding(codings: list[dict[str, Any]], coding_id: str) -> bool:
    before = len(codings)
    codings[:] = [c for c in codings if c.get("id") != coding_id]
    return len(codings) < before


def codings_for(codings: list[dict[str, Any]], interview_id: str) -> list[dict[str, Any]]:
    return sorted((c for c in codings if c.get("interview_id") == interview_id),
                  key=lambda c: (int(c.get("t_from", 0)), int(c.get("t_to", 0))))


def codes_at(codings: list[dict[str, Any]], interview_id: str, t_from: int, t_to: int) -> list[str]:
    """Ids dos codigos aplicados a ESTA faixa exata de turnos, na ordem em
    que foram aplicados (puro).

    Exata, e nao "que encosta na faixa": o rotulo do trecho e o botao de
    tirar codigo tem de falar da mesma coisa — mostrar o codigo de um
    trecho vizinho maior e depois nao conseguir remove-lo confunde."""
    out: list[str] = []
    for c in codings:
        if (c.get("interview_id") != interview_id
                or int(c.get("t_from", -1)) != t_from or int(c.get("t_to", -1)) != t_to):
            continue
        if c["code_id"] not in out:
            out.append(c["code_id"])
    return out


def contiguous_ranges(turns: list[dict[str, Any]], t_from: int, t_to: int,
                      speakers: set[str] | None) -> list[tuple[int, int]]:
    """As faixas de turnos que o codigo deve pintar dentro de um trecho (puro).

    Um trecho quase sempre e uma troca (pergunta + resposta). Quando o
    usuario tira alguem da analise, o codigo nao pode cobrir a fala dessa
    pessoa: um trecho pergunta-resposta-pergunta-resposta vira DUAS faixas
    do mesmo codigo, e a pergunta fica no documento, logo acima, como
    contexto. `speakers=None` devolve a faixa inteira, como antes."""
    if speakers is None:
        return [(int(t_from), int(t_to))]
    ranges: list[tuple[int, int]] = []
    inicio: int | None = None
    ultimo = min(int(t_to), len(turns) - 1)
    for index in range(max(0, int(t_from)), ultimo + 1):
        label = " ".join(str(turns[index].get("human_label") or turns[index].get("speaker") or "").split())
        if label in speakers:
            if inicio is None:
                inicio = index
            fim = index
        elif inicio is not None:
            ranges.append((inicio, fim))
            inicio = None
    if inicio is not None:
        ranges.append((inicio, fim))
    return ranges


def apply_theme_as_code(codes: list[dict[str, Any]], codings: list[dict[str, Any]],
                        theme: dict[str, Any], skip: set[tuple[str, int, int]] = frozenset(),
                        code_name: str | None = None) -> tuple[dict[str, Any], int]:
    """Cria o codigo do tema (se preciso) e aplica a todas as passagens
    (menos as em `skip`). Devolve (codigo, quantas codings novas). Puro."""
    code = add_code(codes, code_name or theme.get("name") or theme.get("id"),
                    description=str(theme.get("description") or ""))
    created = 0
    for p in theme.get("passages") or []:
        key = (str(p["interview_id"]), int(p["t_from"]), int(p["t_to"]))
        if key in skip:
            continue
        if add_coding(codings, p["interview_id"], p["t_from"], p["t_to"], code["id"],
                      quote=str(p.get("text") or ""), origem="tema") is not None:
            created += 1
    return code, created


# ---------------------------------------------------------------------------
# Texto plano deterministico por entrevista (base dos offsets do export)
# ---------------------------------------------------------------------------

def _clock(seconds: float) -> str:
    total = int(seconds or 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _bmp_only(text: str) -> str:
    """Caracteres fora do BMP (emojis) ocupam 2 unidades em JS: trocar por
    U+FFFD mantem os offsets iguais nos dois lados."""
    return "".join(ch if ord(ch) <= 0xFFFF else "�" for ch in text)


def render_document(turns: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int]]]:
    """(content, spans) — content = um bloco por turno "[hh:mm:ss] Falante:
    texto", separados por linha em branco; spans[i] = (inicio, fim) do
    TEXTO do turno i dentro do content (puro)."""
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, turn in enumerate(turns):
        # O rotulo tambem passa por _bmp_only: um falante batizado com emoji
        # ("Maria 😀") deslocaria TODOS os offsets seguintes no leitor, que
        # conta em UTF-16 — e o erro seria invisivel deste lado.
        label = _bmp_only(" ".join(str(turn.get("human_label") or turn.get("speaker") or "").split())) or "?"
        text = _bmp_only(" ".join(str(turn.get("text") or "").split()))
        head = f"[{_clock(float(turn.get('start', 0) or 0))}] {label}: "
        if index:
            parts.append("\n\n")
            cursor += 2
        parts.append(head)
        cursor += len(head)
        spans.append((cursor, cursor + len(text)))
        parts.append(text)
        cursor += len(text)
    return "".join(parts), spans


# ---------------------------------------------------------------------------
# Exportacao
# ---------------------------------------------------------------------------

def _span_of(spans: list[tuple[int, int]], t_from: int, t_to: int) -> tuple[int, int] | None:
    """(inicio, fim) da faixa de turnos no texto plano; None quando a faixa
    nao existe naquele documento (turno fora do intervalo, ordem invertida) —
    o mesmo criterio vale para o .qualilab e para o CSV (puro)."""
    if not spans or t_from < 0 or t_to >= len(spans) or t_from > t_to:
        return None
    return spans[t_from][0], spans[t_to][1]


_LEADING_LABEL = re.compile(r"^[^:\n]{1,40}:\s+")
PROBE_LETTERS = 60


def _anchor(text: str) -> str:
    """Assinatura de um trecho: so as letras das PALAVRAS DE FALA (puro).

    Fora ficam os rotulos de falante (todo pedaco terminado em ":") e os
    digitos, que so existem de um lado — o texto plano do export carimba a
    hora de cada bloco e repete o rotulo, a passagem guardada na coding tem
    o rotulo em linha, e o falante pode ter sido REBATIZADO entre codificar
    e exportar ("SPEAKER_01" vira "Entrevistado"). Comparar so a fala deixa
    a conferencia imune a tudo isso."""
    palavras = [p for p in str(text or "").split() if not p.endswith(":")]
    folded = unicodedata.normalize("NFD", " ".join(palavras).lower())
    return "".join(c for c in folded if c.isalpha() and not unicodedata.combining(c))


def _probe(quote: str, size: int = PROBE_LETTERS) -> str:
    """As primeiras letras UTEIS do trecho guardado, para procura-lo no
    texto atual (puro). Tira tambem o rotulo composto que abre a passagem
    ("Maria Silva: ..."), que nao existe no texto do primeiro turno."""
    body = _LEADING_LABEL.sub("", " ".join(str(quote or "").split()), count=1)
    return _anchor(body)[:size]


def is_anchored(quote: str, span_text: str) -> bool:
    """O trecho guardado na coding ainda esta onde a coding diz que esta?

    A transcricao pode ter mudado depois da codificacao (dividir bloco,
    apagar o texto de um turno): os indices de turno continuam validos, mas
    passam a apontar para OUTRA fala. Sem esta conferencia o export sairia
    com a citacao errada debaixo do codigo, e a invariante
    quote == content[span] continuaria verdadeira — porque o proprio quote
    e recalculado do texto atual.

    A comparacao e por SONDAGEM, nao por igualdade: o `quote` vem da
    passagem do indice (rotulo do falante em linha, cortado em 600
    caracteres, e em turno longo so um pedaco do turno), enquanto o span
    cobre os turnos inteiros — exigir igualdade acusaria deriva em projeto
    intacto. Coding sem `quote` nao tem como ser conferida: passa (puro)."""
    probe = _probe(quote)
    return not probe or probe in _anchor(span_text)


def plan_codings(documents: list[dict[str, Any]], codes: list[dict[str, Any]],
                 codings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(colocadas, desancoradas) — onde cada coding cai no texto plano do
    seu documento. Usada pelo `.qualilab` e pelo CSV, para os dois dizerem a
    mesma coisa e contarem as mesmas perdas. Codings de entrevistas fora da
    lista de documentos, ou de codigos que nao existem, sao ignoradas (nao
    contam como perda); as que nao podem ser localizadas na transcricao
    ATUAL saem em `desancoradas` (puro)."""
    rendered: dict[str, tuple[str, str, list[tuple[int, int]]]] = {}
    for n, doc in enumerate(documents, start=1):
        content, spans = render_document(doc.get("turns") or [])
        rendered[str(doc["interview_id"])] = (f"doc-{n}", content, spans)
    code_ids = {c["id"] for c in codes}
    placed: list[dict[str, Any]] = []
    lost: list[dict[str, Any]] = []
    for coding in codings:
        target = rendered.get(str(coding.get("interview_id")))
        if target is None or coding.get("code_id") not in code_ids:
            continue
        doc_id, content, spans = target
        t_from, t_to = int(coding.get("t_from", 0)), int(coding.get("t_to", 0))
        span = _span_of(spans, t_from, t_to)
        if span is None:
            lost.append(coding)
            continue
        quote = content[span[0]:span[1]]
        if not is_anchored(str(coding.get("quote") or ""), quote):
            lost.append(coding)
            continue
        placed.append({"coding": coding, "doc_id": doc_id, "interview_id": str(coding["interview_id"]),
                       "t_from": t_from, "t_to": t_to,
                       "span_start": span[0], "span_end": span[1], "quote": quote})
    return placed, lost


def build_qualilab(project_name: str, documents: list[dict[str, Any]],
                   codes: list[dict[str, Any]], codings: list[dict[str, Any]],
                   author: str = AUTHOR_DEFAULT) -> dict[str, Any]:
    """Projeto .qualilab (puro). documents: [{interview_id, name, turns}].
    Codings fora das entrevistas exportadas sao ignoradas; as que nao podem
    ser localizadas na transcricao atual ficam de fora (ver `plan_codings`) —
    a invariante quote == content[span] vale por construcao."""
    docs_out = [{"id": f"doc-{n}", "name": str(doc.get("name") or doc["interview_id"]),
                 "content": render_document(doc.get("turns") or [])[0]}
                for n, doc in enumerate(documents, start=1)]
    codes_out = [{
        "id": c["id"], "parent_id": c.get("parent_id"), "name": c["name"],
        "hue": int(c.get("hue_deg", 0)) // 30, "depth": 1 if c.get("parent_id") else 0,
        "position": int(c.get("position", i)), "hue_deg": int(c.get("hue_deg", 0)),
    } for i, c in enumerate(codes)]
    placed, _lost = plan_codings(documents, codes, codings)
    codings_out = [{
        "id": f"c{n}", "document_id": item["doc_id"], "code_id": item["coding"]["code_id"],
        "span_start": item["span_start"], "span_end": item["span_end"], "quote": item["quote"],
        "layer": str(item["coding"].get("layer") or LAYER_DEFAULT),
        "author_name": str(item["coding"].get("author_name") or author),
    } for n, item in enumerate(placed, start=1)]
    return {
        "_meta": {"id": "file-project", "name": project_name, "code": "TRANSCRITORIO",
                  "mode": "individual", "displayName": author},
        "documents": docs_out,
        "categories": [],
        "doc_values": [],
        "codes": codes_out,
        "codings": codings_out,
        "memos": [{"scope": "project", "target_id": "file-project", "author_name": author,
                   "content": "Exportado pelo Transcritório: um documento por entrevista; "
                              "cada bloco é \"[hh:mm:ss] Falante: texto\"."}],
    }


def csv_rows(documents: list[dict[str, Any]], codes: list[dict[str, Any]],
             codings: list[dict[str, Any]]) -> list[list[str]]:
    """Linhas do CSV (cabecalho incluso): entrevista, inicio, fim, codigos,
    trecho — uma linha por trecho codificado, codigos separados por ';' (puro).
    O trecho sai do MESMO texto plano do .qualilab e passa pela mesma
    conferencia de ancoragem, para os dois exports dizerem a mesma coisa."""
    name_by_id = {c["id"]: c["name"] for c in codes}
    turns_by_iid = {str(d["interview_id"]): d.get("turns") or [] for d in documents}
    placed, _lost = plan_codings(documents, codes, codings)
    grouped: dict[tuple[str, int, int], list[str]] = {}
    quotes: dict[tuple[str, int, int], str] = {}
    for item in placed:
        key = (item["interview_id"], item["t_from"], item["t_to"])
        grouped.setdefault(key, []).append(name_by_id[item["coding"]["code_id"]])
        quotes.setdefault(key, item["quote"])
    rows = [["entrevista", "inicio", "fim", "codigos", "trecho"]]
    for (iid, t_from, t_to), names in sorted(grouped.items()):
        turns = turns_by_iid[iid]
        start = float(turns[t_from].get("start", 0) or 0) if t_from < len(turns) else 0.0
        end = float(turns[t_to].get("end", turns[t_to].get("start", 0)) or 0) if t_to < len(turns) else 0.0
        rows.append([iid, _clock(start), _clock(end), "; ".join(sorted(set(names))), quotes[(iid, t_from, t_to)]])
    return rows


def _scoped_documents(interview_ids: list[str], load_turns: Callable[[str], list[dict[str, Any]]],
                      titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return [{"interview_id": iid, "name": (titles or {}).get(iid) or iid, "turns": load_turns(iid)}
            for iid in interview_ids]


def export_qualilab(paths: Paths, interview_ids: list[str], out_path: Path,
                    project_name: str, load_turns: Callable[[str], list[dict[str, Any]]],
                    titles: dict[str, str] | None = None) -> dict[str, int]:
    """Grava o .qualilab do escopo; devolve contagens, `desancoradas`
    inclusive (trechos que a transcricao atual nao confirma mais) — quem
    chama TEM de contar isso ao usuario."""
    documents = _scoped_documents(interview_ids, load_turns, titles)
    codes, codings = load_codebook(paths), load_codings(paths)
    payload = build_qualilab(project_name, documents, codes, codings)
    _placed, lost = plan_codings(documents, codes, codings)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"documents": len(payload["documents"]), "codes": len(payload["codes"]),
            "codings": len(payload["codings"]), "desancoradas": len(lost)}


def export_csv(paths: Paths, interview_ids: list[str], out_path: Path,
               load_turns: Callable[[str], list[dict[str, Any]]]) -> dict[str, int]:
    """Grava o CSV e devolve contagens (linhas de dados e desancoradas).

    UTF-8 com BOM e separador `;`: o Excel abre .csv pelo separador de
    listas do sistema, que no Windows em portugues e `;` — com virgula,
    tudo chega grudado na primeira coluna."""
    documents = _scoped_documents(interview_ids, load_turns)
    codes, codings = load_codebook(paths), load_codings(paths)
    rows = csv_rows(documents, codes, codings)
    _placed, lost = plan_codings(documents, codes, codings)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle, delimiter=";").writerows(rows)
    return {"linhas": len(rows) - 1, "desancoradas": len(lost)}


def coded_interview_ids(codings: list[dict[str, Any]]) -> list[str]:
    """Entrevistas que tem alguma codificacao, na ordem de aparicao (puro).
    O export usa isto para nunca deixar trabalho do usuario de fora."""
    out: list[str] = []
    for coding in codings:
        iid = str(coding.get("interview_id") or "")
        if iid and iid not in out:
            out.append(iid)
    return out
