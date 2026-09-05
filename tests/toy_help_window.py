"""Toy: a janela F1 lista o app inteiro, lida do menu vivo — 2026-09-05.

O relato que originou isto foi uma pergunta: "como o usuário vai saber
como fazer todas essas coisas? como vai consultar?". O app tinha 150
tooltips bem escritos e NENHUMA tela de consulta.

A aposta do desenho e que o catalogo nao seja escrito a mao: ele e gerado
percorrendo a barra de menus, que por regra do Programa R ja e o catalogo
completo. Este teste fixa a consequencia dessa aposta:

  toda acao da janela que TEM atalho aparece na consulta, com a tecla.

Isso vale para as acoes de hoje e para as que ainda nao existem — nenhuma
lista aqui precisa ser atualizada quando o app crescer.

Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Estado da MAQUINA fora do teste (mesma isolacao do smoke_nav_ui).
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QTabWidget, QTreeWidgetItem
except ImportError:  # pragma: no cover - CI minimo roda sem Qt
    print("SKIP: toy_help_window (PySide6 ausente)")
    raise SystemExit(0)

app = QApplication.instance() or QApplication([])

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
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_help"])

from transcribe_pipeline import comandos  # noqa: E402
from transcribe_pipeline.review_studio_qt import ReviewStudioWindow  # noqa: E402

win = ReviewStudioWindow(project_root=tmp)
win.show()
app.processEvents()

catalogo = comandos.percorrer_menu(
    win.menuBar(),
    fmt_portavel=QKeySequence.SequenceFormat.PortableText,
    fmt_nativo=QKeySequence.SequenceFormat.NativeText)
assert catalogo, "o catalogo nao pode sair vazio"

# --------------------------------------------------- 1. nenhuma tecla escondida
# Regra geral, nao lista: se a acao tem atalho, a pessoa pode esquece-lo, e
# a consulta tem de responder. Vale para acoes futuras sem editar este teste.
por_atalho: dict[str, list[str]] = {}
for cmd in catalogo:
    for tecla in cmd.atalhos:
        por_atalho.setdefault(tecla, []).append(cmd.rotulo)

faltando = []
for nome, obj in vars(win).items():
    if not isinstance(obj, QAction) or not obj.shortcuts():
        continue
    for seq in obj.shortcuts():
        tecla = seq.toString(QKeySequence.SequenceFormat.PortableText)
        if obj.text() not in por_atalho.get(tecla, []):
            faltando.append(f"{nome} ({obj.text()!r}, {tecla})")
assert not faltando, f"acoes com atalho fora da consulta: {faltando}"
print(f"OK: as {len(por_atalho)} teclas de atalho do app aparecem na consulta")

# As 19 do Estudio sao o caso que motivou tudo — conferir uma explicitamente.
alt_p = [c for c in catalogo if "Alt+P" in c.atalhos]
assert len(alt_p) == 1, alt_p
assert alt_p[0].caminho == "Editar › Bloco e reprodução", alt_p[0].caminho
print("OK: Alt+P aparece com o caminho do menu onde mora")

# --------------------------------------------------- 2. o que NAO e comando
rotulos = [c.rotulo for c in catalogo]
assert not [r for r in rotulos if str(tmp) in r], \
    "Projetos recentes e lista de caminhos desta maquina, nao catalogo"
assert win.busy_menu_hint_action.text() not in rotulos, \
    "a dica de lote em andamento e estado, nao comando"
assert win.cancel_job_action.text() not in rotulos, \
    "Cancelar mora na statusbar, nao no menu"
assert "Atalhos e comandos" in rotulos, "a propria consulta tem de se listar"
print("OK: lista dinamica, dica de lote e Cancelar ficam fora; a F1 se lista")

# --------------------------------------------------- 3. sem linha repetida
pares = [(c.caminho, c.rotulo) for c in catalogo]
repetidos = {p for p in pares if pares.count(p) > 1}
assert not repetidos, f"comando listado duas vezes: {repetidos}"
print(f"OK: {len(catalogo)} comandos, nenhum repetido")

# --------------------------------------------------- 4. catraca de explicacao
# Comando sem tooltip proprio aparece sem explicacao. Sao poucos e conhecidos;
# o teto so pode CAIR (mesmo espirito de toy_ui_color_ratchet).
# Medidos em 2026-09-05: Desfazer, Refazer, Renomear rotulo, Mover arquivo
# para cima/baixo, e os tres itens inline que so existem fora do frozen
# (Instalar aceleracao NVIDIA, Verificar atualizacoes, Reparar instalacao).
TETO_SEM_EXPLICACAO = 8
sem_dica = [c.rotulo for c in catalogo if not c.dica]
assert len(sem_dica) <= TETO_SEM_EXPLICACAO, (
    f"{len(sem_dica)} comandos sem explicacao (teto {TETO_SEM_EXPLICACAO}): "
    f"{sem_dica}. Escreva o tooltip, ou ABAIXE o teto se voce ja escreveu.")
print(f"OK: {len(sem_dica)} comandos sem explicacao (teto {TETO_SEM_EXPLICACAO})")

# --------------------------------------------------- 5. a janela
win.open_help()
primeira = win._help_dialog
assert primeira is not None
assert not primeira.isModal(), "consulta-se um atalho ENQUANTO se revisa"
win.open_help("atalhos")
assert win._help_dialog is primeira, "a janela e cacheada, como a Perguntar"

def folhas() -> list[QTreeWidgetItem]:
    arvore = primeira.arvore
    achadas = []
    for i in range(arvore.topLevelItemCount()):
        grupo = arvore.topLevelItem(i)
        achadas.extend(grupo.child(j) for j in range(grupo.childCount()))
    return achadas


todas = len(folhas())
assert todas == len(catalogo), (todas, len(catalogo))
primeira.busca.setText("altp")
app.processEvents()
filtradas = [f.text(0) for f in folhas()]
assert filtradas == ["Passar o fim para o próximo"], filtradas
primeira.busca.setText("")
app.processEvents()
assert len(folhas()) == todas, "limpar a busca devolve a lista inteira"
print(f"OK: a janela mostra os {todas} comandos e a busca por tecla filtra")

# A cola imprimivel sai com conteudo de verdade.
cola = primeira._texto_atual()
assert "Alt+P" in cola and "EDITAR › BLOCO E REPRODUÇÃO" in cola
print("OK: a lista em texto (copiar/salvar) sai completa")

# --------------------------------------------------- 6. F1 com o cursor no texto
# Consultar acontece no meio da revisao: se a tecla nao chega com o foco no
# editor, a janela nao serve para nada (mesma sonda do toy_atalhos_estudio).
alvo = win.text_edit
pai = alvo.parentWidget()
while pai is not None:
    if isinstance(pai, QTabWidget):
        pai.setCurrentWidget(alvo)
        break
    alvo, pai = pai, pai.parentWidget()
win.activateWindow()
win.raise_()
win.text_edit.setEnabled(True)
app.processEvents()
win.text_edit.setPlainText("o cursor da revisora esta aqui dentro")
win.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
app.processEvents()
assert win.text_edit.hasFocus(), "o teste precisa do foco no texto do bloco"

disparos = []
win.shortcuts_action.triggered.disconnect()
win.shortcuts_action.triggered.connect(lambda: disparos.append(1))
antes = win.text_edit.toPlainText()
QTest.keyClick(win.text_edit, Qt.Key.Key_F1, Qt.KeyboardModifier.NoModifier)
app.processEvents()
assert disparos, "F1 nao chega com o cursor dentro do texto do bloco"
assert win.text_edit.toPlainText() == antes, "F1 nao pode escrever no bloco"
print("OK: F1 dispara com o cursor no texto e nao mexe no texto")

# ------------------------------------- 7. o manual nao pode apontar para um beco
# O manual e prosa escrita a mao — o unico pedaco desta ajuda que pode
# envelhecer. A guarda: todo caminho "Menu → Item" citado nele tem de
# existir na barra de menus VIVA. Renomeou uma acao, o teste aponta.
# (Era exatamente esse o defeito do antigo item "Documentação", e o dos
# textos que ainda mandavam o usuario ao menu "Arquivo", extinto na R1.)
import re  # noqa: E402

from transcribe_pipeline import ui_help  # noqa: E402

manual = ui_help.texto_do_manual()
assert manual, "o manual embutido tem de ser encontrado a partir do pacote"

menus = sorted({c.caminho.split(comandos.SETA.strip())[0].strip()
                for c in catalogo if c.caminho}, key=len, reverse=True)
ITEM = r"(?:[^\n.,;)→]*?…|[^\n.,;)→]+)"
CITACAO = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in menus) + r")"
    r"\s*→\s*(" + ITEM + r"(?:\s*→\s*" + ITEM + r")*)")

caminhos_validos = {c.caminho for c in catalogo if c.caminho}
comandos_validos = {(c.caminho, c.rotulo) for c in catalogo}

citados, becos = [], []
for menu, resto in CITACAO.findall(manual):
    partes = [menu] + [p.strip() for p in resto.split("→")]
    citados.append(" → ".join(partes))
    caminho = comandos.SETA.join(partes[:-1])
    if (caminho, partes[-1]) in comandos_validos:
        continue
    if comandos.SETA.join(partes) in caminhos_validos:
        continue  # a citacao nomeia um submenu inteiro, e nao um comando
    becos.append(" → ".join(partes))

assert citados, "o manual precisa dizer ONDE ficam as coisas, com o caminho do menu"
assert not becos, f"o manual manda o usuario a um caminho que nao existe: {becos}"
print(f"OK: os {len(citados)} caminhos de menu citados no manual existem de verdade")

# ------------------------------------- 8. a faixa de novidades cede o lugar
# As quatro faixas da lista sao QFrame empilhados, SEM prioridade: podem
# aparecer juntas. Se a novidade nao cedesse, ela empurraria para baixo o
# aviso de lote em andamento e o de vozes por identificar, que pedem acao.
from transcribe_pipeline import novidades as _novidades  # noqa: E402
from transcribe_pipeline import runtime as _runtime  # noqa: E402

# Antes da faixa, os tres casos da semente. O do meio e o que faz o
# recurso valer a pena: sem ele, a primeira versao a trazer o aviso seria
# a unica incapaz de anunciar as suas proprias novidades.
_settings = _runtime.app_data_dir() / "app_settings.json"
_settings.parent.mkdir(parents=True, exist_ok=True)

_settings.write_text("{}", encoding="utf-8")
win._init_novidades()
assert win._novidades_pendentes == (), "maquina recem-instalada nao recebe aviso"

_settings.write_text('{"install_profile": "padrao"}', encoding="utf-8")
win._init_novidades()
assert win._novidades_pendentes, "quem ja usava o app tem de ver as novidades desta versao"
win._init_novidades()
assert win._novidades_pendentes == (), "a semente ja foi gravada: nao repete"
print("OK: a semente distingue maquina nova de instalacao que ja existia")

win._novidades_pendentes = _novidades.NOVIDADES[:1]
for nome in ("engine_offer_banner", "diar_offer_banner",
             "busy_hint_banner", "voice_batch_banner"):
    getattr(win, nome).setVisible(False)
win._update_novidades_banner()
assert win.novidades_banner.isVisible(), "sozinha, a faixa de novidades aparece"
assert "Novidade" in win.novidades_label.text(), win.novidades_label.text()

win.diar_offer_banner.setVisible(True)
win._update_novidades_banner()
assert not win.novidades_banner.isVisible(), \
    "com uma faixa que pede acao na tela, a novidade cede o lugar"
win.diar_offer_banner.setVisible(False)


class _LoteFalso:
    def isRunning(self): return True


anterior_worker = win.worker
win.worker = _LoteFalso()
win._update_novidades_banner()
assert not win.novidades_banner.isVisible(), "durante um lote, nada de novidade"
win.worker = anterior_worker

# Dispensar grava e nao volta na mesma sessao.
win._update_novidades_banner()
assert win.novidades_banner.isVisible()
win._on_novidades_dispensar()
assert not win.novidades_banner.isVisible()
win._update_novidades_banner()
assert not win.novidades_banner.isVisible(), "dispensada e dispensada"
print("OK: a faixa de novidades cede o lugar, e sai de vez quando dispensada")

# A aba existe sempre — e a resposta a "e depois que eu dispensei?".
win.open_help("novidades")
assert primeira._tabs.tabText(primeira._tabs.currentIndex()) == "Novidades desta versão"
assert "0" in primeira.novidades_view.toPlainText()[:200]
print("OK: a aba Novidades desta versão abre com a lista da versão instalada")

print("PASS: toy_help_window")
