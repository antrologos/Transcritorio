"""Toy R3-c4: criterio puro do banner "Separar falantes" da lista.

Valida diar_offer_candidates() sem abrir janela: so entra quem esta
transcrita, sem NENHUMA fonte de separacao (exclusive/regular/canais)
e sem edicoes humanas.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline.review_studio_qt import diar_offer_candidates


@dataclass
class S:
    interview_id: str
    canonical_exists: bool = False
    review_exists: bool = False
    diarization_exclusive_exists: bool = False
    diarization_regular_exists: bool = False


statuses = [
    S("a_pendente"),                                        # nao transcrita
    S("b_alvo", canonical_exists=True),                     # ALVO
    S("c_ja_separada", canonical_exists=True,
      diarization_exclusive_exists=True),                   # ja tem exclusive
    S("d_regular", review_exists=True,
      diarization_regular_exists=True),                     # ja tem regular
    S("e_canais", review_exists=True),                      # separada por canais
    S("f_editada", canonical_exists=True, review_exists=True),  # tem edicoes
    S("g_alvo_review", review_exists=True),                 # ALVO (so review)
]

alvo = diar_offer_candidates(
    statuses, edited_ids={"f_editada"}, channel_ids={"e_canais"})
assert alvo == ["b_alvo", "g_alvo_review"], f"alvo inesperado: {alvo}"

# Sem exclusoes, os 2 alvos ganham companhia de e_canais e f_editada.
alvo2 = diar_offer_candidates(statuses, edited_ids=set(), channel_ids=set())
assert alvo2 == ["b_alvo", "e_canais", "f_editada", "g_alvo_review"], alvo2

# Lista vazia -> lista vazia (banner some).
assert diar_offer_candidates([], edited_ids=set(), channel_ids=set()) == []

print("PASS: toy_diar_offer (criterio do banner de oferta da lista)")
