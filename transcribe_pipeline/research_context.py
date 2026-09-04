"""Contexto de pesquisa do projeto (plano-programa v0.2.0, fase 2.0.a).

Arquivo opcional por projeto (00_config/contexto_pesquisa.md) onde o
pesquisador descreve o estudo, cola o roteiro de perguntas, o codebook
inicial e a lista de nomes conhecidos (participantes, lugares,
instituicoes). E o grounding de todas as funcionalidades de LLM local:
- QC por persona: reconhece perguntas DO roteiro em vez de adivinhar;
- Sumario: indice tematico alinhado aos eixos do roteiro + temas
  emergentes fora dele;
- Codificacao QDA: codigos consistentes com o codebook do pesquisador;
- Anonimizacao: nomes conhecidos viram deteccao deterministica.

Stdlib pura; sem dependencia de LLM. O arquivo e escrito/editado pelo
usuario (template gerado sob demanda); nunca e sobrescrito se existir.
"""
from __future__ import annotations

from pathlib import Path

from .config import Paths

CONTEXT_FILENAME = "contexto_pesquisa.md"

KNOWN_NAMES_HEADER = "## Nomes conhecidos"

TEMPLATE = """# Contexto da pesquisa

Preencha o que fizer sentido; tudo e opcional. Este arquivo alimenta as
funcionalidades de analise local (sumario, codificacao, anonimizacao,
verificacao de papeis). Nada dele sai da sua maquina.

## Sobre o estudo

(Descreva em poucas linhas: tema, objetivo, populacao entrevistada.)

## Roteiro de perguntas

(Cole aqui o roteiro/topico-guia usado nas entrevistas.)

## Codebook inicial

(Se ja tiver codigos/temas definidos, liste um por linha: `- codigo: descricao`.)

## Nomes conhecidos

(Nomes de pessoas, lugares e instituicoes citados nas entrevistas — um
por linha, comecando com `- `. Usados na anonimizacao assistida.)
"""


def context_path(paths: Paths) -> Path:
    return paths.config_dir / CONTEXT_FILENAME


def load_research_context(paths: Paths) -> str:
    """Conteudo do contexto, ou string vazia se nao existir/ilegivel."""
    path = context_path(paths)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - contexto e opcional, nunca fatal
        return ""


def write_template_if_missing(paths: Paths) -> Path:
    """Cria o template UMA vez; nunca sobrescreve o que o usuario escreveu."""
    path = context_path(paths)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    return path


def is_filled(context_text: str) -> bool:
    """O usuario escreveu alguma coisa, ou o arquivo ainda e so o template?

    Desde que o projeto passa a nascer com o template, TODO projeto tem o
    arquivo — e mandar as instrucoes ao usuario ("Preencha o que fizer
    sentido…", "(Descreva em poucas linhas…)") para a AI como se fossem o
    contexto da pesquisa e ruido em todos os prompts. Conta como conteudo
    qualquer linha que nao seja titulo `#`, nao esteja entre parenteses e
    nao faca parte do paragrafo fixo do template (puro)."""
    fixo = {" ".join(line.split()) for line in TEMPLATE.splitlines()}
    for raw_line in str(context_text or "").splitlines():
        line = " ".join(raw_line.split())
        if not line or line.startswith("#") or line.startswith("(") or line in fixo:
            continue
        return True
    return False


def known_names(context_text: str) -> list[str]:
    """Extrai a lista da secao 'Nomes conhecidos' (linhas `- Nome`).

    Ignora linhas em parenteses (instrucoes do template) e vazias;
    preserva a ordem; remove duplicatas mantendo a primeira ocorrencia.
    """
    names: list[str] = []
    in_section = False
    for raw_line in context_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_section = line.startswith(KNOWN_NAMES_HEADER)
            continue
        if not in_section or not line.startswith("- "):
            continue
        name = line[2:].strip()
        if name and not name.startswith("(") and name not in names:
            names.append(name)
    return names
