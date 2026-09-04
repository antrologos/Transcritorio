"""Toy: janela Perguntar v3 (um botao, dois estagios) — fluxo com AI falsa.

Estagio 1 renderiza as secoes ("Respondem"/"Relacionados") com o rodape
honesto; estagio 2 so quando algo responde e o modelo de analise esta
pronto (cancelavel); pergunta sobre o conjunto vai pela faixa de rota
(visao geral) com o escape "responder pelos trechos mesmo assim"; sem
resumos, oferece "Resumir as N entrevistas agora". Tudo com ask
monkeypatchado — nenhum modelo carregado.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp_home = tempfile.mkdtemp(prefix="toy_explore_dialog_")
os.environ["TRANSCRITORIO_HOME"] = str(Path(_tmp_home) / "appdata")
os.environ["TRANSCRITORIO_MODEL_CACHE"] = str(Path(_tmp_home) / "models")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from transcribe_pipeline.review_studio_qt import ExploreDialog, ReviewStudioWindow
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import app_service, ask, search  # noqa: E402
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
for iid in ("E1", "E2"):
    save_review_transcript(ctx.paths, iid, {"transcript": {"turns": [
        {"id": "t1", "start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "O pagamento atrasou muito."},
        {"id": "t2", "start": 5.0, "end": 9.0, "speaker": "SPEAKER_01", "text": "Sim, atrasou."}]}})

win = ReviewStudioWindow(project_root=root)
win.refresh_interviews()
app.processEvents()
search.encoder_cached = lambda *a, **k: True  # type: ignore[assignment]
search.reranker_cached = lambda: True  # type: ignore[assignment]
dlg = ExploreDialog(win)
dlg.show()
app.processEvents()

# --- abertura: um botao, sem "Encontrar trechos", linha de estado ---
botoes = [b.text() for b in dlg.findChildren(type(dlg.ask_button))]
assert not any("Encontrar trechos" in b for b in botoes), botoes
assert dlg.ask_button.text() == "✨ Perguntar" and dlg.ask_button.isEnabled()
assert dlg.state_label.text().startswith("Nesta máquina:")
assert dlg.max_results_spin.value() == 20
print("PASS: um so botao, N configuravel, estado na abertura")


def _wait_worker(timeout: float = 10.0) -> None:
    t0 = time.time()
    while dlg._worker is not None and dlg._worker.isRunning() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)


HIT = {"interview_id": "E1", "t_from": 0, "t_to": 1, "start": 0.0, "end": 9.0, "label": "",
       "text": "SPEAKER_00: O pagamento atrasou muito. SPEAKER_01: Sim, atrasou.", "similarity": 0.86, "score": 0.4, "z": 3.1}
HIT2 = dict(HIT, interview_id="E2", score=-3.0, z=2.2)
RESULT = {"hits": [HIT, HIT2], "sections": [
    {"key": "responde", "label": "Respondem", "weak": False, "hits": [HIT]},
    {"key": "relacionado", "label": "Relacionados", "weak": False, "hits": [HIT2]}],
    "reranked": True, "considered": 12, "max_results": 20, "kind": "trechos", "worth_answer": True}
RESULT["trechos"] = ask.build_trechos(RESULT["hits"])

chamadas: list[str] = []


def fake_retrieve(paths, ids, question, max_results=None, progress_callback=None, should_cancel=None):
    chamadas.append(f"retrieve:{question}:{max_results}")
    r = dict(RESULT)
    r["kind"] = ask.question_kind(question)
    return r


def fake_answer(paths, question, trechos, progress_callback=None, should_cancel=None):
    chamadas.append("answer")
    return {"resposta": "O pagamento atrasou [1]. Outra fala [2]."}


ask.retrieve = fake_retrieve  # type: ignore[assignment]
ask.answer_from_trechos = fake_answer  # type: ignore[assignment]
win._capability_state = lambda key: ("pronta", "", 0.0)  # type: ignore[method-assign]

# --- estagio 1 + 2: secoes, rodape, resposta com [n] clicaveis ---
dlg.query_input.setText("problemas com o pagamento")
dlg.max_results_spin.setValue(30)
dlg.run_question()
_wait_worker()  # retrieve
_wait_worker()  # answer
assert chamadas[0] == "retrieve:problemas com o pagamento:30" and "answer" in chamadas, chamadas
textos = [dlg.results.item(i).text() for i in range(dlg.results.count())]
assert textos[0] == "Respondem (1)" and textos[1].startswith("[1]  ") and textos[2] == "Relacionados (1)" and textos[3].startswith("[2]  "), textos
assert "2 trechos tratam disso (de até 20)" in dlg.status_label.text() and "restante ficou fora" in dlg.status_label.text()
assert dlg.answer_view.isVisibleTo(dlg) and 'href="trecho:1"' in dlg.answer_view.toHtml()
assert not dlg.cancel_answer_button.isVisibleTo(dlg)
# clicar em [2] seleciona o trecho 2 na lista
from PySide6.QtCore import QUrl  # noqa: E402
dlg._on_answer_anchor(QUrl("trecho:2"))
assert dlg.results.currentRow() == 3
print("PASS: dois estagios — secoes, rodape, resposta com citacoes clicaveis")

# --- nada responde: sem AI, rodape explica ---
chamadas.clear()
fraco = dict(RESULT, worth_answer=False, sections=[{"key": "relacionado", "label": "Relacionados", "weak": True, "hits": [HIT2]}], hits=[HIT2])
fraco["trechos"] = ask.build_trechos([HIT2])
ask.retrieve = lambda *a, **k: dict(fraco, kind="trechos")  # type: ignore[assignment]
dlg.query_input.setText("quanto ganhava o supervisor?")
dlg.run_question()
_wait_worker()
assert "answer" not in chamadas
assert dlg.results.item(0).text().startswith("Nada realmente próximo")
assert "não escreveu resposta" in dlg.status_label.text() and not dlg.answer_view.isVisibleTo(dlg)
print("PASS: nada responde — a AI nao e chamada e o rodape explica")

# --- sem GPU: trechos sim, resposta nao ---
chamadas.clear()
ask.retrieve = fake_retrieve  # type: ignore[assignment]
win._capability_state = lambda key: ("incompativel", "precisa de uma placa NVIDIA", 0.0) if key == "resumo_perguntar" else ("pronta", "", 0.0)  # type: ignore[method-assign]
dlg.query_input.setText("problemas com o pagamento")
dlg.run_question()
_wait_worker()
assert "answer" not in chamadas and "NVIDIA" in dlg.status_label.text()
assert dlg.results.count() == 4
# O rotulo do botao nao pode prometer o que esta maquina nao entrega
# (relato de campo 2026-09-04: "nao entrega o que promete").
dlg._announce_readiness()
assert dlg.ask_button.text() == "✨ Buscar trechos", dlg.ask_button.text()
assert "placa NVIDIA" in dlg.ask_button.toolTip()
assert "resposta escrita" in dlg.state_label.text() and "NVIDIA" in dlg.state_label.text()
# e volta a prometer quando a maquina entrega
win._capability_state = lambda key: ("pronta", "", 8.7)  # type: ignore[method-assign]
dlg._announce_readiness()
assert dlg.ask_button.text() == "✨ Perguntar", dlg.ask_button.text()
win._capability_state = lambda key: ("incompativel", "precisa de uma placa NVIDIA", 0.0) if key == "resumo_perguntar" else ("pronta", "", 0.0)  # type: ignore[method-assign]
print("PASS: sem GPU os trechos aparecem, a resposta e explicada e o botao nao promete")

# --- pergunta sobre o conjunto: sem resumos -> faixa + 'Resumir as N entrevistas agora' ---
win._capability_state = lambda key: ("pronta", "", 0.0)  # type: ignore[method-assign]
ask.resumos_for_scope = lambda paths, ids, titles=None: []  # type: ignore[assignment]
resumir: list[list[str]] = []
win.run_summarize_job = lambda *a, ids=None: resumir.append(list(ids or []))  # type: ignore[method-assign]
chamadas.clear()
dlg.query_input.setText("do que falam as entrevistas?")
dlg.run_question()
_wait_worker()
assert dlg.route_banner.isVisibleTo(dlg) and "nenhuma das 2 tem resumo" in dlg.route_label.text(), dlg.route_label.text()
assert dlg.route_button.isVisibleTo(dlg) and dlg.route_button.text() == "Resumir as 2 entrevistas agora"
dlg.route_button.click()
assert resumir == [["E1", "E2"]], resumir
assert "answer" not in chamadas
print("PASS: pergunta global sem resumos oferece Resumir")

# --- pergunta sobre o conjunto: com resumos -> visao geral, com escape ---
ask.resumos_for_scope = lambda paths, ids, titles=None: [{"interview_id": "E1", "titulo": "E1", "resumo": "r", "indice": "i"}]  # type: ignore[assignment]
ask.run_visao_geral = lambda paths, ids, q, progress_callback=None, should_cancel=None, titles=None: {  # type: ignore[assignment]
    "resposta": "Falam de pagamento [E1].", "citadas": ["E1"], "com_resumo": ["E1"], "sem_resumo": ["E2"]}
dlg.query_input.setText("quais são os principais temas?")
dlg.run_question()
_wait_worker()
_wait_worker()
assert dlg.route_banner.isVisibleTo(dlg) and "1 das 2 têm resumo" in dlg.route_label.text(), dlg.route_label.text()
assert dlg.route_button.text() == "Responder pelos trechos mesmo assim"
assert 'href="entrevista:E1"' in dlg.answer_view.toHtml()
assert dlg.results.item(0).text() == "Entrevistas citadas (1)" and dlg.results.item(1).text().startswith("[E1]")
assert "1 sem resumo ficaram de fora" in dlg.status_label.text()
# escape: responder pelos trechos
chamadas.clear()
dlg.route_button.click()
_wait_worker()
assert "answer" in chamadas and not dlg.route_banner.isVisibleTo(dlg)
print("PASS: pergunta global com resumos -> visao geral citando entrevistas, com escape")

# --- cancelar a resposta: os trechos ficam ---
def slow_answer(paths, question, trechos, progress_callback=None, should_cancel=None):
    t0 = time.time()
    while time.time() - t0 < 5:
        if should_cancel():
            return {"erro": "cancelado"}
        time.sleep(0.02)
    return {"resposta": "tarde [1]"}


ask.answer_from_trechos = slow_answer  # type: ignore[assignment]
ask.retrieve = fake_retrieve  # type: ignore[assignment]
dlg.query_input.setText("problemas com o pagamento")
dlg.run_question()
_wait_worker()  # retrieve termina; a resposta comeca
t0 = time.time()
while not dlg.cancel_answer_button.isVisibleTo(dlg) and time.time() - t0 < 5:
    app.processEvents()
    time.sleep(0.02)
assert dlg.cancel_answer_button.isVisibleTo(dlg) and dlg.cancel_answer_button.text().startswith("Cancelar a resposta")
dlg.cancel_answer_button.click()
_wait_worker()
assert "cancelada" in dlg.status_label.text() and dlg.results.count() == 4, dlg.status_label.text()
assert not dlg.cancel_answer_button.isVisibleTo(dlg)
print("PASS: cancelar a resposta mantem os trechos")

print("PASS: toy_explore_dialog")
