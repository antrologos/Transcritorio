"""Toy test para research_context (plano-programa v0.2.0, fase 2.0.a)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import research_context as rc
from transcribe_pipeline.config import ensure_directories, load_config, make_paths

with tempfile.TemporaryDirectory() as tmp:
    config = load_config(None)
    paths = make_paths(config, base_dir=Path(tmp))
    ensure_directories(paths)

    # Sem arquivo: contexto vazio, nomes vazios
    assert rc.load_research_context(paths) == ""
    assert rc.known_names("") == []

    # Template criado uma vez; nao sobrescreve
    path = rc.write_template_if_missing(paths)
    assert path.exists()
    path.write_text("meu conteudo", encoding="utf-8")
    rc.write_template_if_missing(paths)
    assert rc.load_research_context(paths) == "meu conteudo"
    print("PASS: template nao sobrescreve")

    # known_names: so a secao certa, sem instrucoes nem duplicatas
    text = (
        "## Roteiro de perguntas\n- Isto nao e um nome\n\n"
        "## Nomes conhecidos\n\n"
        "(instrucao do template)\n"
        "- Carlos Alberto\n"
        "- IBGE\n"
        "- (comentario a ignorar)\n"
        "- Carlos Alberto\n"
        "-semhifen\n"
        "\n## Outra secao\n- Fora da secao\n"
    )
    assert rc.known_names(text) == ["Carlos Alberto", "IBGE"]
    print("PASS: known_names")

    # is_filled: o template intocado NAO vai para os prompts da AI
    assert rc.is_filled("") is False
    assert rc.is_filled(rc.TEMPLATE) is False, "template puro nao e contexto"
    assert rc.is_filled(rc.TEMPLATE + "\n") is False
    assert rc.is_filled(rc.TEMPLATE.replace(
        "(Descreva em poucas linhas: tema, objetivo, populacao entrevistada.)",
        "Entrevistas com recenseadores do Censo 2022.")) is True
    assert rc.is_filled("## Nomes conhecidos\n- IBGE\n") is True
    assert rc.is_filled("# So um titulo\n\n(so instrucao)\n") is False
    print("PASS: is_filled")

    # e o comando do worker so leva --context-file quando ha conteudo
    from transcribe_pipeline import ask as _ask
    rc.context_path(paths).write_text(rc.TEMPLATE, encoding="utf-8")
    assert _ask.context_worth_sending(paths) is False
    rc.context_path(paths).write_text(rc.TEMPLATE + "\nEstudo sobre o Censo.\n", encoding="utf-8")
    assert _ask.context_worth_sending(paths) is True
    print("PASS: context_worth_sending")

print("PASS: toy_research_context")
