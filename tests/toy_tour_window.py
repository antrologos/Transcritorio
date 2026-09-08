"""Toy: a volta guiada aponta lugares REAIS da janela — 2026-09-07.

Cada passo nomeia um atributo da janela principal. Se o atributo sumir ou
mudar de nome (a janela e reformada com frequencia), o halo cairia no
nada e o passo mentiria. Este toy fixa:

  1. todo alvo (e toda reserva) e um QWidget da ReviewStudioWindow real;
  2. o halo cobre a geometria do alvo, e troca de aba quando o passo pede;
  3. com o Estudio fechado, os passos do Estudio apontam a reserva e
     mostram a nota;
  4. Ajuda → Volta guiada existe no catalogo; a janela nao e modal;
  5. a faixa "Primeira vez aqui?" aparece uma vez, grava a resposta e
     cede a quem pede acao — e a de novidades cede a ela.

Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

try:
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QApplication, QWidget
except ImportError:  # pragma: no cover
    print("SKIP: toy_tour_window (PySide6 ausente)")
    raise SystemExit(0)

app = QApplication.instance() or QApplication([])
# Os prints citam "→"; o console cp1252 do Windows nao o codifica.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import (  # noqa: E402
    DEFAULT_CONFIG, ensure_directories, make_paths, write_config)

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_tour"])

from transcribe_pipeline import comandos, novidades, runtime, volta_guiada  # noqa: E402
from transcribe_pipeline.review_studio_qt import ReviewStudioWindow  # noqa: E402

_settings = runtime.app_data_dir() / "app_settings.json"
_settings.parent.mkdir(parents=True, exist_ok=True)
_settings.write_text("{}", encoding="utf-8")

win = ReviewStudioWindow(project_root=tmp)
win.resize(1400, 900)
win.show()
app.processEvents()

# ------------------------------------------------- 1. alvos sao widgets reais
for p in volta_guiada.PASSOS:
    alvo = win.menuBar() if p.alvo == "menuBar" else getattr(win, p.alvo, None)
    assert isinstance(alvo, QWidget), f"passo {p.titulo!r}: alvo {p.alvo!r} nao e um widget da janela"
    if p.precisa_entrevista:
        assert isinstance(getattr(win, p.alvo_reserva, None), QWidget), \
            f"passo {p.titulo!r}: reserva {p.alvo_reserva!r} nao e um widget"
print(f"OK: os {volta_guiada.total()} alvos (e as reservas) sao widgets da janela real")

# ------------------------------------------------- 4. menu e nao-modal
catalogo = comandos.percorrer_menu(
    win.menuBar(), fmt_portavel=QKeySequence.SequenceFormat.PortableText,
    fmt_nativo=QKeySequence.SequenceFormat.NativeText)
assert ("Ajuda", "Volta guiada") in {(c.caminho, c.rotulo) for c in catalogo}, \
    "Ajuda → Volta guiada tem de existir como item de menu"
win.open_tour()
painel, halo = win._tour_panel, win._tour_halo
assert not painel.isModal(), "a volta aponta a janela viva: nao pode trava-la"
assert painel.indice == 0 and painel.contador.text() == f"1 de {volta_guiada.total()}"
print("OK: Ajuda → Volta guiada existe; o painel abre no passo 1, sem modal")

# ------------------------------------------------- 2. o halo cobre o alvo
def centro_em_janela(w: QWidget):
    return w.mapTo(win, w.rect().center())


primeiro = volta_guiada.PASSOS[0]
alvo0 = getattr(win, primeiro.alvo)
app.processEvents()
assert halo.isVisibleTo(win), "o halo tem de estar ligado no passo 1"
assert halo.geometry().contains(centro_em_janela(alvo0)), (halo.geometry(), centro_em_janela(alvo0))
print("OK: o halo cobre o botao de adicionar midia no passo 1")

# ------------------------------------------------- 3. Estudio fechado = reserva
indice_docs = next(i for i, p in enumerate(volta_guiada.PASSOS) if p.aba == 1)
painel.ir_para(indice_docs)
app.processEvents()
assert painel.nota.isVisibleTo(painel) and painel.nota.text() == volta_guiada.PASSOS[indice_docs].reserva
assert halo.geometry().contains(centro_em_janela(win.interview_table)), \
    "sem entrevista aberta, o passo do Estudio aponta a lista"
print("OK: com o Estudio fechado, o passo aponta a lista e explica")

# Com entrevista "aberta" (so o id basta para a decisao), a aba troca e o
# halo cai no alvo de verdade.
win.current_interview_id = "E1"
painel.ir_para(indice_docs)
app.processEvents()
assert win.review_tabs.currentIndex() == 1, "o passo dos Documentos abre a aba Documentos"
assert not painel.nota.isVisibleTo(painel)
assert halo.geometry().contains(centro_em_janela(win.docs_panel))
win.current_interview_id = None
print("OK: com entrevista aberta, o passo troca a aba e o halo cai no alvo")

# Redimensionar a janela reposiciona o halo (filtro de eventos).
painel.ir_para(0)
app.processEvents()
win.resize(1100, 700)
app.processEvents()
assert halo.geometry().contains(centro_em_janela(alvo0)), "o halo acompanha o redimensionamento"
# Concluir no ultimo passo fecha e apaga o halo.
painel.ir_para(volta_guiada.total() - 1)
painel.proximo_button.click()
app.processEvents()
assert not halo.isVisibleTo(win), "fechar a volta apaga o halo"
print("OK: o halo acompanha a janela e some ao concluir")

# ------------------------------------------------- 5. a faixa da oferta
dados = json.loads(_settings.read_text(encoding="utf-8"))
assert dados.get("volta_guiada_oferecida") is True, \
    "abrir pelo menu conta como oferecida: quem achou sozinho nao precisa da faixa"
_settings.write_text("{}", encoding="utf-8")
for nome in ("engine_offer_banner", "diar_offer_banner", "busy_hint_banner", "voice_batch_banner"):
    getattr(win, nome).setVisible(False)
win._novidades_pendentes = novidades.NOVIDADES[:1]
win._update_novidades_banner()
assert win.tour_offer_banner.isVisibleTo(win), "primeira vez com projeto: a oferta aparece"
assert not win.novidades_banner.isVisibleTo(win), "a novidade cede a oferta da volta"
win.diar_offer_banner.setVisible(True)
win._update_novidades_banner()
assert not win.tour_offer_banner.isVisibleTo(win), "faixa que pede acao vence a oferta"
win.diar_offer_banner.setVisible(False)
win._update_novidades_banner()
assert win.tour_offer_banner.isVisibleTo(win)
win._on_tour_depois()
assert not win.tour_offer_banner.isVisibleTo(win)
assert json.loads(_settings.read_text(encoding="utf-8")).get("volta_guiada_oferecida") is True
win._update_novidades_banner()
assert win.novidades_banner.isVisibleTo(win), "respondida a oferta, a novidade volta"
assert not win.tour_offer_banner.isVisibleTo(win), "'Agora não' e para sempre (fica no menu)"
print("OK: a oferta aparece uma vez, grava a resposta, cede a quem pede acao e libera a novidade")

print("PASS: toy_tour_window")
