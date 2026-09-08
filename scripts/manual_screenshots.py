"""Capturas de tela do manual, geradas do app — 2026-09-08.

Por que existe: as capturas do site eram de abril e mostravam uma interface
que nao existe mais. Refazer a mao a cada mudanca nao acontece; geradas por
script, refazer e rodar de novo.

MATERIAL SINTETICO, SEMPRE. As entrevistas de pesquisa sao confidenciais e
NUNCA podem aparecer no site (regra do autor, 2026-09-07). Este script monta
um projeto de demonstracao com uma conversa ficticia escrita aqui e um WAV
gerado por codigo — nada real, nada a pedir autorizacao, e reproduzivel em
qualquer maquina.

Duas licoes que o tests/gallery_ui.py ja tinha aprendido e que valem aqui:
- NAO usar offscreen: no Windows o offscreen nao carrega a base de fontes e
  todo texto vira tofu;
- win.grab() renderiza widget OCULTO — nada pisca na tela enquanto roda.

E uma terceira, do manual: recortar POR WIDGET. A coluna de prosa do site
tem ~680 px; uma janela de 1400 px reduzida a isso nao mostra texto nenhum.

Uso:
    python -B scripts/manual_screenshots.py            # grava em build/manual_shots/
    python -B scripts/manual_screenshots.py --lista    # so lista os nomes
    python -B scripts/manual_screenshots.py --publicar "D:/.../site-src/public/img/manual"
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import struct
import sys
import tempfile
import wave
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Plataforma NATIVA de proposito (fontes); estado da maquina fora do caminho.
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# --------------------------------------------------------------- a conversa
# Ficticia, escrita para o manual. Dois falantes, um erro de fronteira de
# proposito (o bloco 6 termina com uma fala da entrevistada) e um bloco
# marcado para conferir — sao os casos que os capitulos explicam.
ENTREVISTA = [
    ("Entrevistadora", 0.0, 6.4, "Para começar, me conta como você veio parar neste bairro."),
    ("Dona Marlene", 6.4, 18.2,
     "Ah, isso foi em noventa e oito. Meu marido trabalhava na fábrica ali da "
     "estrada, e a gente morava longe demais, tomava três conduções."),
    ("Entrevistadora", 18.2, 22.0, "E como era o bairro quando vocês chegaram?"),
    ("Dona Marlene", 22.0, 35.6,
     "Era mato, minha filha. Não tinha asfalto, não tinha posto de saúde. A gente "
     "buscava água na bica lá embaixo. Hoje você olha e não acredita."),
    ("Entrevistadora", 35.6, 40.1, "Quem foi que começou a organizar as coisas por aqui?"),
    ("Dona Marlene", 40.1, 52.8,
     "Foi a associação. Um grupo de mulheres, na verdade. A gente se reunia na "
     "casa da dona Zefa, que era a única que tinha uma sala grande."),
    ("Entrevistadora", 52.8, 61.4,
     "E vocês conseguiram alguma coisa nessa época? Conseguimos o posto, "
     "que foi a maior briga de todas."),
    ("Dona Marlene", 61.4, 70.0,
     "Levou seis anos. Seis anos indo na prefeitura, todo mês, até eles cansarem "
     "da gente."),
]
MARCADOS = {6}  # o bloco com a fronteira errada, marcado para conferir

ID = "01-Conversa de exemplo"
DURACAO = 70.0


def gerar_wav(destino: Path) -> None:
    """Um WAV deterministico com envoltoria de fala. Nao e ninguem falando:
    e ruido modelado pelas fronteiras dos turnos, so para o player e a onda
    terem o que mostrar."""
    taxa = 16000
    aleatorio = random.Random(20260908)
    quadros = bytearray()
    for i in range(int(DURACAO * taxa)):
        t = i / taxa
        falando = any(ini <= t < fim for _f, ini, fim, _txt in ENTREVISTA)
        env = 0.0
        if falando:
            # Silabas: uma modulacao lenta por cima de ruido de banda estreita.
            env = 0.35 * (0.55 + 0.45 * math.sin(2 * math.pi * 3.1 * t))
        amostra = int(32767 * env * (aleatorio.random() * 2 - 1) * 0.6)
        quadros += struct.pack("<h", max(-32768, min(32767, amostra)))
    with wave.open(str(destino), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(taxa)
        fh.writeframes(bytes(quadros))


def montar_projeto(base: Path) -> tuple[Path, Path]:
    """Cria o projeto de demonstracao e devolve (raiz, caminho da midia)."""
    from transcribe_pipeline import manifest as manifest_mod
    from transcribe_pipeline import review_store
    from transcribe_pipeline.config import (DEFAULT_CONFIG, ensure_directories,
                                            make_paths, write_config)

    raiz = base / "Bairro Sul 2026.transcricao"
    midia_dir = base / "gravacoes"
    midia_dir.mkdir(parents=True, exist_ok=True)
    midia = midia_dir / f"{ID}.wav"
    gerar_wav(midia)

    config = dict(DEFAULT_CONFIG)
    config["project_root"] = str(raiz)
    config["asr_language"] = "pt"
    config["diarization_num_speakers"] = 2
    paths = make_paths(config, base_dir=raiz)
    ensure_directories(paths)
    (paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
    write_config(paths.config_dir / "run_config.yaml", config, header=["# demonstração"])

    linha = {c: "" for c in manifest_mod.MANIFEST_COLUMNS}
    linha.update({
        "interview_id": ID, "source_path": str(midia), "source_ext": ".wav",
        # "true", nao "1": get_interview_row filtra por esta string exata.
        "source_kind": "A", "selected": "true",
        "source_size_bytes": str(midia.stat().st_size),
    })
    with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
        escritor = csv.DictWriter(h, fieldnames=manifest_mod.MANIFEST_COLUMNS)
        escritor.writeheader()
        escritor.writerow(linha)
    (paths.manifest_dir / "speakers_map.csv").write_text(
        "interview_id,speaker_id,role\n", encoding="utf-8-sig")

    turnos = []
    for i, (falante, ini, fim, texto) in enumerate(ENTREVISTA):
        turno = {
            "id": f"t{i + 1}",
            "speaker": f"SPEAKER_{0 if falante.startswith('Entrevistadora') else 1:02d}",
            "human_label": falante,
            "start": ini, "end": fim, "text": texto,
            "flags": ["duvida"] if i in MARCADOS else [],
            "notes": "as vozes dos dois lados parecem iguais" if i in MARCADOS else "",
        }
        turnos.append(turno)
    canonico = {"interview_id": ID, "turns": turnos}
    alvo = review_store.canonical_path(paths, ID)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(json.dumps(canonico, ensure_ascii=False, indent=1), encoding="utf-8")
    review = {
        "schema_version": review_store.REVIEW_SCHEMA_VERSION,
        "review_status": "draft", "reviewer": "",
        "source": {"canonical_path": str(alvo), "interview_id": ID},
        "transcript": json.loads(json.dumps(canonico)),
        "edits": [],
    }
    review_store.save_review_transcript(paths, ID, review)
    return raiz, midia


def picos_sinteticos() -> list[float]:
    """A envoltoria da onda, desenhada das fronteiras dos turnos.

    Com silabas e uma respiracao no fim de cada turno: sem isso o desenho
    vira um bloco macico de ruido, que nao parece fala nenhuma.
    """
    aleatorio = random.Random(7)
    passos = 1200
    picos = []
    for i in range(passos):
        t = DURACAO * i / passos
        turno = next((x for x in ENTREVISTA if x[1] <= t < x[2]), None)
        if turno is None:
            picos.append(0.02)
            continue
        # Meio segundo de silencio antes de cada troca de falante.
        if turno[2] - t < 0.5:
            picos.append(0.03 + 0.02 * aleatorio.random())
            continue
        silaba = 0.5 + 0.5 * abs(math.sin(2 * math.pi * 2.7 * t))
        frase = 0.7 + 0.3 * math.sin(2 * math.pi * 0.35 * t)
        picos.append(max(0.02, min(1.0, 0.85 * silaba * frase * (0.7 + 0.3 * aleatorio.random()))))
    return picos


# ------------------------------------------------------------------ capturas
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lista", action="store_true", help="so listar os nomes")
    ap.add_argument("--publicar", metavar="PASTA", help="gravar direto na pasta do site")
    args = ap.parse_args()

    nomes = ["lista-do-projeto", "transcrever-menu", "estudio", "estudio-blocos",
             "estudio-editor", "vozes", "documentos", "propriedades",
             "atalhos-f1", "volta-guiada", "exportar"]
    if args.lista:
        print("\n".join(nomes))
        return 0

    destino = Path(args.publicar) if args.publicar else RAIZ / "build" / "manual_shots"
    destino.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    # Dobrar a densidade: as capturas sobrevivem a reducao para 680 px.
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # Nenhuma caixa modal pode aparecer num script sem ninguem na frente: ela
    # trava para sempre e sem mensagem. Aqui elas viram linha impressa.
    from PySide6.QtWidgets import QMessageBox

    def _fala(titulo, texto, *_a, **_k):
        print(f"  [caixa suprimida] {titulo}: {str(texto)[:120]}")
        return QMessageBox.StandardButton.No

    for _nome in ("information", "warning", "critical", "question", "about"):
        setattr(QMessageBox, _nome, staticmethod(_fala))

    print("montando o projeto de demonstração…")
    base = Path(tempfile.mkdtemp())
    raiz, _midia = montar_projeto(base)
    print(f"  projeto: {raiz}")

    from transcribe_pipeline.review_studio_qt import (ReviewStudioWindow,
                                                      _apply_dark_theme)
    _apply_dark_theme(app)
    win = ReviewStudioWindow(project_root=raiz)
    win.resize(1440, 900)
    # A oferta da volta guiada aparece em maquina "nova" e, na coluna
    # estreita da lista, quebra em vinte linhas — ruido em toda captura.
    # Ela tem captura propria (o painel), entao aqui e dispensada.
    win._marcar_tour_oferecida()
    win._novidades_pendentes = ()
    win._update_novidades_banner()
    # A lista nasce apertada; nas capturas ela precisa caber o nome do
    # arquivo. O divisor principal e uma variavel local em _build_ui, entao
    # e achado pela arvore de widgets (o unico horizontal da janela).
    from PySide6.QtWidgets import QSplitter

    for div in win.findChildren(QSplitter):
        if div.orientation() == Qt.Orientation.Horizontal and div.count() >= 2:
            div.setSizes([470, 970])
    app.processEvents()

    salvos: list[str] = []

    def grava(nome: str, alvo=None, margem: int = 0) -> None:
        app.processEvents()
        widget = alvo if alvo is not None else win
        if margem and widget is not win:
            canto = widget.mapTo(win, QPoint(0, 0))
            reg = QRect(canto, widget.size()).adjusted(-margem, -margem, margem, margem)
            imagem = win.grab(reg)
        else:
            imagem = widget.grab()
        arquivo = destino / f"{nome}.png"
        if imagem.save(str(arquivo)):
            salvos.append(nome)
            print(f"  {nome}.png  {imagem.width()}x{imagem.height()}")
        else:
            print(f"  {nome}.png FALHOU")

    print(f"Capturas em {destino}")
    grava("lista-do-projeto")

    # O menu e uma JANELA propria: win.grab() nao o inclui (a captura saia
    # identica a da lista). Capturar o proprio menu, depois do popup.
    botao = getattr(win, "transcribe_button", None)
    if botao is not None and botao.menu() is not None:
        menu = botao.menu()
        menu.popup(botao.mapToGlobal(QPoint(0, botao.height())))
        app.processEvents()
        grava("transcrever-menu", menu)
        menu.hide()
        app.processEvents()

    win.open_review(ID)
    app.processEvents()
    if hasattr(win, "waveform_widget"):
        win.waveform_widget.set_waveform(picos_sinteticos(), DURACAO)
    app.processEvents()
    grava("estudio")
    grava("estudio-blocos", win.turn_table, margem=8)
    if hasattr(win, "review_splitter"):
        grava("estudio-editor", win.text_edit.parentWidget(), margem=8)

    if hasattr(win, "review_tabs"):
        win.review_tabs.setCurrentIndex(1)
        grava("documentos", win.review_tabs, margem=6)
        win.review_tabs.setCurrentIndex(2)
        grava("propriedades", win.review_tabs, margem=6)
        win.review_tabs.setCurrentIndex(0)

    from transcribe_pipeline.review_studio_qt import ExportDialog, SpeakerNamingDialog
    # Formato das linhas: title / samples / suggestions (review_studio_qt:11113).
    def _amostras(indices):
        return [{"start": ENTREVISTA[i][1], "end": ENTREVISTA[i][2],
                 "text": ENTREVISTA[i][3]} for i in indices]

    vozes = SpeakerNamingDialog(
        _midia,
        [{"title": "Voz 1 — 0,6 min de fala em 4 blocos",
          "samples": _amostras([0, 4]),
          "suggestions": ["Entrevistadora", "Entrevistado", "Moderadora"]},
         {"title": "Voz 2 — 0,8 min de fala em 4 blocos",
          "samples": _amostras([1, 5]),
          "suggestions": ["Dona Marlene", "Entrevistada", "Participante"]}],
        win)
    grava("vozes", vozes)
    vozes.close()

    exportar = ExportDialog(has_open=True, open_title=ID, n_selected=0, n_total=1, parent=win)
    grava("exportar", exportar)
    exportar.close()

    win.open_help("atalhos")
    app.processEvents()
    grava("atalhos-f1", win._help_dialog)
    win._help_dialog.close()

    win.open_tour()
    app.processEvents()
    grava("volta-guiada", win._tour_panel)
    win._tour_panel.close()

    print(f"\n{len(salvos)} captura(s): {', '.join(salvos)}")
    faltando = [n for n in nomes if n not in salvos]
    if faltando:
        print(f"nao geradas (estado indisponivel): {', '.join(faltando)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
