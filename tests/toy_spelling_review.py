"""Toy 2026-08-31: revisao de grafias — cobertura honesta + sugestao editavel.

Dois becos do teste real do b43: (1) "Revisar grafias" dizia "nada a
corrigir" sem a analise NUNCA ter rodado (glossary_coverage_gap agora
distingue nunca-analisado / defasado / em dia); (2) a sugestao da AI
("UERG" -> "UEG") estava errada e nao havia como corrigi-la — o campo
"Corrigir para:" do SpellingReviewDialog agora e editavel e as
decisoes carregam a forma digitada.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_pipeline.glossario import glossary_coverage_gap

# ------------------------------------------------ cobertura (pura)
glossario = {"interview_ids": ["E01", "E02"], "entradas": []}
assert glossary_coverage_gap(glossario, ["E01", "E02"]) == []
assert glossary_coverage_gap(glossario, ["E01", "E02", "E03"]) == ["E03"]
assert glossary_coverage_gap(glossario, []) == []
# Glossario antigo SEM o campo: cobertura desconhecida -> nunca acusa
assert glossary_coverage_gap({"entradas": []}, ["E01"]) == []
assert glossary_coverage_gap({"interview_ids": "estranho"}, ["E01"]) == []
print("PASS: glossary_coverage_gap (defasadas / campo ausente)")

# ------------------------------------------------ dialogo (sugestao editavel)
try:
    from PySide6.QtWidgets import QApplication, QLineEdit
except ImportError as exc:  # CI minimo sem PySide6
    print(f"SKIP dialogo: PySide6 ausente ({exc})")
    print("PASS: toy_spelling_review (so a parte pura)")
    sys.exit(0)

import os as _os_iso
import tempfile as _tf_iso
_os_iso.environ["TRANSCRITORIO_HOME"] = _tf_iso.mkdtemp()

from transcribe_pipeline.review_studio_qt import SpellingReviewDialog

app = QApplication.instance() or QApplication([])

grupos = [
    {"variante": "UERG", "canonico": "UEG", "tipo": "ORG", "total": 1,
     "ocorrencias": [
         {"interview_id": "E25R", "turn_id": "t1", "start": 1.0,
          "span": [10, 14], "variante": "UERG", "canonico": "UEG",
          "trecho": "…parceria com a UERG e a…"},
     ]},
    {"variante": "Joao Silva", "canonico": "João Silva", "tipo": "PER",
     "total": 2,
     "ocorrencias": [
         {"interview_id": "E25R", "turn_id": "t2", "start": 5.0,
          "span": [0, 10], "variante": "Joao Silva",
          "canonico": "João Silva", "trecho": "Joao Silva disse…"},
     ]},
]
# A "janela" so precisa ser QWidget (parent) com open_search_hit.
from PySide6.QtWidgets import QWidget
stub = QWidget()
stub.open_search_hit = lambda *_a: None  # type: ignore[attr-defined]
dlg = SpellingReviewDialog(stub, grupos)

# Campo pre-preenchido com a sugestao da AI, um por grupo
edits = [e for e in dlg.findChildren(QLineEdit)]
assert [e.text() for e in edits] == ["UEG", "João Silva"], [e.text() for e in edits]

# Nada marcado -> nada selecionado
assert dlg.selected() == []

# Marca a ocorrencia do 1o grupo e CORRIGE a sugestao para UERJ
dlg._checks[0][0].setChecked(True)
edits[0].setText("UERJ")
decisoes = dlg.selected()
assert len(decisoes) == 1
assert decisoes[0]["canonico"] == "UERJ", decisoes[0]
assert decisoes[0]["variante"] == "UERG"
print("PASS: sugestao editavel entra na decisao (UERG -> UERJ)")

# Campo esvaziado volta para a sugestao original
edits[0].setText("   ")
assert dlg.selected()[0]["canonico"] == "UEG"
print("PASS: campo vazio volta para a sugestao da AI")

# O 2o grupo nao e afetado pela edicao do 1o
dlg._checks[1][0].setChecked(True)
por_variante = {d["variante"]: d["canonico"] for d in dlg.selected()}
assert por_variante["Joao Silva"] == "João Silva", por_variante
print("PASS: edicao e por grupo, sem vazamento")

print("PASS: toy_spelling_review")
