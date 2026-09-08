"""Quem entra na análise: inventário de falantes e sugestão de quem conduz.

Em entrevista não se codifica quem pergunta; em grupo focal não se codifica
quem modera — mas os nomes desses papéis variam de projeto para projeto, e
há quem queira justamente analisar a fala de quem conduz. Por isso o app
PERGUNTA (decisão do usuário, 2026-09-03): esta camada só monta a lista de
rótulos com o peso de cada um e sugere, para o usuário conferir, quem
parece conduzir.

Stdlib pura, sem dependência de projeto — testável isoladamente.
"""
from __future__ import annotations

import unicodedata
from typing import Any

# Nomes de quem CONDUZ. Lista curta e explícita de propósito: é sugestão
# para o usuário conferir, nunca regra automática. Cobre os rótulos que o
# próprio app cria (project_store.default_speaker_labels: "Entrevistador"
# no 1:1, "Moderador" em grupo) e as variantes comuns escritas à mão.
CONDUCTOR_WORDS = frozenset({
    "entrevistador", "entrevistadora", "entrevistadores", "entrevistadoras",
    "moderador", "moderadora", "moderadores", "moderadoras",
    "pesquisador", "pesquisadora", "mediador", "mediadora",
    "facilitador", "facilitadora", "coordenador", "coordenadora",
    "condutor", "condutora", "interviewer", "moderator", "researcher",
})


def fold(text: str) -> str:
    """Minúsculas sem acento, para comparar rótulos escritos de formas
    diferentes ("Entrevistadora", "ENTREVISTADOR") (puro)."""
    normalized = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def turn_label(turn: dict[str, Any]) -> str:
    """Rótulo do turno: o nome humano quando existe, senão o id do falante.
    Mesma regra de `search._turn_label` — os dois têm de concordar, porque
    é por este texto que a escolha do usuário casa com os turnos."""
    return " ".join(str(turn.get("human_label") or turn.get("speaker") or "").split())


def looks_like_conductor(label: str) -> bool:
    """O rótulo parece ser de quem conduz? Basta uma palavra da lista
    aparecer ("Entrevistador 1", "Moderadora - Ana") (puro)."""
    palavras = {p.strip(".:,;-") for p in fold(label).split()}
    return bool(palavras & CONDUCTOR_WORDS)


def speaker_inventory(turns_by_interview: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """[{label, turns, words, interviews}] do escopo, do mais falante para o
    menos (puro). É o que a janela mostra para o usuário escolher."""
    dados: dict[str, dict[str, Any]] = {}
    for interview_id, turns in (turns_by_interview or {}).items():
        for turn in turns or []:
            label = turn_label(turn)
            if not label:
                continue
            item = dados.setdefault(label, {"label": label, "turns": 0, "words": 0, "_ids": set()})
            item["turns"] += 1
            item["words"] += len(str(turn.get("text") or "").split())
            item["_ids"].add(interview_id)
    inventario = []
    for item in dados.values():
        inventario.append({"label": item["label"], "turns": item["turns"],
                           "words": item["words"], "interviews": len(item["_ids"])})
    inventario.sort(key=lambda i: (-i["words"], -i["turns"], i["label"]))
    return inventario


def suggest_excluded(labels: list[str]) -> set[str]:
    """Quem vem SUGERIDO desmarcado (puro).

    Nunca sugere excluir todo mundo: se todos os rótulos parecem de quem
    conduz (projeto com um rótulo só, "Moderador 1"/"Moderador 2"), a
    sugestão é vazia — sobrar zero falante seria pior do que não sugerir."""
    candidatos = {label for label in labels if looks_like_conductor(label)}
    if not candidatos or len(candidatos) >= len(set(labels)):
        return set()
    return candidatos


def default_selection(labels: list[str]) -> list[str]:
    """Marcados por padrão = todos menos os sugeridos (puro), na ordem dada."""
    excluir = suggest_excluded(labels)
    return [label for label in labels if label not in excluir]


def describe_selection(inventario: list[dict[str, Any]], selecionados: list[str] | None) -> str:
    """Uma linha para a janela: quem conta e quem ficou de fora (puro)."""
    if selecionados is None:
        return "todos os falantes"
    escolhidos = [i for i in inventario if i["label"] in set(selecionados)]
    fora = [i for i in inventario if i["label"] not in set(selecionados)]
    if not fora:
        return "todos os falantes"
    if not escolhidos:
        return "nenhum falante escolhido"
    nomes = ", ".join(i["label"] for i in escolhidos)
    return f"{nomes} (fora: {', '.join(i['label'] for i in fora)})"
