"""Toy R2: DocsPanel (aba Documentos) — apresentacao pura e sinais."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from transcribe_pipeline.ui_docs_panel import DocEntry, DocsPanel

app = QApplication.instance() or QApplication([])

panel = DocsPanel()

# ---------------------------------------------------------------- vazio
panel.set_sections(None, [], [])
labels = [w.text() for w in panel.findChildren(QLabel)]
assert any("Abra uma entrevista" in t for t in labels), labels
print("PASS: estado vazio orienta")

# ---------------------------------------------------------------- secoes e estados
entradas = [
    DocEntry("export_docx", "Transcrição final (Word)", estado="existe",
             detalhe="exportada em 21/08", caminho="C:/x/doc.docx"),
    DocEntry("export_srt", "Legendas (SRT)", estado="ausente",
             detalhe="ainda não exportada", acao_rotulo="Exportar…",
             acao_chave="exportar"),
    DocEntry("resumo", "Resumo com temas", ai=True, estado="gerando"),
]
projeto = [
    DocEntry("glossario", "Glossário de nomes", ai=True, estado="existe",
             detalhe="gerado em 19/08", caminho="C:/x/glossario.md",
             extras=(("Revisar grafias…", "revisar_grafias"),)),
]
panel.set_sections("Maria", entradas, projeto)

textos = [w.text() for w in panel.findChildren(QLabel)]
assert any("DESTA ENTREVISTA (Maria)" in t for t in textos), textos
assert any("DO PROJETO" in t for t in textos)
assert any("gerando…" in t for t in textos), "estado gerando ausente"
assert any("Tudo isso fica na pasta Resultados" in t for t in textos)
botoes = {w.text() for w in panel.findChildren(QPushButton)}
assert "Abrir" in botoes and "Exportar…" in botoes and "▸" in botoes
assert "Revisar grafias…" in botoes and "Mostrar na pasta Resultados" in botoes
print("PASS: secoes, estados e botoes")

# titulo AI leva ✨ e o selo local no tooltip
ai_labels = [w for w in panel.findChildren(QLabel) if w.text().startswith("✨ ")]
assert ai_labels and all("nada sai do seu computador" in w.toolTip() for w in ai_labels)
print("PASS: identidade AI (✨ + selo local)")

# ---------------------------------------------------------------- sinais
recebidos: list[tuple[str, str]] = []
panel.open_requested.connect(lambda p: recebidos.append(("open", p)))
panel.show_in_folder_requested.connect(lambda p: recebidos.append(("show", p)))
panel.action_requested.connect(lambda a: recebidos.append(("acao", a)))

for botao in panel.findChildren(QPushButton):
    botao.click()

assert ("open", "C:/x/doc.docx") in recebidos
assert ("show", "C:/x/doc.docx") in recebidos
assert ("acao", "exportar") in recebidos
assert ("acao", "revisar_grafias") in recebidos
assert ("acao", "abrir_resultados") in recebidos
print("PASS: sinais open/show/action")

# ---------------------------------------------------------------- reconstrucao
panel.set_sections("Outra", [DocEntry("x", "Documento X", estado="ausente",
                                      detalhe="ainda não gerado")], [])
textos2 = [w.text() for w in panel.findChildren(QLabel) if w.text()]
assert any("Outra" in t for t in textos2)
print("PASS: set_sections reconstroi sem residuos")

# ------------------------------------------------- banner de sucesso (R4)
# Vive no layout RAIZ (fora do scroll): set_sections nao pode mata-lo.
docs_abertos: list[str] = []
panel.open_document_requested.connect(docs_abertos.append)

panel.show_success("Resumo com temas pronto.", caminho="C:/x/resumo.md",
                   extras=[("Revisar grafias…", "revisar_grafias")])
assert panel._sucesso.isVisibleTo(panel)
assert "Resumo com temas pronto." in panel._sucesso_label.text()
panel.set_sections("Maria", entradas, projeto)   # refresh NAO derruba o banner
assert panel._sucesso.isVisibleTo(panel), "set_sections matou o banner"

for botao in panel._sucesso.findChildren(QPushButton):
    if botao.text() == "Abrir":
        botao.click()
assert docs_abertos == ["C:/x/resumo.md"], docs_abertos

recebidos.clear()
for botao in panel._sucesso.findChildren(QPushButton):
    if botao.text() == "Revisar grafias…":
        botao.click()
assert ("acao", "revisar_grafias") in recebidos

# show_success de novo TROCA os botoes (nao acumula) e clear esconde
panel.show_success("Glossário pronto — 12 nomes.", caminho="C:/x/glossario.md")
rotulos = [b.text() for b in panel._sucesso.findChildren(QPushButton)]
assert rotulos.count("Abrir") == 1 and "Revisar grafias…" not in rotulos, rotulos
panel.clear_success()
assert not panel._sucesso.isVisibleTo(panel)
print("PASS: banner de sucesso (sobrevive ao refresh; sinais; clear)")

print("PASS: toy_ui_docs_panel")
