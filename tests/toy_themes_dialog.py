"""Toy: janela Temas das entrevistas (Parte B) — fluxo com AI falsa, offscreen.

Descoberta (themes.discover monkeypatchado) renderiza temas + "sem tema";
nomes pela AI rodam em segundo plano e substituem os termos (sem
sobrescrever o nome dado pelo usuario); renomear/juntar; aplicar codigo aos
marcados e codigos por trecho (acrescentar/tirar) gravam em
08_codificacao; exportar .qualilab respeita a invariante quote ==
content[span]; maquina sem placa: botao de nomes some e o motivo aparece;
guia verbal (sem "IA", sem "..."). Nenhum modelo carregado.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_themes_dialog_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    from transcribe_pipeline import review_studio_qt as rs
    from transcribe_pipeline.review_studio_qt import ReviewStudioWindow, ThemesDialog
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_service, coding, search, themes  # noqa: E402
from transcribe_pipeline.manifest import write_manifest  # noqa: E402
from transcribe_pipeline.review_store import save_review_transcript  # noqa: E402

app = QApplication.instance() or QApplication([])
root = Path(_tmp_home) / "proj.transcricao"
ctx = app_service.create_project(root, "lote")
write_manifest([
    {"interview_id": iid, "selected": "true", "source_path": f"midia/{iid}.m4a",
     "source_ext": ".m4a", "wav_path": f"Transcricoes/01_audio_wav16k_mono/{iid}.wav",
     "status": "pending", "duration_sec": "60"}
    for iid in ("E1", "E2")
], ctx.paths.manifest_dir / "manifest.csv")
TURNS = [
    {"id": "t1", "start": 0.0, "end": 5.0, "speaker": "SPEAKER_00",
     "human_label": "Entrevistador", "text": "O pagamento atrasou muito?"},
    {"id": "t2", "start": 5.0, "end": 9.0, "speaker": "SPEAKER_01",
     "human_label": "Entrevistado", "text": "Sim, atrasou três meses."},
    {"id": "t3", "start": 9.0, "end": 14.0, "speaker": "SPEAKER_00",
     "human_label": "Entrevistador", "text": "E o treinamento durou cinco dias?"},
]
for iid in ("E1", "E2"):
    save_review_transcript(ctx.paths, iid, {"transcript": {"turns": TURNS}})
# A escolha de "Quem entra" é perguntada na PRIMEIRA descoberta; os fluxos
# gerais abaixo rodam com ela já respondida (todos os falantes).
ctx.project["coding_speakers"] = None
ctx.project["coding_speakers_asked"] = True
app_service.save_project_metadata(ctx)
(ctx.paths.output_root / "00_config").mkdir(parents=True, exist_ok=True)
from transcribe_pipeline import research_context  # noqa: E402
research_context.context_path(ctx.paths).write_text(
    "# Contexto\n\n## Codebook inicial\n\n- recepcao: como receberam\n\n## Nomes conhecidos\n", encoding="utf-8")

win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()
search.encoder_cached = lambda *a, **k: True  # type: ignore[assignment]
estado_llm = ["pronta"]
win._capability_state = lambda key: (estado_llm[0] if key == "resumo_perguntar" else "pronta", "", 8.7)  # type: ignore[method-assign]


def P(iid, t_from, t_to, start, text, sim=0.9):
    return {"interview_id": iid, "t_from": t_from, "t_to": t_to, "start": start, "end": start + 5.0,
            "text": text, "similarity": sim}


PAYLOAD = {
    "themes": [
        {"id": "tema_001", "name": "pagamento, atrasou, meses", "terms": ["pagamento", "atrasou", "meses"],
         "description": "O pagamento atrasou muito.", "n_passages": 3, "n_interviews": 2, "name_source": "termos",
         "passages": [P("E1", 0, 1, 0.0, "Entrevistador: O pagamento atrasou muito. Entrevistado: Sim, atrasou três meses."),
                      P("E2", 0, 1, 0.0, "Entrevistador: O pagamento atrasou muito.", 0.88),
                      P("E2", 1, 1, 5.0, "Entrevistado: Sim, atrasou três meses.", 0.8)]},
        {"id": "tema_002", "name": "treinamento, durou, dias", "terms": ["treinamento", "durou", "dias"],
         "description": "E o treinamento durou cinco dias.", "n_passages": 2, "n_interviews": 2, "name_source": "termos",
         "passages": [P("E1", 2, 2, 9.0, "Entrevistador: E o treinamento durou cinco dias."),
                      P("E2", 2, 2, 9.0, "Entrevistador: E o treinamento durou cinco dias.", 0.85)]},
    ],
    "outros": [{"interview_id": "E1", "t_from": 1, "t_to": 1, "start": 5.0, "text": "Entrevistado: Sim, atrasou três meses."}],
    "interview_ids": ["E1", "E2"], "n_passages": 6, "n_themes_requested": 6, "encoder": "x", "created_at": "2026-09-03T10:00",
}
chamadas: list[str] = []


def fake_discover(paths, ids, n_themes=None, progress_callback=None, should_cancel=None, speakers=None):
    chamadas.append(f"discover:{','.join(ids)}:{n_themes}:{speakers}")
    if progress_callback:
        progress_callback({"event": "themes_progress", "progress": 60, "message": "Agrupando…"})
    return json.loads(json.dumps(PAYLOAD))


import threading as _threading

# Portão: com ele fechado, a nomeação fica presa na thread e o teste pode
# inspecionar a janela COM a AI rodando (sem depender de corrida de threads).
portao_nomes = _threading.Event()
portao_nomes.set()


def fake_names(paths, payload, progress_callback=None, should_cancel=None):
    chamadas.append("nomear")
    portao_nomes.wait(10.0)
    return {"ok": True, "nomeados": 2, "nomes": [
        {"id": "tema_001", "nome": "Pagamento e atrasos", "descricao": "Atrasos na remuneração."},
        {"id": "tema_002", "nome": "Treinamento", "descricao": "Duração do treinamento."}]}


themes.discover = fake_discover  # type: ignore[assignment]
themes.name_with_llm = fake_names  # type: ignore[assignment]

dlg = ThemesDialog(win)
dlg.show()
app.processEvents()


def _wait_worker_of(alvo, timeout: float = 10.0) -> None:
    t0 = time.time()
    while alvo._worker is not None and alvo._worker.isRunning() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)


def _wait_worker(timeout: float = 10.0) -> None:
    _wait_worker_of(dlg, timeout)


def _pump_until(pred, timeout: float = 10.0) -> None:
    t0 = time.time()
    while not pred() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert pred(), "condição não atingida a tempo"


# --- abertura ---
assert dlg.state_label.text().startswith("Nesta máquina:") and "nomes pela AI — pronta" in dlg.state_label.text()
assert dlg.n_themes_spin.value() == 0 and dlg.n_themes_spin.text() == "automático"
assert dlg.themes_list.count() == 0 and not dlg.names_button.isVisible()
# primeira vez: a janela diz qual e o primeiro passo (listas vazias sem orientacao nao servem)
assert "Comece por «Descobrir os temas»" in dlg.status_label.text(), dlg.status_label.text()
# e os botoes de codificacao explicam o que fazer, em vez de pedir o impossivel
dlg._apply_code_to_checked()
assert "Descubra os temas primeiro" in dlg.status_label.text(), dlg.status_label.text()
dlg._passage_codes()
assert "Descubra os temas primeiro" in dlg.status_label.text(), dlg.status_label.text()
dlg._rename()
assert "Descubra os temas primeiro" in dlg.status_label.text(), dlg.status_label.text()
print("PASS: abertura")

# --- descoberta + nomes em segundo plano ---
dlg.n_themes_spin.setValue(4)
portao_nomes.clear()      # a AI fica presa: dá para inspecionar a janela com ela rodando
dlg.run_discover()
_pump_until(lambda: dlg.themes_list.count() > 0)
assert chamadas[0] == "discover:E1,E2:4:None", chamadas
assert dlg.themes_list.count() == 3, dlg.themes_list.count()      # 2 temas + sem tema
assert "Sem tema definido" in dlg.themes_list.item(2).text()
assert "2 tema(s) em 6 trechos de 2 entrevista(s); 1 trecho(s) sem tema definido" in dlg.status_label.text()
# nomes pela AI comecaram sozinhos APOS A DESCOBERTA (modelo pronto) e a janela seguiu usavel
_pump_until(lambda: "nomear" in chamadas)
assert dlg.cancel_button.isVisible() and "Cancelar os nomes" in dlg.cancel_button.text()
# usuario renomeia o tema 2 ENQUANTO a AI nomeia: o nome dele prevalece
rs.QInputDialog.getText = staticmethod(lambda *a, **k: ("Formação dos recenseadores", True))  # type: ignore[assignment]
dlg.themes_list.setCurrentRow(1)
dlg._rename()
portao_nomes.set()
_wait_worker()            # nomes
assert "nomear" in chamadas
nomes = [dlg.themes_list.item(i).text() for i in range(dlg.themes_list.count())]
assert nomes[0].startswith("Pagamento e atrasos") and nomes[1].startswith("Formação dos recenseadores"), nomes
assert "A AI nomeou 1 tema(s)." in dlg.status_label.text(), dlg.status_label.text()
salvo = themes.load_themes(ctx.paths)
assert salvo["themes"][0]["name"] == "Pagamento e atrasos" and salvo["themes"][1]["name_source"] == "usuario"
print("PASS: descoberta, nomes em segundo plano, renomear prevalece")

# --- selecao mostra os trechos (todos marcados); duplo clique abre a entrevista ---
dlg.themes_list.setCurrentRow(0)
app.processEvents()
assert dlg.passages.count() == 3 and dlg.theme_title.text() == "Pagamento e atrasos"
assert "Atrasos na remuneração." in dlg.theme_desc.text() and "Termos característicos: pagamento" in dlg.theme_desc.text()
assert all(dlg.passages.item(i).checkState().name == "Checked" for i in range(3))
abertos: list[tuple[str, float]] = []
win.open_search_hit = lambda iid, start: abertos.append((iid, start))  # type: ignore[method-assign]
dlg._open_hit(dlg.passages.item(1))
assert abertos == [("E2", 0.0)]
print("PASS: trechos do tema")

# --- aplicar codigo aos marcados (sementes do contexto no codebook) ---
assert [c["name"] for c in dlg._codes] == ["recepcao"], "semente do contexto"
dlg.passages.item(2).setCheckState(rs.Qt.CheckState.Unchecked)
rs.QInputDialog.getItem = staticmethod(lambda *a, **k: ("Pagamento e atrasos", True))  # type: ignore[assignment]
dlg._apply_code_to_checked()
assert "aplicado a 2 trecho(s)" in dlg.status_label.text(), dlg.status_label.text()
codings = coding.load_codings(ctx.paths)
assert len(codings) == 2 and {c["origem"] for c in codings} == {"tema"}
codebook = coding.load_codebook(ctx.paths)
assert [c["name"] for c in codebook] == ["recepcao", "Pagamento e atrasos"]
assert codebook[1]["description"] == "Atrasos na remuneração."
assert dlg.passages.item(0).text().startswith("● Pagamento e atrasos")
assert not dlg.passages.item(2).text().startswith("●")
# de novo: nada duplica
dlg._apply_code_to_checked()
assert "(2 já o tinham)" in dlg.status_label.text() and len(coding.load_codings(ctx.paths)) == 2
print("PASS: aplicar codigo aos marcados")

# --- codigos por trecho: acrescentar um segundo e tirar ---
dlg.passages.setCurrentRow(0)
rs.QInputDialog.getItem = staticmethod(lambda *a, **k: ("recepcao", True))  # type: ignore[assignment]
dlg._passage_codes()
assert "acrescentado" in dlg.status_label.text()
assert dlg.passages.item(0).text().startswith("● Pagamento e atrasos; recepcao"), dlg.passages.item(0).text()
assert len(coding.load_codings(ctx.paths)) == 3
dlg.passages.setCurrentRow(0)
rs.QInputDialog.getItem = staticmethod(lambda *a, **k: ("✓ recepcao", True))  # type: ignore[assignment]
dlg._passage_codes()
assert "retirado" in dlg.status_label.text() and len(coding.load_codings(ctx.paths)) == 2
assert dlg.passages.item(0).text().startswith("● Pagamento e atrasos\n")
print("PASS: codigos por trecho")

# --- «Sem tema definido» nao e um tema: o motivo tem de dizer isso ---
dlg.themes_list.setCurrentRow(dlg.themes_list.count() - 1)
assert "Sem tema definido" in dlg.themes_list.currentItem().text()
dlg._rename()
assert "«Sem tema definido» não é um tema" in dlg.status_label.text(), dlg.status_label.text()
dlg._merge()
assert "«Sem tema definido» não é um tema" in dlg.status_label.text(), dlg.status_label.text()
print("PASS: «Sem tema definido»")

# --- juntar temas ---
dlg.themes_list.setCurrentRow(0)
rs.QInputDialog.getItem = staticmethod(lambda *a, **k: ("Formação dos recenseadores", True))  # type: ignore[assignment]
dlg._merge()
assert dlg.themes_list.count() == 2 and "5 trechos" in dlg.themes_list.item(0).text(), [dlg.themes_list.item(i).text() for i in range(dlg.themes_list.count())]
assert len(themes.load_themes(ctx.paths)["themes"]) == 1
print("PASS: juntar")

# --- exportar .qualilab e CSV ---
out = Path(_tmp_home) / "export" / "proj.qualilab"
rs.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))  # type: ignore[assignment]
dlg._export()
assert "Projeto do QualiLab gravado" in dlg.status_label.text(), dlg.status_label.text()
q = json.loads(out.read_text(encoding="utf-8"))
assert len(q["documents"]) == 2 and len(q["codings"]) == 2 and len(q["codes"]) == 2
docs = {d["id"]: d["content"] for d in q["documents"]}
for cd in q["codings"]:
    assert cd["quote"] == docs[cd["document_id"]][cd["span_start"]:cd["span_end"]]
csv_out = Path(_tmp_home) / "export" / "proj.csv"
rs.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(csv_out), ""))  # type: ignore[assignment]
dlg._export()
assert "Planilha gravada" in dlg.status_label.text() and csv_out.exists()
# o combo "Onde" descreve a DESCOBERTA: estreitar o escopo nao pode sumir com codificacao
idx_aberta = dlg.scope_combo.findData("open")
if idx_aberta >= 0:
    dlg.scope_combo.setCurrentIndex(idx_aberta)
    app.processEvents()
assert sorted(dlg._export_ids()) == ["E1", "E2"], dlg._export_ids()
rs.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))  # type: ignore[assignment]
dlg._export()
q2 = json.loads(out.read_text(encoding="utf-8"))
assert len(q2["documents"]) == 2 and len(q2["codings"]) == 2, "codificacao fora do escopo sumiu"
idx_todas = dlg.scope_combo.findData("all")
if idx_todas >= 0:
    dlg.scope_combo.setCurrentIndex(idx_todas)
    app.processEvents()
print("PASS: exportar (escopo não descarta codificação)")

# --- maquina sem placa: sem botao de nomes, motivo no status ---
estado_llm[0] = "incompativel"
dlg2 = ThemesDialog(win)
dlg2.show()
app.processEvents()
assert "não roda neste computador" in dlg2.state_label.text()
assert dlg2.themes_list.count() >= 1, "temas gravados reaparecem na abertura"
assert not dlg2.names_button.isVisible()
print("PASS: sem placa")

# --- modelo instalavel: botao oferece o download, nada roda sozinho ---
estado_llm[0] = "instalavel"
chamadas.clear()
themes_path = themes.themes_path(ctx.paths)
themes_path.unlink()
dlg3 = ThemesDialog(win)
dlg3.show()
app.processEvents()
dlg3.run_discover()
_wait_worker()
assert chamadas == ["discover:E1,E2:None:None"], chamadas
assert dlg3.names_button.isVisible() and "baixa ~8,7 GB" in dlg3.names_button.text(), dlg3.names_button.text()
print("PASS: instalavel oferece o download")

# --- nomes pela AI: marcações do usuário preservadas, e sempre há como tentar de novo ---
estado_llm[0] = "pronta"           # modelo instalado: a nomeação roda de verdade
themes.name_with_llm = fake_names  # type: ignore[assignment]
chamadas.clear()
dlg4 = ThemesDialog(win)
dlg4.show()
app.processEvents()
portao_nomes.clear()               # segura a AI enquanto o usuário mexe nas caixas
dlg4.run_discover()
_pump_until(lambda: dlg4.themes_list.count() > 0)
dlg4.themes_list.setCurrentRow(0)
app.processEvents()
assert dlg4.passages.count() == 3
dlg4.passages.item(1).setCheckState(rs.Qt.CheckState.Unchecked)
dlg4.passages.item(2).setCheckState(rs.Qt.CheckState.Unchecked)
_pump_until(lambda: "nomear" in chamadas)
portao_nomes.set()
_wait_worker_of(dlg4)              # nomes (começaram sozinhos após a descoberta)
assert dlg4.themes_list.item(0).text().startswith("Pagamento e atrasos"), "a AI nomeou mesmo"
marcas = [dlg4.passages.item(i).checkState().name for i in range(dlg4.passages.count())]
assert marcas == ["Checked", "Unchecked", "Unchecked"], marcas
assert len(dlg4._checked_passages()) == 1, "a nomeação remarcaria trechos que o usuário tirou"
print("PASS: nomes em segundo plano preservam as marcações")

# AI que não nomeia nada (JSON malformado, ids inventados): mensagem acionável
# e botão de volta — antes a janela ficava sem saída até reiniciar o app.
themes.name_with_llm = lambda *a, **k: {"ok": True, "nomeados": 0, "nomes": []}  # type: ignore[assignment]
dlg4.run_discover()                # descoberta nova: os temas voltam aos termos
_wait_worker_of(dlg4)              # descoberta
_wait_worker_of(dlg4)              # nomes (começaram sozinhos e não nomearam nada)
assert "não conseguiu nomear os temas desta vez" in dlg4.status_label.text(), dlg4.status_label.text()
assert dlg4._needs_names() and dlg4.names_button.isVisible(), "sem botão, o usuário ficaria sem saída"
themes.name_with_llm = fake_names  # type: ignore[assignment]
print("PASS: nomeação sem resultado continua acionável")

# --- fechar durante a descoberta devolve os botões (a janela é cacheada) ---
import threading as _threading
segura = _threading.Event()


def slow_discover(paths, ids, n_themes=None, progress_callback=None, should_cancel=None, speakers=None):
    segura.wait(5.0)
    return json.loads(json.dumps(PAYLOAD))


themes.discover = slow_discover  # type: ignore[assignment]
dlg5 = ThemesDialog(win)
dlg5.show()
app.processEvents()
dlg5.run_discover()
app.processEvents()
assert not dlg5.discover_button.isEnabled() and dlg5.cancel_button.isVisible()
dlg5.close()
segura.set()
_wait_worker_of(dlg5)
assert dlg5.discover_button.isEnabled(), "«Descobrir os temas» ficaria cinza para sempre"
assert not dlg5.cancel_button.isVisible()
themes.discover = fake_discover  # type: ignore[assignment]
print("PASS: fechar durante a descoberta")

# --- troca de projeto esquece temas e codigos do projeto anterior ---
outro = Path(_tmp_home) / "outro.transcricao"
ctx2 = app_service.create_project(outro, "outro")
write_manifest([], ctx2.paths.manifest_dir / "manifest.csv")
win._themes_dialog = dlg
antes = dlg.themes_list.count()
assert antes >= 1 and dlg._codes and dlg._codings
win.switch_project_context(ctx2)
app.processEvents()
assert dlg._payload is None and dlg._codes == [] and dlg._codings == [], "estado do projeto antigo sobreviveu"
assert dlg.themes_list.count() == 0 and dlg.passages.count() == 0
# e nada do projeto antigo foi gravado no novo
assert themes.load_themes(ctx2.paths) is None and coding.load_codings(ctx2.paths) == []
win.switch_project_context(ctx)
app.processEvents()
print("PASS: troca de projeto")

# --- "Quem entra": a primeira descoberta pergunta, e o filtro vale ---
win.context.project["coding_speakers"] = None      # é o contexto DA JANELA que vale
win.context.project["coding_speakers_asked"] = False
app_service.save_project_metadata(win.context)
themes.themes_path(ctx.paths).unlink(missing_ok=True)
chamadas.clear()
perguntou = {"n": 0}
_exec_real = rs.QDialog.exec


def fake_exec(self):
    """Responde a janela «Quem entra» sem interação: desmarca o entrevistador."""
    lista = self.findChild(rs.QListWidget)
    if lista is None:
        return rs.QDialog.DialogCode.Rejected
    perguntou["n"] += 1
    for r in range(lista.count()):
        item = lista.item(r)
        if str(item.data(rs.Qt.ItemDataRole.UserRole)) == "Entrevistador":
            item.setCheckState(rs.Qt.CheckState.Unchecked)
    for botao in self.findChildren(QPushButton):
        if botao.text().startswith("Usar estes"):
            botao.click()
            break
    return rs.QDialog.DialogCode.Accepted


rs.QDialog.exec = fake_exec  # type: ignore[assignment]
dlg6 = ThemesDialog(win)
dlg6.show()
app.processEvents()
assert dlg6.speakers_label.text() == "todos os falantes"
dlg6.run_discover()
assert perguntou["n"] == 1, "a primeira descoberta tem de perguntar quem entra"
_wait_worker_of(dlg6)
assert chamadas[0] == "discover:E1,E2:None:['Entrevistado']", chamadas
assert dlg6._speakers == ["Entrevistado"], dlg6._speakers
assert "fora: Entrevistador" in dlg6.speakers_label.text(), dlg6.speakers_label.text()
# gravado no projeto e relido
assert win.context.project["coding_speakers"] == ["Entrevistado"]
assert win.context.project["coding_speakers_asked"] is True
# a segunda descoberta NAO pergunta de novo
dlg6.run_discover()
assert perguntou["n"] == 1, "só pergunta uma vez por projeto"
_wait_worker_of(dlg6)
# e o codigo passa a pintar so o turno do entrevistado
dlg6.themes_list.setCurrentRow(0)
app.processEvents()
antes = len(coding.load_codings(ctx.paths))
rs.QInputDialog.getItem = staticmethod(lambda *a, **k: ("Só a resposta", True))  # type: ignore[assignment]
dlg6._apply_code_to_checked()
novas = [c for c in coding.load_codings(ctx.paths) if c["quote"].startswith("Entrevistado:")]
assert len(coding.load_codings(ctx.paths)) > antes and novas, "nada foi codificado"
assert all(c["t_from"] == c["t_to"] == 1 for c in novas), [(c["t_from"], c["t_to"]) for c in novas]
assert "Só a fala de Entrevistado" in dlg6.status_label.text(), dlg6.status_label.text()
rs.QDialog.exec = _exec_real  # type: ignore[assignment]
print("PASS: «Quem entra» — pergunta uma vez, filtra e o código pinta só a fala escolhida")

# --- guia verbal nos textos da janela ---
textos = [w.text() for w in dlg.findChildren(QLabel)] + [w.text() for w in dlg.findChildren(QPushButton)]
textos += [w.toolTip() for w in dlg.findChildren(QPushButton)] + [dlg.windowTitle()]
for t in textos:
    assert not re.search(r"\bIA\b", t), t
    assert "..." not in t, t
    assert not re.search(r"\b(QC|manifesto|canonical|merge|fundir)\b", t, re.I), t
print("PASS: guia verbal")

print("PASS: toy_themes_dialog")
