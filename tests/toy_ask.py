"""Toy test: perguntar as entrevistas (fase 2.7) — partes puras."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.llm_worker import (
    SEM_RESPOSTA,
    format_trechos,
    validate_answer,
)

# --- format_trechos: numeracao e campos ---
trechos = [
    {"interview_id": "D06R", "inicio": "00:13:39", "label": "Entrevistada 1", "text": " o bônus  atrasou "},
    {"interview_id": "D08R", "inicio": "00:22:09", "label": "ENTREVISTADO", "text": "tinha um prazo pra cair"},
]
formatted = format_trechos(trechos)
assert formatted.splitlines()[0] == "[1] (D06R, 00:13:39, Entrevistada 1) o bônus atrasou"
assert "[2] (D08R" in formatted
print("PASS: format_trechos")

# --- validate_answer: citacao obrigatoria OU recusa exata ---
assert validate_answer("O pagamento atrasou [1] e havia um prazo [2].", 2) is True
assert validate_answer("O pagamento atrasou.", 2) is False           # sem citacao
assert validate_answer("Citando [3] inexistente.", 2) is False       # fora do intervalo
assert validate_answer(SEM_RESPOSTA, 2) is True                       # recusa honesta
assert validate_answer(f"  {SEM_RESPOSTA.lower()}  ", 0) is True
print("PASS: validate_answer")

# --- build_trechos (importa ask; precisa das deps do app) ---
try:
    from transcribe_pipeline.ask import build_trechos
    built = build_trechos([
        {"interview_id": "X", "start": 819.9, "label": "A", "text": "t", "similarity": 0.4},
    ])
    assert built[0]["n"] == 1 and built[0]["inicio"] == "00:13:39"
    assert built[0]["similarity"] == 0.4
    print("PASS: build_trechos")
except ImportError as exc:
    print(f"SKIP: build_trechos ({exc})")

print("PASS: toy_ask")
