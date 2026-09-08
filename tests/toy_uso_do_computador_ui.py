"""Toy: a escolha "quanto do computador usar" na janela pré-lote — 2026-09-05.

O usuário pediu que a divisão do computador fosse uma escolha VISÍVEL, e
"desde o começo". A casa dela é a janela que abre antes do lote: é onde a
decisão acontece (a pessoa está prestes a entregar o computador a um lote
longo) e onde a estimativa de tempo já aparece.

Fixa quatro coisas:
1. os dois rádios existem quando há estimativa (ou seja, em máquina sem placa)
   e NÃO existem quando não há — sem placa de vídeo é que o processador é o
   recurso disputado;
2. o padrão da janela segue a preferência gravada da máquina;
3. `computer_use()` devolve o que foi marcado, e `None` quando nem foi
   oferecido — quem chama precisa distinguir "escolheu tudo" de "não perguntei";
4. os textos não falam de thread, núcleo, CPU ou processo.

Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import SpeakerCountDialog
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_settings  # noqa: E402

app = QApplication.instance() or QApplication([])

ESTIMATIVA = ("Neste computador (sem placa de vídeo), para 1 h de áudio: "
              "transcrição ≈ 7 min · separação de falantes ≈ 7 min.")

# --- sem estimativa (máquina com placa): a escolha nem aparece ---------------
sem = SpeakerCountDialog(2, None, estimate_text="", ask_counts=True)
assert sem.uso_rapido_radio is None and sem.uso_leve_radio is None
assert sem.computer_use() is None, "None = não perguntei; diferente de 'escolheu tudo'"
print("PASS: com placa de vídeo a escolha não é oferecida")

# --- com estimativa: os dois rádios, com o padrão da máquina -----------------
assert app_settings.computer_use() == "tudo", "instalação nova começa em 'tudo'"
com = SpeakerCountDialog(2, None, estimate_text=ESTIMATIVA, ask_counts=True)
assert com.uso_rapido_radio is not None and com.uso_leve_radio is not None
assert com.uso_rapido_radio.isChecked() and not com.uso_leve_radio.isChecked()
assert com.computer_use() == "tudo"
print("PASS: padrão da máquina é 'usa o computador inteiro'")

# --- marcar o outro muda a resposta -----------------------------------------
com.uso_leve_radio.setChecked(True)
assert com.computer_use() == "metade"
print("PASS: a escolha do usuário chega a quem pergunta")

# --- a preferência gravada volta como padrão na próxima vez ------------------
app_settings.save({"computer_use": "metade"})
assert app_settings.computer_use() == "metade"
de_novo = SpeakerCountDialog(2, None, estimate_text=ESTIMATIVA, ask_counts=True)
assert de_novo.uso_leve_radio.isChecked(), "a janela lembra a escolha da máquina"
assert de_novo.computer_use() == "metade"
app_settings.save({"computer_use": "tudo"})
print("PASS: a preferência é lembrada")

# --- vocabulário: sem jargão ------------------------------------------------
PROIBIDAS = ("thread", "núcleo", "nucleo", "cpu", "processo", "paralelo", " IA ")
textos = []
for radio in (com.uso_rapido_radio, com.uso_leve_radio):
    textos.append(radio.text())
    textos.append(radio.toolTip())
for texto in textos:
    baixo = texto.lower()
    for palavra in PROIBIDAS:
        assert palavra.strip().lower() not in baixo, f"jargão {palavra!r} em {texto!r}"
    assert "..." not in texto, "reticências têm de ser o caractere '…'"
assert any("computador inteiro" in t for t in textos)
assert any("25%" in t for t in textos), "o custo tem de estar dito, e medido"
print("PASS: textos sem jargão e com o custo dito")

print("PASS: toy_uso_do_computador_ui")
