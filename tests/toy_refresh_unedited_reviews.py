"""Toy test para app_service.refresh_unedited_reviews (D2.3).

Apos re-render, a transcricao editavel so e recriada do canonical quando o
usuario ainda NAO editou nada — edicoes humanas nunca sao descartadas.

Importa app_service (numpy transitivo); skip condicional no CI minimo.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from transcribe_pipeline import app_service, review_store
    from transcribe_pipeline.config import ensure_directories, load_config, make_paths
    from transcribe_pipeline.utils import write_json
except ImportError as exc:
    print(f"SKIP: dependencia ausente ({exc})")
    sys.exit(0)


def write_canonical(paths, interview_id: str, text: str) -> None:
    write_json(
        paths.canonical_dir / "json" / f"{interview_id}.canonical.json",
        {
            "interview_id": interview_id,
            "turns": [
                {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "human_label": "", "text": text}
            ],
        },
    )


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    config = load_config(None)
    config["project_root"] = "."
    paths = make_paths(config, base_dir=root)
    ensure_directories(paths)
    context = app_service.ProjectContext(
        config_path=root / "cfg.yaml", config=config, paths=paths,
        rows=[], project={}, metadata={}, jobs={},
    )

    # Dois arquivos: EDITADO (usuario mexeu) e LIMPO (sem edicoes)
    for interview_id in ("EDITADO", "LIMPO"):
        write_canonical(paths, interview_id, "texto antigo")
        review_store.create_review_from_canonical(paths, interview_id)
    review = review_store.load_review_transcript(paths, "EDITADO", create=False)
    turn_id = review_store.review_turns(review)[0]["id"]
    review_store.set_turn_text(review, turn_id, "texto corrigido pelo usuario")
    review_store.save_review_transcript(paths, "EDITADO", review)

    # Novo render muda o canonical dos dois
    write_canonical(paths, "EDITADO", "texto novo do render")
    write_canonical(paths, "LIMPO", "texto novo do render")

    result = app_service.refresh_unedited_reviews(context, ["EDITADO", "LIMPO", "INEXISTENTE"])
    assert result.ok

    clean = review_store.load_review_transcript(paths, "LIMPO", create=False)
    assert review_store.review_turns(clean)[0]["text"] == "texto novo do render"
    print("PASS: review sem edicoes e recriada do canonical novo")

    edited = review_store.load_review_transcript(paths, "EDITADO", create=False)
    assert review_store.review_turns(edited)[0]["text"] == "texto corrigido pelo usuario"
    assert edited.get("edits"), "trilha de edicoes preservada"
    print("PASS: review com edicoes humanas fica intacta")

    print("PASS: id inexistente ignorado sem crash")

print()
print("PASS: toy_refresh_unedited_reviews")
