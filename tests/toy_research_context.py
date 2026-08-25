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

print("PASS: toy_research_context")
