from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable
from array import array
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import os
import shutil
import glob
import re
import subprocess
import sys
import time
import wave
from bisect import bisect_left

_logger = logging.getLogger("transcritorio.gui")


def _setup_logger() -> None:
    """Configura o logger do GUI.

    Modo dev (rodando do venv/scripts): stderr visible -> StreamHandler.
    Modo frozen (PyInstaller --windowed): sem console -> arquivo rotativo em
    app_data_dir()/logs/gui.log para nao spammar o usuario final.
    """
    if _logger.handlers:
        return
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(name)s %(levelname)s] %(message)s", datefmt="%H:%M:%S")
    is_frozen = bool(getattr(sys, "frozen", False))
    if is_frozen:
        try:
            from . import runtime as _runtime
            logs_dir = _runtime.app_data_dir() / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            from logging.handlers import RotatingFileHandler
            _h = RotatingFileHandler(
                str(logs_dir / "gui.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            _h.setFormatter(fmt)
            _logger.addHandler(_h)
        except Exception:
            # Ultimo recurso: NullHandler (silencia)
            _logger.addHandler(logging.NullHandler())
    else:
        _h = logging.StreamHandler()
        _h.setFormatter(fmt)
        _logger.addHandler(_h)


_setup_logger()

from . import app_service, project_store, review_store, voice_recognition
from .runtime import resolve_executable
from .utils import sanitize_message

try:
    from PySide6.QtCore import QEvent, QPointF, QThread, QTimer, Qt, QUrl, Signal
    from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QShortcut, QUndoCommand, QUndoStack
    from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QProgressDialog,
        QRadioButton,
        QScrollArea,
        QSlider,
        QSpinBox,
        QSplitter,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextBrowser,
        QTextEdit,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
        QWizard,
        QWizardPage,
    )
except ImportError as exc:  # pragma: no cover
    QT_IMPORT_ERROR: ImportError | None = exc
else:
    QT_IMPORT_ERROR = None


SPEAKER_LABELS = {"Entrevistador": "ENTREVISTADOR", "Entrevistado": "ENTREVISTADO"}
FLAG_LABELS = {"inaudivel": "Inaud\u00edvel", "duvida": "D\u00favida", "sobreposicao": "Sobreposi\u00e7\u00e3o"}
VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


def preferred_media_index(candidates: list[Path]) -> int:
    """Indice da midia que o player deve abrir por padrao.

    Os timestamps da transcricao referem-se ao WAV preparado (16 kHz); em
    originais MP3/M4A (VBR) o seek e impreciso e o desvio ACUMULA ao longo
    do arquivo — causa da dessincronia audio x texto vista em uso real
    (2026-08-25). Regra: fonte de audio -> WAV preparado (fallback:
    original); fonte de VIDEO -> original (o painel de video precisa da
    imagem; drift em AAC e muito menor).
    """
    if not candidates:
        return 0
    if candidates[0].suffix.lower() in VIDEO_SUFFIXES:
        return 0
    for index, path in enumerate(candidates):
        if path.suffix.lower() == ".wav":
            return index
    return 0


def _promote_staging_to_files(
    staging: Path,
    files_dir: Path,
    delays: tuple[float, ...] = (0.0, 0.2, 0.4, 0.8, 1.6, 3.2),
) -> None:
    """Conclui a promocao staging/ -> files/ tolerando o lock do Dropbox.

    O rename de diretorio falha com WinError 5 quando o cliente Dropbox
    segura handles dos arquivos recem-copiados para sincroniza-los
    (incidente de 2026-08-25 — mesma familia da regra "sem renames
    atomicos" do pipeline). Tenta o rename com recuos; se persistir,
    promove arquivo a arquivo (moves individuais nao sofrem o lock do
    diretorio) preservando a estrutura relativa.
    """
    import shutil
    import time as _time

    last_error: Exception | None = None
    for delay in delays:
        if delay:
            _time.sleep(delay)
        try:
            staging.rename(files_dir)
            return
        except OSError as exc:
            last_error = exc
    for src in sorted(staging.rglob("*")):
        if not src.is_file():
            continue
        dest = files_dir / src.relative_to(staging)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    shutil.rmtree(staging, ignore_errors=True)
    if not files_dir.exists():
        raise last_error or OSError("nao foi possivel promover staging para files")


def fmt_gb(value: float) -> str:
    """Tamanho em GB no formato do guia: virgula decimal, sem casa quando
    inteiro ("8,7 GB", "3 GB")."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "? GB"
    if abs(number - round(number)) < 0.05:
        return f"{int(round(number))} GB"
    return f"{number:.1f}".replace(".", ",") + " GB"


def render_answer_html(resposta: str, n_trechos: int, ids: tuple[str, ...] = ()) -> str:
    """Resposta da AI em HTML com as citacoes clicaveis (pura).

    [n] com 1 <= n <= n_trechos vira <a href="trecho:n">; [ID] de uma
    entrevista conhecida vira <a href="entrevista:ID">; o resto e escapado.
    """
    import html as _html
    import re as _re

    known = {str(i) for i in ids}

    def _sub(match: "_re.Match[str]") -> str:
        inner = match.group(1)
        if inner.isdigit() and 1 <= int(inner) <= int(n_trechos):
            return f'<a href="trecho:{int(inner)}">[{inner}]</a>'
        if inner in known:
            return f'<a href="entrevista:{_html.escape(inner)}">[{_html.escape(inner)}]</a>'
        return f"[{_html.escape(inner)}]"

    escaped = _html.escape(str(resposta or ""))
    linked = _re.sub(r"\[([^\[\]\n]{1,80})\]", _sub, escaped)
    return "<p>" + linked.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"


def results_footer_text(result: dict[str, Any]) -> str:
    """Rodape honesto da busca (pura): quantos trechos tratam do tema, de
    ate quantos, e se o corte veio do reordenador ou so da semelhanca."""
    hits = result.get("hits") or []
    n = len(hits)
    max_results = int(result.get("max_results") or 0)
    considered = int(result.get("considered") or 0)
    if n == 0:
        base = "Nenhum trecho trata disso de perto."
    else:
        plural = n != 1
        base = f"{n} trecho{'s' if plural else ''} trata{'m' if plural else ''} disso"
        if max_results:
            base += f" (de até {max_results})"
        base += "."
        if considered > n:
            base += " O restante ficou fora por não tratar do tema."
    if not result.get("reranked"):
        base += (" Ordenados só pela semelhança — a busca de qualidade (Gerenciar modelos) "
                 "faz um corte mais certeiro.")
    return base


def relevance_label(hit: dict[str, Any]) -> str:
    """Grau de proximidade em linguagem simples (nunca numeros na UI).

    v3 (2026-09-03): RELATIVO ao escopo — `z` = desvios acima da media dos
    cossenos de todas as passagens consultadas (search.rank_semantic). Um
    piso absoluto nao separa nada num encoder de recuperacao (cossenos
    comprimidos em 0,7-0,9), e os limiares antigos (0,55) rotulavam tudo
    "muito proximo". O reordenador (etapa seguinte) refina isto.
    """
    try:
        z = float(hit.get("z", 0) or 0)
    except (TypeError, ValueError):
        z = 0.0
    if z >= 3.0:
        return "muito próximo"
    if z >= 2.0:
        return "próximo"
    return "relacionado"


def search_scope_text(scope: str, total: int, ready: int, subject: str) -> str:
    """Linha de escopo das janelas de busca/AI (pura, testavel sem Qt).

    Diz ONDE a operacao busca e SOBRE O QUE (transcricoes, nunca o audio
    bruto) — feedback 2026-08-26: o usuario nao tinha como saber. scope:
    "all" | "choose" | "open"; total = itens no escopo; ready = com
    transcricao (revisada > canonica). No modo choose a lista interna so
    contem transcritas, entao ready == total por construcao.
    """
    missing = total - ready
    if scope == "open":
        if ready:
            return f"{subject} lê a transcrição da entrevista aberta."
        return (f"{subject} lê transcrições, não o áudio — "
                "e a entrevista aberta ainda não foi transcrita.")
    if scope == "choose":
        if total == 0:
            return "Marque na lista acima quais entrevistas entram."
        if total == 1:
            return f"{subject} lê a transcrição da entrevista escolhida."
        return f"{subject} lê as transcrições das {total} entrevistas escolhidas."
    if total == 0:
        return "Este projeto ainda não tem arquivos."
    if ready == 0:
        if total == 1:
            return (f"{subject} lê transcrições, não o áudio — "
                    "e o único arquivo do projeto ainda não foi transcrito.")
        return (f"{subject} lê transcrições, não o áudio — e nenhum dos "
                f"{total} arquivos do projeto foi transcrito ainda.")
    if ready == total:
        if total == 1:
            return f"{subject} lê a transcrição do único arquivo do projeto."
        return f"{subject} lê as transcrições de todos os {total} arquivos do projeto."
    return (f"{subject} lê as transcrições de {ready} dos {total} arquivos do "
            f"projeto — {missing} ainda sem transcrição ficam de fora.")


def boundary_flagged_rows(turns: list[dict[str, Any]]) -> list[int]:
    """Turnos da verificacao acustica de trocas que o usuario ainda NAO
    tratou: precisam do marcador na nota (origem automatica) E do flag
    'duvida' ainda presente — desmarcar Duvida no editor tira o bloco da
    contagem do banner na hora (feedback de uso real, 2026-08-25)."""
    rows: list[int] = []
    for index, turn in enumerate(turns):
        flags = {str(flag) for flag in (turn.get("flags") or [])}
        if "duvida" in flags and BOUNDARY_NOTE_MARKER in str(turn.get("notes") or ""):
            rows.append(index)
    return rows
APP_NAME = "Transcrit\u00f3rio"


def channel_suffix(channel: str | None = None) -> str:
    """Sufixo do titulo quando o app roda num canal de teste (puro).
    "" no canal normal; " \u2014 vers\u00e3o de teste (beta 0.3.0b1)" na beta."""
    from . import __version__
    nome = (channel if channel is not None else os.environ.get("TRANSCRITORIO_CHANNEL", "")).strip()
    if not nome:
        return ""
    return f" \u2014 vers\u00e3o de teste ({nome} {__version__})"
APP_CREDITS = "Rog\u00e9rio Jer\u00f4nimo Barbosa - https://antrologos.github.io/"
APP_ICON_FILE = "transcritorio_icon.svg"
WAVEFORM_CACHE_VERSION = 1

# Interview table column indices (checkbox at col 0, data cols shifted +1)
COL_CHECK = 0
COL_ARQUIVO = 1
COL_FORMATO = 2
COL_TRANSCRICAO = 3
COL_DURACAO = 4
COL_LINGUA = 5
COL_FALANTES = 6
COL_ROTULOS = 7
COL_CONTEXTO = 8
COL_AVISOS = 9


MAX_TITLE_CHARS = 200


def _sanitize_rename_title(raw: str) -> tuple[str, bool]:
    """Sanitize a user-entered display label.

    Returns (title, truncated). An empty return means "reset to default".
    """
    if raw is None:
        return "", False
    cleaned = "".join(c for c in raw if c.isprintable() or c == " ")
    cleaned = cleaned.strip()
    truncated = len(cleaned) > MAX_TITLE_CHARS
    if truncated:
        cleaned = cleaned[:MAX_TITLE_CHARS]
    return cleaned, truncated


from .boundary_check import BOUNDARY_NOTE_MARKER
from . import ui_tokens


# Helpers de cor para tema escuro — cores vindas de ui_tokens.
# Contraste validado em toy_ui_tokens (WCAG) contra os fundos da paleta.
def _style_ok() -> str:
    return f"color: {ui_tokens.SUCCESS_TEXT}; font-weight: 700;"


def _style_warn() -> str:
    return f"color: {ui_tokens.WARN};"


def _style_err() -> str:
    return f"color: {ui_tokens.DANGER_TEXT}; font-weight: 700;"


def _style_muted() -> str:
    return f"color: {ui_tokens.TEXT_MUTED};"


def _compute_effective_target_ids(
    all_ids_in_order: list[str],
    checked: set[str],
    visually_selected: set[str],
    cursor_row_id: str | None = None,
) -> list[str]:
    """Windows Explorer precedence for target selection.

    1. Cursor outside both checked and visually_selected -> return only cursor.
    2. Cursor inside visually_selected -> return visually_selected (visual order).
    3. Else if checked non-empty -> return checked (visual order).
    4. Else -> return visually_selected (visual order).
    """
    if cursor_row_id is not None and cursor_row_id not in checked and cursor_row_id not in visually_selected:
        return [cursor_row_id]
    if cursor_row_id is not None and cursor_row_id in visually_selected:
        return [iid for iid in all_ids_in_order if iid in visually_selected]
    if checked:
        return [iid for iid in all_ids_in_order if iid in checked]
    return [iid for iid in all_ids_in_order if iid in visually_selected]


def _compute_destructive_target_ids(
    all_ids_in_order: list[str],
    visually_selected: set[str],
    cursor_row_id: str | None = None,
) -> list[str]:
    """Alvo de acoes DESTRUTIVAS (Lixeira): SO a selecao visual/cursor.

    Nunca usa os checkboxes: eles significam "transcrever estes" (e vem
    marcados por default) — usa-los como alvo de delecao mandou TODAS as
    entrevistas de um projeto para a lixeira quando o usuario mirava uma
    (incidente de 2026-08-25).
    """
    if cursor_row_id is not None and cursor_row_id not in visually_selected:
        return [cursor_row_id]
    return [iid for iid in all_ids_in_order if iid in visually_selected]


def open_folder_in_explorer(path: Path) -> None:
    """Open a folder in the platform's file manager."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def app_asset_path(filename: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "assets" / filename
    repo = Path(__file__).resolve().parent.parent / "assets" / filename
    if repo.exists():
        return repo
    # Wheel instalado (v0.2): os assets viajam DENTRO do pacote
    # (transcribe_pipeline/assets, package-data) — parent.parent seria
    # site-packages, onde nao ha assets/.
    return Path(__file__).resolve().parent / "assets" / filename


def format_clock(seconds: float | int | None) -> str:
    if seconds is None:
        seconds = 0
    total = max(0, int(float(seconds)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_timecode(seconds: float | int | None) -> str:
    if seconds is None:
        seconds = 0
    total_ms = max(0, int(round(float(seconds) * 1000)))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}.{ms:03d}"


def media_format_label(status: object) -> str:
    """Return user-friendly media format, e.g. 'Áudio M4A' or 'Vídeo MP4 (WAV pronto)'."""
    ext = getattr(status, "source_ext", "").lower().lstrip(".")
    if not ext:
        return ""
    is_video = f".{ext}" in VIDEO_SUFFIXES
    tipo = "Vídeo" if is_video else "Áudio"
    label = f"{tipo} {ext.upper()}"
    if getattr(status, "wav_exists", False) and ext != "wav":
        label += " (WAV pronto)"
    return label


def media_splitter_sizes(
    total: int,
    video_visible: bool,
    *,
    min_video: int = 140,
    min_audio: int = 180,
    min_table: int = 180,
    min_editor: int = 210,
) -> list[int]:
    """Distribuicao [video, audio, blocos, editor] do review_splitter.

    Desde 2026-08-31 sao QUATRO paineis independentes e COLAPSAVEIS
    (pedido do usuario: minimizar cada secao a zero e esticar sem
    teto); esta funcao da a distribuicao das TRANSICOES (video
    aparece/some) — depois disso quem manda e o arrasto. Invariantes:
    soma == total exata; a tabela de blocos nunca fica abaixo de
    min_table quando o total permite (o deficit sai do video, depois
    do audio, depois do editor); janela menor que a soma dos pisos
    escala proporcionalmente; video oculto = 0 no primeiro slot.

    Os pisos cobrem os minimumSizeHint reais dos paineis (video 120;
    audio = onda 96 + duas fileiras de botoes ≈ 176; editor =
    text_edit 60 + fileiras ≈ 208) para a distribuicao inicial nao
    nascer clampada. Mudou um minimo interno? Ajustar aqui junto.
    """
    if total <= 0:
        return [0, 0, 0, 0]
    if video_visible:
        fracs = (0.22, 0.20, 0.34, 0.24)
        pisos = (min_video, min_audio, min_table, min_editor)
    else:
        fracs = (0.0, 0.26, 0.46, 0.28)
        pisos = (0, min_audio, min_table, min_editor)
    soma_pisos = sum(pisos)
    if total < soma_pisos:
        video = total * pisos[0] // soma_pisos
        audio = total * pisos[1] // soma_pisos
        tabela = total * pisos[2] // soma_pisos
        return [video, audio, tabela, total - video - audio - tabela]
    video = max(pisos[0], round(total * fracs[0])) if video_visible else 0
    audio = max(pisos[1], round(total * fracs[1]))
    editor = max(pisos[3], round(total * fracs[3]))
    tabela = total - video - audio - editor
    if tabela < min_table:
        deficit = min_table - tabela
        cede = min(deficit, video - pisos[0])
        video -= cede
        deficit -= cede
        cede = min(deficit, audio - pisos[1])
        audio -= cede
        deficit -= cede
        editor -= min(deficit, editor - pisos[3])
        tabela = total - video - audio - editor
    return [video, audio, tabela, editor]


def choose_split_time(
    turn_start: float,
    turn_end: float,
    edit_cursor: float | None,
    edit_cursor_from_click: bool,
    player_seconds: float | None,
    player_playing: bool,
    words: list[dict[str, Any]],
    text: str,
    split_char: int,
) -> tuple[float, str]:
    """Tempo da fronteira ao dividir um bloco (pura — gate no toy).

    O texto SEMPRE divide no cursor de texto; esta funcao escolhe o tempo
    coerente com essa promessa. Prioridade: clique deliberado na onda >
    palavra sob o cursor de texto > player pausado dentro do bloco >
    interpolacao linear. Candidato fora do intervalo aberto
    (turn_start, turn_end) cai para a regra seguinte — a posicao do
    player durante reproducao ativa NUNCA decide (era a causa de
    fronteiras incoerentes com o texto).
    """
    from . import words as words_mod

    if (
        edit_cursor_from_click
        and edit_cursor is not None
        and turn_start < float(edit_cursor) < turn_end
    ):
        return float(edit_cursor), "tempo definido pelo clique na onda sonora"
    word_time, word_exact = words_mod.word_time_for_char(words, text, split_char)
    if word_time is not None and turn_start < float(word_time) < turn_end:
        return float(word_time), (
            "tempo exato da palavra sob o cursor" if word_exact
            else "tempo aproximado pela palavra mais próxima")
    if (
        not player_playing
        and player_seconds is not None
        and turn_start < float(player_seconds) < turn_end
    ):
        return float(player_seconds), "tempo definido pela posição do player (pausado)"
    text_length = max(1, len(text))
    ratio = max(0.01, min(0.99, split_char / text_length))
    return (
        turn_start + ((turn_end - turn_start) * ratio),
        "tempo estimado pela posição do cursor no texto",
    )


def languages_outside_pt(langs: Iterable[str]) -> set[str]:
    """Idiomas do lote que o TAGARELA NAO cobre (pura).

    Ignora vazios, "pt" e o rotulo "automático" (= nenhum idioma
    declarado; para um motor so-portugues, transcrever como pt e o
    comportamento esperado — antes o rotulo bloqueava a oferta E o
    proprio guard do motor, caso real de beta tester 2026-09-02).
    """
    return {str(lang).strip() for lang in langs if str(lang).strip()} - {"pt", "automático"}


# Qualidade dos Whisper em linguagem de gente (2026-09-02: o TAGARELA e o
# padrao em todas as maquinas; para outros idiomas o usuario escolhe um
# Whisper e precisa saber o que perde/ganha com cada tamanho).
_WHISPER_QUALITY_NOTES: dict[str, str] = {
    "large-v3": "melhor qualidade; lento sem placa de vídeo",
    "large-v3-turbo": "quase a mesma qualidade do large-v3, bem mais rápido; ideal com placa de vídeo",
    "medium": "boa qualidade; lento sem placa de vídeo",
    "small": "rápido em qualquer computador; erra mais palavras que os modelos grandes",
    "base": "demonstração — qualidade baixa",
    "tiny": "demonstração — qualidade baixa",
}
_WHISPER_QUALITY_ORDER = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")


def whisper_reserve_options(installed: Iterable[str], recommended: str) -> list[tuple[str, str, bool]]:
    """Whisper para outros idiomas: [(chave, nota de qualidade, instalado)] (pura).

    A reserva recomendada da maquina vem primeiro (mesmo se ainda nao
    baixada); depois os Whisper instalados, do melhor para o mais simples.
    """
    inst = {str(k) for k in installed if str(k) in _WHISPER_QUALITY_ORDER}
    chaves: list[str] = []
    if recommended in _WHISPER_QUALITY_ORDER:
        chaves.append(recommended)
    for key in _WHISPER_QUALITY_ORDER:
        if key in inst and key not in chaves:
            chaves.append(key)
    return [(k, _WHISPER_QUALITY_NOTES.get(k, ""), k in inst) for k in chaves]


def engine_language_note(model_key: str | None, language: str | None) -> str:
    """Aviso inline quando o modelo e o TAGARELA e o idioma nao e portugues
    (pura). "" quando nao ha o que avisar (pt, vazio, automático, Whisper)."""
    from . import model_manager as _mm_note
    if (_mm_note.ASR_VARIANTS.get(str(model_key or "")) or {}).get("engine") != "parakeet_onnx":
        return ""
    codigo = str(language or "").strip()
    if codigo.lower() in {"", "pt", "auto", "automático", "project"}:
        return ""
    rotulo = str((_mm_note.ALIGN_LANGUAGES.get(codigo) or {}).get("label") or codigo)
    return (f"⚠ O TAGARELA só transcreve português. Para {rotulo}, escolha um "
            "modelo Whisper — na hora de transcrever o Transcritório também oferece essa troca.")


def engine_offer_due(
    device: str,
    engine: str | None,
    platform: str,
    declined: bool,
    busy: bool,
) -> bool:
    """Faixa da lista oferecendo o TAGARELA (pura).

    Motor atual e Whisper, oferta nao recusada em definitivo e nenhum job
    rodando — em QUALQUER maquina (2026-09-02: o TAGARELA e o padrao em
    todas; antes so sem GPU e fora do Mac). E o caminho de migracao
    VISIVEL para quem instalou antes (a QMessageBox ao Transcrever era
    facil de nao ver). `device`/`platform` ficam na assinatura por
    compatibilidade.
    """
    _ = (device, platform)
    if busy or declined:
        return False
    return engine != "parakeet_onnx"


# --- Progresso do lote em DOIS niveis (decisao do usuario, 2026-09-02) -------
# Lista: cada arquivo mostra a propria etapa e o proprio avanco. Barra de
# baixo: so o lote ("Arquivo k de n · id · atividade · ~M min restantes"),
# sem o percentual interno do motor no texto — tres numeros com tres
# significados confundiam. Puras, cobertas por tests/toy_progress_rows.
_STAGE_VERBS: dict[str, str] = {
    "aguardando": "Na fila",
    "preparar audio": "Preparando áudio",
    "transcrever": "Transcrevendo",
    "identificar falantes": "Separando falantes",
    "montar transcricao": "Montando transcrição",
    "conferir trocas": "Conferindo trocas",
    "recriar transcricao": "Recriando transcrição",
    "verificar arquivos": "Verificando",
}


def row_progress_text(job: dict[str, Any] | None) -> str | None:
    """Texto da coluna Transcrição para um job em andamento; None quando o
    job nao esta em andamento (a linha precisa do estado completo)."""
    job = job or {}
    status = str(job.get("status") or "")
    if status == "Na fila":
        return "Na fila"
    if status != "Rodando":
        return None
    verbo = _STAGE_VERBS.get(str(job.get("stage") or "").strip().lower(), "Processando")
    try:
        pct = max(0, min(100, int(job.get("progress") or 0)))
    except (TypeError, ValueError):
        pct = 0
    return f"{verbo} {pct}%"


def strip_percent(text: str) -> str:
    """Tira percentuais internos ("(46%)", "— 30%") do texto de atividade."""
    s = re.sub(r"\s*[—–-]\s*\d{1,3}%", "", str(text or ""))
    s = re.sub(r"\s*\(\d{1,3}%\)", "", s)
    s = re.sub(r"\s*\b\d{1,3}%", "", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def strip_step_prefix(message: str) -> str:
    """Remove o prefixo "k/n ID: " das mensagens de etapa do lote."""
    return re.sub(r"^\d+/\d+\s+\S+:\s*", "", str(message or ""))


def eta_text(remaining_s: float) -> str:
    remaining_s = max(0.0, float(remaining_s))
    if remaining_s < 90:
        return "menos de 2 min restantes"
    mins = int(round(remaining_s / 60))
    if mins < 60:
        return f"~{mins} min restantes"
    horas, resto = divmod(mins, 60)
    return f"~{horas} h {resto:02d} min restantes"


def batch_label(k: int, n: int, group: str, atividade: str, remaining_s: float | None) -> str:
    """Rotulo da barra de baixo durante um lote por arquivo (pura)."""
    partes = [f"Arquivo {k} de {n}", str(group or ""), strip_percent(strip_step_prefix(atividade))]
    if remaining_s is not None:
        partes.append(eta_text(remaining_s))
    return " · ".join(p for p in partes if p)


def busy_hint_text(estado: str) -> str:
    """Faixa da lista durante um lote (pura): o usuario achou que as ferramentas
    de AI "nao vieram instaladas" porque ficavam cinza sem explicacao (2026-09-02)."""
    estado = str(estado or "").strip().rstrip(".")
    if estado:
        return (f"⏳ Lote em andamento — {estado}. As análises com AI e as demais ações "
                "voltam sozinhas quando terminar.")
    return "⏳ Tarefa em andamento. As análises com AI e as demais ações voltam sozinhas quando terminar."


def busy_click_text(acao: str, estado: str) -> str:
    """Resposta ao clique numa acao durante o lote (pura)."""
    estado = str(estado or "").strip().rstrip(".")
    andamento = f"Em andamento: {estado}. " if estado else ""
    return (f"O Transcritório executa uma tarefa por vez. {andamento}"
            f"\"{acao}\" fica disponível quando o lote terminar — não é preciso fazer nada.")


def busy_reason_text(estado: str) -> str:
    """Tooltip de todo item desabilitado por lote (pura)."""
    estado = str(estado or "").strip().rstrip(".")
    if estado:
        return f"Aguarde o lote terminar ({estado}) — as ações voltam sozinhas."
    return "Aguarde a tarefa atual terminar — as ações voltam sozinhas."


def failure_summary(failures: int, message: str | None) -> str:
    """Texto de uma etapa com falhas: "N falha(s)." + a causa que o servico
    devolveu em JobResult.message (pura). Incidente 2026-09-02: "1 falha(s)."
    sozinho escondia "Não encontrei o texto transcrito de …"."""
    base = f"{failures} falha(s)."
    detail = str(message or "").strip()
    return f"{base} {detail}" if detail else base


def job_step_flags(do_diarize: bool, boundary_cfg: bool, diarize_now: bool | None) -> tuple[bool, bool]:
    """(diarizar, conferir trocas) de UM lote (pura).

    diarize_now=False e a caixa "Separar falantes agora" desmarcada: vale so
    para este lote, nunca grava no projeto; sem diarizacao nao ha trocas a
    conferir. None/True nao mudam a decisao do projeto.
    """
    diar = bool(do_diarize) and (diarize_now is not False)
    return diar, diar and bool(boundary_cfg)


def parakeet_cpu_offer_due(
    device: str,
    engine: str | None,
    langs: Iterable[str],
    declined: bool,
    platform: str | None = None,
) -> bool:
    """Oferecer o TAGARELA ao transcrever com o Whisper (pura).

    Padrao em TODAS as maquinas desde 2026-09-02 (antes so sem GPU e fora
    do Mac). So oferece quando: o motor atual e Whisper, o lote e todo em
    portugues (o TAGARELA so transcreve pt) e o usuario nunca recusou em
    definitivo. "Transcrever novamente" e escolha explicita — o chamador
    nem consulta esta funcao nesse caso. `device`/`platform` ficam por
    compatibilidade.
    """
    _ = (device, platform)
    if declined or engine == "parakeet_onnx":
        return False
    return not languages_outside_pt(langs)


def diar_offer_candidates(
    statuses: list[Any],
    *,
    edited_ids: set[str],
    channel_ids: set[str],
) -> list[str]:
    """Alvo do banner de oferta da lista (R3: instalado => aplicado).

    Entrevistas transcritas SEM separacao de vozes por nenhuma fonte
    (exclusive, regular ou canais) e SEM edicoes humanas — separar em
    lote so faz sentido quando o resultado aparece na transcricao, e
    edicoes nunca sao descartadas por acao de lote.
    """
    out: list[str] = []
    for status in statuses:
        if not (getattr(status, "canonical_exists", False)
                or getattr(status, "review_exists", False)):
            continue
        if (getattr(status, "diarization_exclusive_exists", False)
                or getattr(status, "diarization_regular_exists", False)):
            continue
        iid = getattr(status, "interview_id", "")
        if iid in channel_ids or iid in edited_ids:
            continue
        out.append(iid)
    return out


def props_metadata_updates(
    atual: dict[str, Any],
    form: dict[str, Any],
    tocados: set[str],
) -> dict[str, str]:
    """Diff do form da aba Propriedades -> updates MINIMOS (R4).

    Nunca write-back integral: outros fluxos gravam speakers_confirmed
    no MESMO CSV entre a leitura e o salvar — so os campos que o usuario
    TOCOU entram, e mesmo eles so quando diferem do atual. Paridade de
    chaves com MetadataDialog.updates()/SpeakerCountDialog (exact grava
    o trio count=min=max; range normaliza low/high; auto esvazia o
    trio). Rotulos vazios NUNCA limpam (esvaziar o campo por acidente
    nao pode descartar rotulos — limpar de fato e gesto do dialogo).

    form: language, speaker_mode, speaker_count, min_speakers,
    max_speakers, speaker_labels ("A|B"), context_text, use_context.
    tocados: {"language", "falantes", "rotulos", "contexto"}.
    """
    updates: dict[str, str] = {}
    if "language" in tocados:
        lingua = str(form.get("language") or "")
        if lingua != str(atual.get("language") or ""):
            updates["language"] = lingua
    if "falantes" in tocados:
        modo = str(form.get("speaker_mode") or "")
        if modo == "exact":
            n = str(int(form.get("speaker_count") or 0) or "")
            trio = {"speaker_count": n, "min_speakers": n, "max_speakers": n}
        elif modo == "range":
            low = int(form.get("min_speakers") or 0)
            high = int(form.get("max_speakers") or 0)
            if high < low:
                low, high = high, low
            trio = {"speaker_count": "", "min_speakers": str(low),
                    "max_speakers": str(high)}
        else:
            trio = {"speaker_count": "", "min_speakers": "", "max_speakers": ""}
        mudou = modo != str(atual.get("speaker_mode") or "") or any(
            str(atual.get(chave) or "") != valor for chave, valor in trio.items())
        if modo and mudou:
            updates["speaker_mode"] = modo
            updates.update(trio)
    if "rotulos" in tocados:
        rotulos = str(form.get("speaker_labels") or "").strip()
        if rotulos and rotulos != str(atual.get("speaker_labels") or ""):
            updates["speaker_labels"] = rotulos
    if "contexto" in tocados:
        contexto = str(form.get("context_text") or "").strip()
        usa = bool(form.get("use_context")) and bool(contexto)
        atual_ctx = str(atual.get("context_text") or "")
        atual_usa = str(atual.get("use_context_as_prompt") or "") == "true"
        if contexto != atual_ctx or usa != atual_usa:
            updates["context_mode"] = "custom" if contexto else "empty"
            updates["context_text"] = contexto
            updates["use_context_as_prompt"] = "true" if usa else "false"
    return updates


def parse_timecode(value: str) -> float:
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        raise ValueError("Informe um tempo.")
    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return (int(minutes) * 60) + float(seconds)
        if len(parts) == 1:
            return float(parts[0])
    except ValueError as exc:
        raise ValueError(f"Tempo invalido: {value}") from exc
    raise ValueError(f"Tempo invalido: {value}")


def display_speaker(turn: dict[str, Any]) -> str:
    label = str(turn.get("human_label") or turn.get("speaker") or "")
    if label.upper() == "ENTREVISTADOR":
        return "Entrevistador"
    if label.upper() == "ENTREVISTADO":
        return "Entrevistado"
    return label


def speaker_internal_label(label: str) -> str:
    if label in SPEAKER_LABELS:
        return SPEAKER_LABELS[label]
    return label.strip() or "Falante"


def _speaker_key_sort(label: str) -> tuple[int, int, str]:
    suffix = label[len("SPEAKER_"):] if label.startswith("SPEAKER_") else ""
    if suffix.isdigit():
        return (0, int(suffix), label)
    return (1, 0, label)


def ordered_speaker_keys(turns: list[dict[str, Any]]) -> list[str]:
    """SPEAKER_XX distintos dos turnos, na mesma ordem posicional usada pelo
    render para mapear speaker_labels (indice numerico, nao aparicao)."""
    seen: list[str] = []
    for turn in turns:
        key = str(turn.get("speaker") or "").strip()
        if key and key not in seen:
            seen.append(key)
    return sorted(seen, key=_speaker_key_sort)


def unlabeled_speaker_ids(turns: list[dict[str, Any]]) -> list[str]:
    """Vozes ainda sem nome humano (rotulo efetivo continua SPEAKER_NN)."""
    seen: list[str] = []
    for turn in turns:
        key = review_store.turn_speaker_key(turn)
        if key.startswith("SPEAKER_") and key[len("SPEAKER_"):].isdigit() and key not in seen:
            seen.append(key)
    return sorted(seen, key=_speaker_key_sort)


def raw_voice_ids(turns: list[dict[str, Any]]) -> list[str]:
    """Vozes cruas da diarizacao (SPEAKER_NN em turn["speaker"]).

    Exclui SPEAKER_UNKNOWN — pseudo-voz de diarizacao parcial/desligada, nao
    e uma pessoa nomeavel."""
    seen: list[str] = []
    for turn in turns:
        key = str(turn.get("speaker") or "").strip()
        if key.startswith("SPEAKER_") and key[len("SPEAKER_"):].isdigit() and key not in seen:
            seen.append(key)
    return sorted(seen, key=_speaker_key_sort)


def should_offer_voice_naming(config: dict[str, Any], file_metadata: dict[str, str] | None, turns: list[dict[str, Any]]) -> bool:
    """Gatilho puro do "De quem é esta voz?" (plano D2.5).

    O rotulo default posicional (Entrevistador/Entrevistado) NAO conta como
    confirmacao — por isso o criterio e o flag speakers_confirmed, nunca a
    presenca de nomes nos turnos (que mascarou o caso N=2)."""
    if not bool(config.get("voice_naming_prompt", True)):
        return False
    confirmed = str((file_metadata or {}).get("speakers_confirmed") or "").strip().lower()
    if confirmed == "true":
        return False
    return len(raw_voice_ids(turns)) >= 2


def voice_naming_pending(
    ids: list[str],
    config: dict[str, Any],
    metadata: dict[str, dict[str, str]],
    load_turns: Callable[[str], list[dict[str, Any]]],
) -> list[str]:
    """Entrevistas de um lote cujas vozes ainda nao foram identificadas
    (faixa "vozes por identificar", 2026-09-02).

    Mesmo criterio do dialogo "De quem é esta voz?" (should_offer_voice_naming).
    load_turns so e chamado para quem ainda nao tem speakers_confirmed; uma
    falha ao ler a transcricao vale como "nada a perguntar". Ordem do lote
    preservada."""
    pendentes: list[str] = []
    for iid in ids:
        meta = metadata.get(iid) or {}
        if str(meta.get("speakers_confirmed") or "").strip().lower() == "true":
            continue
        try:
            turns = load_turns(iid)
        except Exception:  # noqa: BLE001 - review ausente/corrompida: sem pergunta
            continue
        if should_offer_voice_naming(config, meta, turns):
            pendentes.append(iid)
    return pendentes


def dominant_speaker_key(turns: list[dict[str, Any]], speaker_id: str) -> str:
    """Rotulo (turn_speaker_key) mais frequente entre os turnos da voz crua.

    Usado no relabel em lote: turno que o usuario ja reatribuiu a mao tem key
    divergente e fica fora da aplicacao."""
    counts: dict[str, int] = {}
    for turn in turns:
        if str(turn.get("speaker") or "").strip() != speaker_id:
            continue
        key = review_store.turn_speaker_key(turn)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return speaker_id
    return max(counts, key=lambda key: counts[key])


def raw_speaker_key(turn: dict[str, Any]) -> str:
    """Voz CRUA da diarizacao (turn["speaker"]), independente do rotulo humano."""
    return str(turn.get("speaker") or "").strip()


# Identidade visual por voz (dialogo "De quem e esta voz?" e coluna Falante
# da tabela de blocos, D3.2). Paleta legivel em tema escuro e claro.
VOICE_CHIP_COLORS = list(ui_tokens.VOICE_COLORS)


def voice_color_map(turns: list[dict[str, Any]]) -> dict[str, str]:
    """Cor estavel por voz crua (ordem dos SPEAKER_NN)."""
    return {
        key: VOICE_CHIP_COLORS[index % len(VOICE_CHIP_COLORS)]
        for index, key in enumerate(ordered_speaker_keys(turns))
    }


def ids_without_speaker_setup(metadata: dict[str, dict[str, str]], ids: list[str]) -> list[str]:
    """Arquivos cuja configuracao de falantes nunca foi definida por um humano.

    O sync pre-semeia speaker_mode com defaults — por isso o criterio e o
    marcador speaker_setup (gravado pelo dialogo "Quantas pessoas falam?" e
    pelo Editar propriedades), nunca a presenca de um modo (plano D3.1)."""
    return [
        interview_id for interview_id in ids
        if str((metadata.get(interview_id) or {}).get("speaker_setup") or "").strip().lower() != "true"
    ]


def speaker_sample_clips(
    turns: list[dict[str, Any]], speaker_key: str, count: int = 3, max_seconds: float = 8.0,
    key_fn: Any = None,
) -> list[dict[str, Any]]:
    """Trechos de amostra v2 (plano D2.6): {start, end, text} por trecho.

    Exclui turnos com marcacoes suspeitas (duvida/sobreposicao — os candidatos
    a contaminacao do agrupamento) e espalha as amostras por inicio/meio/fim
    do audio: variedade que tambem torna VISIVEL um agrupamento contaminado
    (voz que muda entre amostras). Fallback: se todos sao suspeitos, usa-os."""
    resolve = key_fn or review_store.turn_speaker_key
    candidates: list[dict[str, Any]] = []
    for turn in turns:
        if resolve(turn) != speaker_key:
            continue
        start = float(turn.get("start", 0) or 0)
        end = float(turn.get("end", start) or start)
        if end <= start:
            continue
        flags = set(str(flag) for flag in (turn.get("flags") or []))
        candidates.append({
            "start": start,
            "end": end,
            "duration": end - start,
            "text": " ".join(str(turn.get("text", "")).split()),
            "suspect": bool(flags & {"duvida", "sobreposicao"}),
        })
    if not candidates:
        return []
    clean = [item for item in candidates if not item["suspect"]] or candidates
    span_start = min(item["start"] for item in clean)
    span = max(1e-9, max(item["end"] for item in clean) - span_start)
    thirds: list[list[dict[str, Any]]] = [[], [], []]
    for item in clean:
        thirds[min(2, int((item["start"] - span_start) / span * 3))].append(item)
    picked = [max(bucket, key=lambda item: item["duration"]) for bucket in thirds if bucket]
    if len(picked) < count:
        rest = sorted((item for item in clean if item not in picked), key=lambda item: item["duration"], reverse=True)
        picked.extend(rest[: count - len(picked)])
    picked = sorted(picked[:count], key=lambda item: item["start"])
    return [
        {"start": item["start"], "end": min(item["end"], item["start"] + max_seconds), "text": item["text"]}
        for item in picked
    ]


def speaker_sample_ranges(
    turns: list[dict[str, Any]], speaker_key: str, count: int = 3, max_seconds: float = 8.0,
    key_fn: Any = None,
) -> list[tuple[float, float]]:
    """Ate `count` trechos de amostra da voz: os turnos mais longos, cortados
    em max_seconds — o suficiente para reconhecer quem fala."""
    resolve = key_fn or review_store.turn_speaker_key
    candidates: list[tuple[float, float, float]] = []
    for turn in turns:
        if resolve(turn) != speaker_key:
            continue
        start = float(turn.get("start", 0) or 0)
        end = float(turn.get("end", start) or start)
        if end > start:
            candidates.append((end - start, start, end))
    candidates.sort(reverse=True)
    return [(start, min(end, start + max_seconds)) for _dur, start, end in candidates[:count]]


def speaker_talk_summary(turns: list[dict[str, Any]], speaker_key: str, key_fn: Any = None) -> tuple[float, int]:
    """(segundos falados, numero de blocos) da voz."""
    resolve = key_fn or review_store.turn_speaker_key
    seconds = 0.0
    blocks = 0
    for turn in turns:
        if resolve(turn) != speaker_key:
            continue
        start = float(turn.get("start", 0) or 0)
        end = float(turn.get("end", start) or start)
        seconds += max(0.0, end - start)
        blocks += 1
    return seconds, blocks


def order_role_suggestions(turns: list[dict[str, Any]], speaker_ids: list[str], key_fn: Any = None) -> dict[str, list[str]]:
    """Sugestoes de rotulo por voz, ORDENADAS por heuristica de papel.

    Quem fala menos e pergunta mais provavelmente conduz (Entrevistador /
    Moderador). A heuristica so ordena as sugestoes do combo — nunca rotula
    sozinha; a decisao e do usuario ouvindo as amostras (plano D2.4).
    """
    if not speaker_ids:
        return {}
    resolve = key_fn or review_store.turn_speaker_key
    stats: dict[str, dict[str, float]] = {key: {"seconds": 0.0, "blocks": 0.0, "questions": 0.0} for key in speaker_ids}
    for turn in turns:
        key = resolve(turn)
        entry = stats.get(key)
        if entry is None:
            continue
        start = float(turn.get("start", 0) or 0)
        end = float(turn.get("end", start) or start)
        entry["seconds"] += max(0.0, end - start)
        entry["blocks"] += 1
        entry["questions"] += str(turn.get("text", "")).count("?")
    total_seconds = sum(entry["seconds"] for entry in stats.values()) or 1.0

    def interviewer_score(key: str) -> float:
        entry = stats[key]
        question_rate = entry["questions"] / max(1.0, entry["blocks"])
        talk_share = entry["seconds"] / total_seconds
        return question_rate - talk_share

    likely_lead = max(speaker_ids, key=interviewer_score)
    result: dict[str, list[str]] = {}
    if len(speaker_ids) <= 2:
        for key in speaker_ids:
            result[key] = ["Entrevistador", "Entrevistado"] if key == likely_lead else ["Entrevistado", "Entrevistador"]
        return result
    participant_names = [f"Participante {index}" for index in range(1, len(speaker_ids))]
    participant = 0
    for key in speaker_ids:
        if key == likely_lead:
            result[key] = ["Moderador"] + participant_names
        else:
            participant += 1
            own = f"Participante {participant}"
            result[key] = [own, "Moderador"] + [name for name in participant_names if name != own]
    return result


def display_flags(turn: dict[str, Any]) -> str:
    flags = turn.get("flags", [])
    if not isinstance(flags, list):
        return ""
    return ", ".join(FLAG_LABELS.get(str(flag), str(flag)) for flag in flags)


def saved_status_message() -> str:
    return "Todas as alteracoes foram salvas"


def saved_status_tooltip() -> str:
    return f"Ultimo salvamento: {datetime.now().strftime('%H:%M:%S')}"


def format_job_time(value: str) -> str:
    """ISO-8601 (UTC) -> hora local HH:MM:SS legivel; passa adiante o que nao
    parsear (a fila mostrava timestamps ISO crus — plano U1.4)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%H:%M:%S")
    except ValueError:
        return text


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "estimando…"
    total = int(round(seconds))
    if total < 60:
        return f"cerca de {total}s"
    minutes = total // 60
    remaining = total % 60
    if minutes < 60:
        return f"cerca de {minutes}min {remaining:02d}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"cerca de {hours}h {minutes:02d}min"


def eta_text_for_job(job: dict, now: datetime) -> str:
    """Coluna Estimativa da fila (U1.4): tempo RESTANTE legivel, so para
    job Rodando com estimativa gravada — Concluido/Falha/Pendente ficam
    vazios (um "estimando…" eterno neles seria mentira). O relay grava
    estimated_finish_at em isoformat NAIVE LOCAL; formatos tz-aware sao
    convertidos por tolerancia (mesma postura do format_job_time)."""
    if str(job.get("status") or "") != "Rodando":
        return ""
    raw = str(job.get("estimated_finish_at") or "").strip()
    if not raw:
        return ""
    try:
        alvo = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if alvo.tzinfo is not None:
        alvo = alvo.astimezone().replace(tzinfo=None)
    return format_eta((alvo - now).total_seconds())


def safe_project_folder_name(name: str) -> str:
    safe = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in name).strip(" ._")
    return f"{safe or 'Projeto de Transcricoes'}.transcricao"


def load_waveform_peaks(path: Path, target_peaks: int = 120000) -> tuple[list[float], float]:
    try:
        with wave.open(str(path), "rb") as handle:
            frame_count = handle.getnframes()
            channel_count = max(1, handle.getnchannels())
            sample_width = handle.getsampwidth()
            frame_rate = handle.getframerate()
            duration = frame_count / frame_rate if frame_rate else 0
            if duration > 0:
                target_peaks = min(500000, max(target_peaks, int(duration * 180)))
            chunk_frames = max(1, frame_count // max(1, target_peaks))
            peaks: list[float] = []
            max_peak = 1
            while True:
                raw = handle.readframes(chunk_frames)
                if not raw:
                    break
                samples = samples_from_wave_bytes(raw, sample_width)
                if not samples:
                    continue
                peak = max(abs(value) for value in samples[::channel_count] or samples)
                peaks.append(float(peak))
                max_peak = max(max_peak, peak)
    except (wave.Error, OSError, EOFError):
        return [], 0
    return [peak / max_peak for peak in peaks], duration


def waveform_cache_path(output_root: Path, interview_id: str) -> Path:
    safe_id = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in interview_id).strip("._")
    return output_root / "00_project" / "waveforms" / f"{safe_id or 'arquivo'}.waveform.json"


def load_waveform_cache(cache_path: Path, source_path: Path) -> tuple[list[float], float] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        source_stat = source_path.stat()
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != WAVEFORM_CACHE_VERSION:
        return None
    if payload.get("source_path") != str(source_path.resolve()):
        return None
    if int(payload.get("source_size", -1)) != int(source_stat.st_size):
        return None
    if int(payload.get("source_mtime_ns", -1)) != int(source_stat.st_mtime_ns):
        return None
    peaks = payload.get("peaks")
    duration = payload.get("duration")
    if not isinstance(peaks, list):
        return None
    try:
        return [float(value) for value in peaks], float(duration or 0)
    except (TypeError, ValueError):
        return None


def save_waveform_cache(cache_path: Path, source_path: Path, peaks: list[float], duration: float) -> None:
    try:
        source_stat = source_path.stat()
    except OSError:
        return
    payload = {
        "version": WAVEFORM_CACHE_VERSION,
        "source_path": str(source_path.resolve()),
        "source_size": int(source_stat.st_size),
        "source_mtime_ns": int(source_stat.st_mtime_ns),
        "duration": float(duration),
        "peaks": peaks,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_media_waveform_peaks(path: Path, target_peaks: int = 120000, sample_rate: int = 16000) -> tuple[list[float], float]:
    command = [
        resolve_executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except OSError:
        return [], 0
    if result.returncode != 0 or not result.stdout:
        return [], 0
    samples = array("h")
    try:
        samples.frombytes(result.stdout)
    except ValueError:
        return [], 0
    if not samples:
        return [], 0
    duration = len(samples) / float(sample_rate)
    if duration > 0:
        target_peaks = min(500000, max(target_peaks, int(duration * 180)))
    chunk_size = max(1, len(samples) // max(1, target_peaks))
    peaks: list[float] = []
    max_peak = 1
    for start in range(0, len(samples), chunk_size):
        peak = max(abs(value) for value in samples[start : start + chunk_size])
        peaks.append(float(peak))
        max_peak = max(max_peak, peak)
    return [peak / max_peak for peak in peaks], duration


def samples_from_wave_bytes(raw: bytes, sample_width: int) -> list[int]:
    if sample_width == 2:
        values = array("h")
        values.frombytes(raw)
        return list(values)
    if sample_width == 1:
        return [value - 128 for value in raw]
    if sample_width == 4:
        values = array("i")
        values.frombytes(raw)
        return list(values)
    return []


if QT_IMPORT_ERROR is None:

    class TurnTextEdit(QTextEdit):
        """Editor do bloco: duplo clique numa palavra tambem leva o audio
        ate ela (fase 3; gesto escolhido pelo usuario em 2026-08-26). A
        selecao padrao da palavra pelo duplo clique e preservada via
        super()."""

        word_seek_requested = Signal(int)

        def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            super().mouseDoubleClickEvent(event)
            cursor = self.cursorForPosition(event.position().toPoint())
            self.word_seek_requested.emit(int(cursor.position()))


    class WaveformWidget(QWidget):
        seek_requested = Signal(float)

        def __init__(self) -> None:
            super().__init__()
            self.peaks: list[float] = []
            self.duration = 0.0
            self.position = 0.0
            self.edit_cursor: float | None = None
            # So um clique DIRETO na onda marca True; seeks programaticos
            # (selecionar bloco, duplo clique em palavra) deixam False —
            # o "Dividir bloco" honra o cursor apenas quando deliberado.
            self.edit_cursor_from_click = False
            self.selected_range: tuple[float, float] | None = None
            self.active_range: tuple[float, float] | None = None
            self.zoom = 1.0
            self.visible_start = 0.0
            self.word_ticks: list[tuple[float, bool]] = []
            self._word_starts: list[float] = []
            self._drag_start_x: float | None = None
            self._drag_start_visible_start = 0.0
            self._drag_moved = False
            self.setMinimumHeight(96)
            self.setAccessibleName("Onda sonora")
            self.setAccessibleDescription(
                "Linha do tempo do áudio. Clique para mover o áudio, arraste para navegar e use a roda do mouse para aproximar."
            )
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setToolTip(
                "Clique para mover o áudio. Arraste para navegar na onda sem mover o áudio. "
                "Use a roda do mouse para aproximar ou afastar."
            )

        def set_waveform(self, peaks: list[float], duration: float) -> None:
            self.peaks = peaks
            self.duration = duration
            self.position = 0.0
            self.edit_cursor = None
            self.edit_cursor_from_click = False
            self.selected_range = None
            self.active_range = None
            self.zoom = 1.0
            self.visible_start = 0.0
            self.word_ticks = []
            self._word_starts = []
            self._drag_start_x = None
            self._drag_start_visible_start = 0.0
            self._drag_moved = False
            self.update()

        def set_words(self, ticks: list[tuple[float, bool]]) -> None:
            """Ticks de palavras (start, posicao_incerta) — fase 3."""
            self.word_ticks = sorted(ticks)
            self._word_starts = [tick[0] for tick in self.word_ticks]
            self.update()

        def set_position(self, seconds: float) -> None:
            self.position = seconds
            if self.zoom > 1.0 and self.duration > 0:
                visible_end = self.visible_start + self.visible_duration()
                if seconds < self.visible_start or seconds > visible_end:
                    self.center_on(seconds)
            self.update()

        def set_edit_cursor(self, seconds: float | None, *, from_click: bool = False) -> None:
            if seconds is None or self.duration <= 0:
                self.edit_cursor = None
                self.edit_cursor_from_click = False
            else:
                self.edit_cursor = max(0.0, min(self.duration, float(seconds)))
                self.edit_cursor_from_click = bool(from_click)
            self.update()

        def set_selected_range(self, start: float | None, end: float | None) -> None:
            self.selected_range = self.normalized_range(start, end)
            self.update()

        def set_active_range(self, start: float | None, end: float | None) -> None:
            self.active_range = self.normalized_range(start, end)
            self.update()

        def normalized_range(self, start: float | None, end: float | None) -> tuple[float, float] | None:
            if self.duration <= 0 or start is None or end is None:
                return None
            left = max(0.0, min(self.duration, float(start)))
            right = max(0.0, min(self.duration, float(end)))
            if right <= left:
                return None
            return (left, right)

        def visible_duration(self) -> float:
            if self.duration <= 0:
                return 0.0
            return self.duration / max(1.0, self.zoom)

        def visible_end(self) -> float:
            return min(self.duration, self.visible_start + self.visible_duration())

        def zoom_in(self) -> None:
            self.set_zoom(self.zoom * 2)

        def zoom_out(self) -> None:
            self.set_zoom(self.zoom / 2)

        def fit_all(self) -> None:
            self.zoom = 1.0
            self.visible_start = 0.0
            self.update()

        def center_on_playhead(self) -> None:
            self.center_on(self.position)
            self.update()

        def center_on(self, seconds: float) -> None:
            visible_duration = self.visible_duration()
            self.visible_start = seconds - (visible_duration / 2)
            self.clamp_visible_start()

        def zoom_to_range(self, start: float, end: float) -> None:
            if self.duration <= 0 or end <= start:
                return
            target_duration = min(self.duration, max(3.0, (end - start) * 1.6))
            self.zoom = max(1.0, min(128.0, self.duration / target_duration))
            self.visible_start = start - ((target_duration - (end - start)) / 2)
            self.clamp_visible_start()
            self.update()

        def set_zoom(self, value: float) -> None:
            if self.duration <= 0:
                return
            center = self.position if self.visible_start <= self.position <= self.visible_end() else self.visible_start + (self.visible_duration() / 2)
            self.zoom = max(1.0, min(128.0, value))
            self.center_on(center)
            self.update()

        def set_zoom_at(self, value: float, anchor_seconds: float, anchor_fraction: float) -> None:
            if self.duration <= 0:
                return
            self.zoom = max(1.0, min(128.0, value))
            self.visible_start = anchor_seconds - (anchor_fraction * self.visible_duration())
            self.clamp_visible_start()
            self.update()

        def pan_by_pixels(self, delta_x: float) -> None:
            if self.duration <= 0 or self.zoom <= 1.0:
                return
            self.visible_start += (delta_x / max(1, self.width())) * self.visible_duration()
            self.clamp_visible_start()
            self.update()

        def clamp_visible_start(self) -> None:
            if self.duration <= 0:
                self.visible_start = 0.0
                return
            max_start = max(0.0, self.duration - self.visible_duration())
            self.visible_start = max(0.0, min(self.visible_start, max_start))

        def paintEvent(self, _event: Any) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            painter.fillRect(rect, QColor(ui_tokens.WAVEFORM["bg"]))
            if not self.peaks:
                painter.setPen(QColor(ui_tokens.WAVEFORM["ruler_text"]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Onda sonora indisponível; prepare o WAV para esta entrevista.")
                return
            width = max(1, rect.width())
            height = max(1, rect.height())
            ruler_height = 22
            wave_height = max(1, height - ruler_height)
            center = ruler_height + (wave_height // 2)
            visible_duration = max(0.001, self.visible_duration())
            visible_start = self.visible_start
            visible_end = self.visible_end()

            def seconds_to_x(seconds: float) -> int:
                bounded = max(visible_start, min(seconds, visible_end))
                return int(((bounded - visible_start) / visible_duration) * width)

            def draw_range(time_range: tuple[float, float] | None, color: QColor) -> None:
                if not time_range:
                    return
                start, end = time_range
                if end < visible_start or start > visible_end:
                    return
                left = seconds_to_x(start)
                right = seconds_to_x(end)
                painter.fillRect(left, ruler_height, max(2, right - left), wave_height, color)

            draw_range(self.selected_range, QColor(255, 255, 255, 28))
            draw_range(self.active_range, QColor(26, 115, 232, 48))

            painter.setPen(QPen(QColor(ui_tokens.WAVEFORM["grid"]), 1))
            painter.drawLine(0, ruler_height - 1, width, ruler_height - 1)
            tick_count = 6 if width >= 420 else 4
            for index in range(tick_count + 1):
                fraction = index / max(1, tick_count)
                x = int(fraction * width)
                seconds = visible_start + (fraction * visible_duration)
                painter.drawLine(x, ruler_height - 7, x, ruler_height - 1)
                painter.setPen(QColor(ui_tokens.WAVEFORM["label"]))
                painter.drawText(x + 3, 14, format_clock(seconds))
                painter.setPen(QPen(QColor(ui_tokens.WAVEFORM["grid"]), 1))

            waveform_path = QPainterPath()
            bottom_points: list[QPointF] = []
            waveform_path.moveTo(0, center)
            for x in range(width):
                start_seconds = visible_start + ((x / width) * visible_duration)
                end_seconds = visible_start + (((x + 1) / width) * visible_duration)
                peak = self.peak_between(start_seconds, end_seconds)
                half = max(1.0, (wave_height * 0.45) * peak)
                top = QPointF(float(x), center - half)
                bottom_points.append(QPointF(float(x), center + half))
                if x == 0:
                    waveform_path.moveTo(top)
                else:
                    waveform_path.lineTo(top)
            for point in reversed(bottom_points):
                waveform_path.lineTo(point)
            waveform_path.closeSubpath()
            painter.setPen(QPen(QColor(ui_tokens.WAVEFORM["cursor_line"]), 1))
            painter.setBrush(QBrush(QColor(ui_tokens.WAVEFORM["cursor_fill"])))
            painter.drawPath(waveform_path)
            if self._word_starts and self.duration > 0:
                low = bisect_left(self._word_starts, visible_start)
                high = bisect_left(self._word_starts, visible_end)
                count = high - low
                # Ticks de inicio de palavra (fase 3), logo abaixo da regua.
                # Guarda de densidade: so com >= ~6 px por palavra visivel;
                # afastado, virariam ruido continuo. Ambar = score do decil
                # inferior do alinhamento (posicao incerta).
                if count and (width / count) >= 6:
                    for tick_start, uncertain in self.word_ticks[low:high]:
                        x = seconds_to_x(tick_start)
                        painter.setPen(QPen(
                            QColor(ui_tokens.WAVEFORM["tick_uncertain"]) if uncertain else QColor(ui_tokens.WAVEFORM["tick"]), 1))
                        painter.drawLine(x, ruler_height + 1, x, ruler_height + 9)
            if self.duration > 0:
                if self.edit_cursor is not None and visible_start <= self.edit_cursor <= visible_end:
                    cursor_x = seconds_to_x(self.edit_cursor)
                    painter.setPen(QPen(QColor(ui_tokens.WAVEFORM["block_dash"]), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(cursor_x, ruler_height, cursor_x, height)
                play_x = seconds_to_x(self.position)
                painter.setPen(QPen(QColor(ui_tokens.WAVEFORM["range"]), 2))
                painter.drawLine(play_x, ruler_height, play_x, height)
                painter.setPen(QColor(ui_tokens.WAVEFORM["time_text"]))
                painter.drawText(8, height - 8, f"{format_timecode(visible_start)} - {format_timecode(visible_end)}   zoom {self.zoom:.0f}x")

        def peak_between(self, start_seconds: float, end_seconds: float) -> float:
            if not self.peaks or self.duration <= 0:
                return 0.0
            peak_count = len(self.peaks)
            start_index_float = (max(0.0, start_seconds) / self.duration) * peak_count
            end_index_float = (min(self.duration, end_seconds) / self.duration) * peak_count
            start_index = max(0, min(peak_count - 1, int(start_index_float)))
            end_index = max(start_index + 1, min(peak_count, int(end_index_float) + 1))
            if end_index - start_index <= 2:
                left = self.peaks[start_index]
                right = self.peaks[min(peak_count - 1, start_index + 1)]
                fraction = max(0.0, min(1.0, start_index_float - start_index))
                return left + ((right - left) * fraction)
            return max(self.peaks[start_index:end_index])

        def mousePressEvent(self, event: Any) -> None:
            if self.duration <= 0:
                return
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_start_x = float(event.position().x())
                self._drag_start_visible_start = self.visible_start
                self._drag_moved = False
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()

        def mouseMoveEvent(self, event: Any) -> None:
            if self._drag_start_x is None or self.duration <= 0:
                return
            delta_x = float(event.position().x()) - self._drag_start_x
            if abs(delta_x) >= 3:
                self._drag_moved = True
            if self._drag_moved:
                self.visible_start = self._drag_start_visible_start - ((delta_x / max(1, self.width())) * self.visible_duration())
                self.clamp_visible_start()
                self.update()
                event.accept()

        def mouseReleaseEvent(self, event: Any) -> None:
            if self.duration <= 0:
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if not self._drag_moved:
                    fraction = max(0.0, min(1.0, event.position().x() / max(1, self.width())))
                    seconds = self.visible_start + (fraction * self.visible_duration())
                    # Emitir ANTES: o seek re-seta o cursor programaticamente
                    # (seek_waveform) e apagaria a marca de clique deliberado.
                    self.seek_requested.emit(seconds)
                    self.set_edit_cursor(seconds, from_click=True)
                self._drag_start_x = None
                self._drag_moved = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                event.accept()

        def wheelEvent(self, event: Any) -> None:
            if self.duration <= 0:
                return
            delta = event.angleDelta()
            if delta.x() and abs(delta.x()) > abs(delta.y()):
                self.pan_by_pixels(-(delta.x() / 120) * self.width() * 0.08)
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.pan_by_pixels(-(delta.y() / 120) * self.width() * 0.08)
                event.accept()
                return
            if not delta.y():
                return
            fraction = max(0.0, min(1.0, event.position().x() / max(1, self.width())))
            anchor_seconds = self.visible_start + (fraction * self.visible_duration())
            factor = 1.25 ** (delta.y() / 120)
            self.set_zoom_at(self.zoom * factor, anchor_seconds, fraction)
            event.accept()


    def _pipeline_weights(model: str, device: str) -> list[int]:
        """Return empirical progress weights [prepare, asr, diarize, render, qc].

        Based on exhaustive benchmark (tests/benchmark_exhaustive_2026-04-19.csv).
        Weights approximate % of total wall-clock time per stage.
        """
        # 2026-09-02: a separacao de falantes ficou ~8x mais rapida (rede 1x
        # por janela + passo de 2 s: 0,06x em CPU, 0,0065x em GPU) — o peso
        # migrou para a transcricao em quase todas as combinacoes.
        _CUDA: dict[str, list[int]] = {
            "tiny":           [5, 75, 19, 1, 0],
            "base":           [5, 75, 19, 1, 0],
            "small":          [4, 80, 15, 1, 0],
            "medium":         [4, 85, 10, 1, 0],
            "large-v3-turbo": [4, 90, 5, 1, 0],
            "large-v3":       [3, 92, 4, 1, 0],
            # TAGARELA: ~62x com a aceleracao onnx-gpu, senao em CPU (16,5x)
            # dentro da maquina CUDA — a transcricao domina nos dois casos.
            "parakeet-pt":    [3, 70, 25, 1, 1],
        }
        _CPU: dict[str, list[int]] = {
            "tiny":           [2, 75, 22, 1, 0],
            "base":           [2, 75, 22, 1, 0],
            "small":          [1, 93, 5, 1, 0],
            "medium":         [1, 93, 5, 1, 0],
            "large-v3-turbo": [2, 94, 4, 0, 0],
            "large-v3":       [1, 94, 4, 1, 0],
            # TAGARELA 16,5x tempo real (0,06x) e diarizacao 0,06x: empate.
            "parakeet-pt":    [1, 48, 49, 1, 1],
        }
        table = _CUDA if device == "cuda" else _CPU
        return table.get(model, table.get("parakeet-pt", [1, 48, 49, 1, 1]))

    class PipelineWorker(QThread):
        progress = Signal(str, int)
        finished_ok = Signal(str)
        failed = Signal(str)

        def __init__(self, label: str, steps: list[tuple], weights: list[int] | None = None) -> None:
            super().__init__()
            self.label = label
            self.steps = steps
            self.weights = weights or [1] * len(steps)
            self.cancel_after_step = False
            self.started_monotonic = time.monotonic()
            # Arquivos do lote (grupos), na ordem: "Arquivo k de n" na barra.
            self._groups: list[str] = []
            for step in steps:
                grupo = str(step[3]) if len(step) >= 4 and step[3] else None
                if grupo and grupo not in self._groups:
                    self._groups.append(grupo)

        def label_for(self, index: int, group: str | None, atividade: str, percent: int) -> str:
            """Texto da barra: por arquivo num lote; "Etapa i de N" nos jobs
            de passo unico (sem grupo)."""
            if group is None or group not in self._groups:
                return f"Etapa {index} de {len(self.steps)}: {atividade}"
            remaining = None
            elapsed = time.monotonic() - self.started_monotonic
            if percent >= 3 and elapsed >= 8:
                remaining = elapsed * (100 - percent) / max(1, percent)
            return batch_label(self._groups.index(group) + 1, len(self._groups), group, atividade, remaining)

        def request_cancel_after_step(self) -> None:
            self.cancel_after_step = True

        def is_cancel_requested(self) -> bool:
            return self.cancel_after_step

        def run(self) -> None:
            try:
                total_weight = max(1, sum(max(1, weight) for weight in self.weights))
                completed_weight = 0
                # Skip-and-continue: falha num step COM grupo (interview_id)
                # pula os steps restantes DAQUELE arquivo e o lote segue;
                # steps SEM grupo (jobs de passo unico) mantem o aborto.
                seen_groups: set[str] = set()
                failed_groups: set[str] = set()
                group_errors: dict[str, str] = {}  # primeiro erro por arquivo
                for index, step in enumerate(self.steps, start=1):
                    if self.cancel_after_step:
                        self.finished_ok.emit(f"{self.label} cancelado.")
                        return
                    message, func, accepts_progress, group = self.unpack_step(step)
                    if group is not None:
                        seen_groups.add(group)
                    weight = max(1, self.weights[index - 1] if index - 1 < len(self.weights) else 1)
                    start_percent = int((completed_weight / total_weight) * 100)
                    end_percent = int(((completed_weight + weight) / total_weight) * 100)
                    if group is not None and group in failed_groups:
                        completed_weight += weight  # barra nao congela
                        continue
                    self.progress.emit(self.label_for(index, group, message, start_percent), start_percent)
                    try:
                        if accepts_progress:
                            result = func(
                                self.step_progress_callback(index, len(self.steps), message, start_percent, end_percent, group),
                                self.is_cancel_requested,
                            )
                        else:
                            result = func()
                        failures = getattr(result, "failures", 0)
                        if failures:
                            raise RuntimeError(
                                f"{message}: {failure_summary(failures, getattr(result, 'message', ''))}")
                    except Exception as exc:
                        if self.cancel_after_step:
                            self.finished_ok.emit(f"{self.label} cancelado.")
                            return
                        if group is None:
                            raise
                        failed_groups.add(group)
                        group_errors.setdefault(group, sanitize_message(str(exc)))
                        completed_weight += weight
                        self.progress.emit(
                            self.label_for(index, group, f"{message} — falhou; continuando com o próximo arquivo.", end_percent),
                            end_percent,
                        )
                        _logger.warning("Arquivo %s falhou no lote: %s", group, exc)
                        continue
                    completed_weight += weight
                    self.progress.emit(
                        self.label_for(index, group, message, end_percent) if group is not None
                        else f"Etapa {index} de {len(self.steps)} concluída: {message}",
                        end_percent)
                    if self.cancel_after_step and index < len(self.steps):
                        self.finished_ok.emit(f"{self.label} interrompido apos a etapa atual.")
                        return
                if failed_groups:
                    resumo = (f"{self.label} concluído com {len(failed_groups)} "
                              "arquivo(s) com falha — veja a coluna Transcrição "
                              "e a fila de processamento (Ferramentas).")
                    if seen_groups and failed_groups >= seen_groups:
                        # Todos falharam: vermelho, COM a causa real (num lote
                        # de 1 arquivo o resumo generico escondia o erro).
                        primeiro = next(iter(group_errors.values()), "")
                        if primeiro:
                            resumo = f"{resumo}\n\nPrimeiro erro: {primeiro}"
                        self.failed.emit(resumo)
                        return
                    self.progress.emit(resumo, 100)
                    self.finished_ok.emit(resumo)
                    return
                self.progress.emit(f"{self.label} concluido.", 100)
                self.finished_ok.emit(f"{self.label} concluido.")
            except Exception as exc:  # GUI boundary
                self.failed.emit(sanitize_message(str(exc)))

        def unpack_step(self, step: tuple) -> tuple[str, Callable, bool, str | None]:
            group = str(step[3]) if len(step) >= 4 and step[3] else None
            if len(step) >= 3:
                return str(step[0]), step[1], bool(step[2]), group
            return str(step[0]), step[1], False, None

        def step_progress_callback(self, index: int, total: int, message: str, start_percent: int, end_percent: int,
                                   group: str | None = None) -> Callable[[dict[str, Any]], None]:
            def callback(detail: dict[str, Any]) -> None:
                progress_value = detail.get("progress")
                try:
                    inner_percent = max(0, min(100, int(progress_value)))
                except (TypeError, ValueError):
                    inner_percent = 0
                percent = start_percent + int(((end_percent - start_percent) * inner_percent) / 100)
                event = detail.get("event", "")
                detail_message = detail.get("message")
                if detail_message and event in ("model_download_bytes", "model_download_start", "model_download_done", "model_download_error", "model_download_retry"):
                    label = str(detail_message)
                elif detail_message and event in ("diarize_progress", "asr_progress", "prepare_progress"):
                    # asr_progress entrou em 2026-09-02: a mensagem honesta
                    # da fase silenciosa em CPU (whisperx_runner) nunca
                    # chegava a statusbar — caia no rotulo generico do step.
                    # prepare_progress: "Preparando os áudios (k de n)" do
                    # preparo em paralelo (B2).
                    label = str(detail_message)
                else:
                    label = message
                self.progress.emit(self.label_for(index, group, label, percent), percent)

            return callback


    class TrashMoveWorker(QThread):
        """Copia arquivos para 00_project/.trash/<id>/staging/, renomeia para
        files/, e escreve undo.json. NAO reescreve CSVs nem deleta originais
        — isso fica para a main thread apos finished_result."""
        progress = Signal(int, int, str)       # current, total, current_name
        stage_changed = Signal(str)            # "Movendo: ..." | "Baixando do Dropbox: ..."
        finished_result = Signal(object, str)  # (entry_dict_or_None, error_str)

        CLOUD_REPARSE_MASK = 0x9000001A

        def __init__(self, trash_entry: dict) -> None:
            super().__init__()
            self.entry = dict(trash_entry)
            self._cancel_requested = False

        def request_cancel(self) -> None:
            self._cancel_requested = True

        def is_cancel_requested(self) -> bool:
            return self._cancel_requested

        def _is_cloud_only(self, path: Path) -> bool:
            try:
                st = path.stat()
                tag = getattr(st, "st_reparse_tag", 0)
                return bool(tag) and (tag & 0x9000FFFF) == self.CLOUD_REPARSE_MASK
            except OSError:
                return False

        def run(self) -> None:
            import shutil
            from datetime import datetime
            from pathlib import Path as _Path
            trash_dir = _Path(self.entry["trash_dir"])
            staging = trash_dir / "staging"
            files_to_move = list(self.entry.get("files_to_move") or [])
            project_root = _Path(self.entry["project_root"])
            total = len(files_to_move)
            try:
                staging.mkdir(parents=True, exist_ok=True)
                moved_files: list[dict] = []
                for idx, mf in enumerate(files_to_move, start=1):
                    if self._cancel_requested:
                        shutil.rmtree(trash_dir, ignore_errors=True)
                        self.finished_result.emit(None, "cancelado")
                        return
                    src = _Path(mf["original"])
                    if not src.exists():
                        continue
                    name = src.name
                    if self._is_cloud_only(src):
                        self.stage_changed.emit(f"Baixando do Dropbox: {name}")
                    else:
                        self.stage_changed.emit(f"Movendo: {name} ({idx}/{total})")
                    self.progress.emit(idx, total, name)
                    # Preserve a relative layout under staging to avoid name collisions
                    try:
                        rel = src.resolve().relative_to(project_root.resolve())
                        dest = staging / rel
                    except ValueError:
                        # File is outside project_root — use filename only
                        dest = staging / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # If dest exists (duplicate filename across sources), suffix it
                    if dest.exists():
                        stem = dest.stem
                        suffix = dest.suffix
                        counter = 1
                        while (dest.parent / f"{stem}__{counter}{suffix}").exists():
                            counter += 1
                        dest = dest.parent / f"{stem}__{counter}{suffix}"
                    shutil.copy2(str(src), str(dest))
                    # Validate size
                    src_size = src.stat().st_size
                    dest_size = dest.stat().st_size
                    if src_size != dest_size:
                        raise RuntimeError(f"tamanho divergente apos copy: {name}")
                    trashed_rel = str(dest.relative_to(trash_dir)).replace("\\", "/")
                    moved_files.append({
                        "original": str(src.resolve()),
                        "trashed": trashed_rel,
                        "size": int(src_size),
                        "mtime": float(src.stat().st_mtime),
                    })
                if self._cancel_requested:
                    shutil.rmtree(trash_dir, ignore_errors=True)
                    self.finished_result.emit(None, "cancelado")
                    return
                # Promover staging -> files (tolerante ao lock do Dropbox)
                files_dir = trash_dir / "files"
                _promote_staging_to_files(staging, files_dir)
                # Ajustar trashed paths: "staging/..." -> "files/..."
                for mf in moved_files:
                    mf["trashed"] = mf["trashed"].replace("staging/", "files/", 1)
                # Escrever undo.json (apos rename OK)
                entry_dict = project_store._build_undo_entry(
                    trash_id=self.entry["trash_id"],
                    interview_ids=self.entry["interview_ids"],
                    csv_mtimes=self.entry.get("csv_mtimes") or {},
                    snapshots=self.entry.get("snapshots") or {},
                    moved_files=moved_files,
                    status="complete",
                )
                entry_dict["project_root"] = str(project_root)
                from .utils import write_json as _write_json
                _write_json(trash_dir / project_store.TRASH_MANIFEST, entry_dict)
                entry_dict["trash_dir"] = str(trash_dir)
                self.finished_result.emit(entry_dict, "")
            except Exception as exc:  # GUI boundary
                shutil.rmtree(trash_dir, ignore_errors=True)
                self.finished_result.emit(None, str(exc)[:500])


    class ReviewSnapshotCommand(QUndoCommand):
        def __init__(
            self,
            window: "ReviewStudioWindow",
            label: str,
            before: dict[str, Any],
            after: dict[str, Any],
            selected_turn_id: str | None,
        ) -> None:
            super().__init__(label)
            self.window = window
            self.before = deepcopy(before)
            self.after = deepcopy(after)
            self.selected_turn_id = selected_turn_id
            self._first_redo = True

        def undo(self) -> None:
            self.window.restore_review_snapshot(self.before, self.selected_turn_id)

        def redo(self) -> None:
            if self._first_redo:
                self._first_redo = False
                return
            self.window.restore_review_snapshot(self.after, self.selected_turn_id)


    class ExportDialog(QDialog):
        """Dialog de exportacao com escopo auto-detectado.

        Regras:
          - n_selected > 0           -> escopo = selected (titulo lista N)
          - senao, has_open           -> escopo = current
          - senao                    -> escopo = all (com confirmacao obrigatoria se N>=20)
        Link "Alterar escopo" expoe combo para trocar manualmente.
        """
        LARGE_EXPORT_THRESHOLD = 20

        def __init__(
            self,
            has_open: bool = False,
            open_title: str = "",
            n_selected: int = 0,
            n_total: int = 0,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self._n_total = int(n_total)
            # Escopo auto
            if n_selected > 0:
                default_scope = "selected"
                title = f"Exportar {n_selected} transcricoes selecionadas"
            elif has_open:
                default_scope = "current"
                title = f"Exportar: {open_title}" if open_title else "Exportar transcrição aberta"
            else:
                default_scope = "all"
                title = f"Exportar todas ({n_total}) transcricoes"
            self.setWindowTitle(title)
            layout = QVBoxLayout(self)

            # Escopo oculto por default; exposto via link "Alterar escopo"
            self.scope_combo = QComboBox()
            entries: list[tuple[str, str]] = []
            if has_open:
                entries.append(("current", f"Arquivo aberto: {open_title or '-'}"))
            if n_selected > 0:
                entries.append(("selected", f"{n_selected} arquivos selecionados"))
            entries.append(("all", f"Todas ({n_total}) transcricoes do projeto"))
            for value, label in entries:
                self.scope_combo.addItem(label, value)
            self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(default_scope)))
            self.scope_row = QWidget()
            scope_layout = QHBoxLayout(self.scope_row)
            scope_layout.setContentsMargins(0, 0, 0, 0)
            scope_layout.addWidget(QLabel("O que exportar:"))
            scope_layout.addWidget(self.scope_combo, stretch=1)
            self.scope_row.setVisible(False)
            layout.addWidget(self.scope_row)

            change_scope_link = QLabel('<a href="#">Alterar escopo</a>')
            change_scope_link.setStyleSheet(_style_muted())
            change_scope_link.linkActivated.connect(lambda _: self.scope_row.setVisible(True))
            if len(entries) > 1:
                layout.addWidget(change_scope_link)

            layout.addWidget(QLabel("Formatos:"))
            self.checkboxes: dict[str, QCheckBox] = {}
            for fmt, label, checked, help_text in [
                ("docx", "DOCX", True, "Documento para leitura e revisão fora do app."),
                ("md", "Markdown", True, "Texto simples com marcacao leve."),
                ("srt", "SRT", False, "Legenda com tempos por bloco."),
                ("vtt", "VTT", False, "Legenda web com tempos por bloco."),
                ("csv", "CSV", False, "Planilha com turnos e metadados."),
                ("tsv", "TSV", False, "Planilha tabulada com turnos e metadados."),
                ("nvivo", "NVivo TSV", False, "Tabela tabulada para importação no NVivo."),
            ]:
                checkbox = QCheckBox(label)
                checkbox.setChecked(checked)
                checkbox.setToolTip(help_text)
                self.checkboxes[fmt] = checkbox
                layout.addWidget(checkbox)
            hint = QLabel("DOCX e Markdown são os padrões de leitura. Legendas e planilhas ficam desmarcadas para evitar arquivos que você não pediu.")
            hint.setWordWrap(True)
            hint.setStyleSheet(_style_muted())
            layout.addWidget(hint)

            # Confirmacao obrigatoria para exports grandes (all com N >= THRESHOLD)
            self.large_confirm: QCheckBox | None = None
            if default_scope == "all" and n_total >= self.LARGE_EXPORT_THRESHOLD:
                self.large_confirm = QCheckBox(f"Confirmo gerar arquivos para {n_total} transcricoes")
                self.large_confirm.setStyleSheet(_style_warn())
                layout.addWidget(self.large_confirm)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
            buttons.accepted.connect(self._maybe_accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            # Se ha confirm de lote grande, OK comeca desabilitado
            if self.large_confirm is not None:
                self._ok_btn.setEnabled(False)
                self.large_confirm.toggled.connect(self._ok_btn.setEnabled)
            # Se escopo for alterado via combo para "all" N>=threshold, reavalie
            self.scope_combo.currentIndexChanged.connect(self._reevaluate_confirm)

        def _reevaluate_confirm(self) -> None:
            scope = self.selected_scope()
            needs = scope == "all" and self._n_total >= self.LARGE_EXPORT_THRESHOLD
            if needs and self.large_confirm is None:
                # Adicionar checkbox sob demanda nao e trivial aqui; apenas re-habilita OK
                # via confirmacao implicita (click direto em OK seguido de AskQuestion).
                pass
            if self.large_confirm is not None:
                self._ok_btn.setEnabled((not needs) or self.large_confirm.isChecked())

        def _maybe_accept(self) -> None:
            # Pergunta final se escopo = all com N>=threshold e nao ha checkbox explicito
            scope = self.selected_scope()
            if scope == "all" and self._n_total >= self.LARGE_EXPORT_THRESHOLD and self.large_confirm is None:
                reply = QMessageBox.question(
                    self,
                    "Exportar todas as transcrições",
                    f"Voce esta prestes a gerar arquivos para {self._n_total} transcricoes.\nContinuar?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self.accept()

        def selected_scope(self) -> str:
            return str(self.scope_combo.currentData())

        def selected_formats(self) -> list[str]:
            return [fmt for fmt, checkbox in self.checkboxes.items() if checkbox.isChecked()]


    _FORMAT_LABELS = {
        ".docx": "Word",
        ".md": "Markdown",
        ".srt": "Legenda SRT",
        ".vtt": "Legenda VTT",
        ".csv": "Planilha CSV",
        ".tsv": "Planilha TSV",
        ".txt": "Texto",
    }


    def _format_bytes(n: int) -> str:
        for unit, threshold in [("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3)]:
            if n < threshold * 1024:
                return f"{n / threshold:.1f} {unit}"
        return f"{n / (1024 ** 4):.1f} TB"


    class ExportResultDialog(QDialog):
        """Dialog pos-export: lista clicavel de arquivos gerados + acoes."""

        def __init__(
            self,
            exported_paths: list[Path],
            skipped_ids: list[str],
            results_folder: Path,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.exported_paths = [Path(p) for p in exported_paths]
            self.skipped_ids = list(skipped_ids)
            self.results_folder = Path(results_folder)
            n = len(self.exported_paths)
            self.setWindowTitle("Exportação concluída")
            self.resize(640, 440)

            layout = QVBoxLayout(self)
            title_text = f"{n} transcricao exportada" if n == 1 else f"{n} transcricoes exportadas"
            title = QLabel(title_text)
            title.setStyleSheet("font-size: 15px; font-weight: 700;")
            layout.addWidget(title)

            subtitle = QLabel(f"Pasta: {self.results_folder}")
            subtitle.setStyleSheet(_style_muted())
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

            self.list = QListWidget()
            for p in self.exported_paths:
                fmt_label = _FORMAT_LABELS.get(p.suffix.lower(), p.suffix.lstrip(".").upper() or "Arquivo")
                try:
                    size = p.stat().st_size
                    size_str = _format_bytes(size)
                except OSError:
                    size_str = "?"
                item = QListWidgetItem(f"{p.name}  —  {size_str}  ·  {fmt_label}")
                item.setData(Qt.ItemDataRole.UserRole, str(p))
                item.setToolTip(str(p))
                self.list.addItem(item)
            self.list.itemActivated.connect(self._open_file)
            layout.addWidget(self.list, stretch=1)

            if self.skipped_ids:
                warn = QLabel(f"{len(self.skipped_ids)} arquivo(s) sem transcricao exportavel — ignorado(s).")
                warn.setStyleSheet(_style_warn())
                warn.setWordWrap(True)
                layout.addWidget(warn)

            btn_row = QHBoxLayout()
            self.open_folder_btn = QPushButton("Abrir pasta")
            self.open_folder_btn.clicked.connect(self._open_folder)
            btn_row.addWidget(self.open_folder_btn)

            if sys.platform == "win32":
                self.show_in_explorer_btn = QPushButton("Mostrar no Explorer")
                self.show_in_explorer_btn.clicked.connect(self._show_in_explorer)
                btn_row.addWidget(self.show_in_explorer_btn)

            self.copy_path_btn = QPushButton("Copiar caminho")
            self.copy_path_btn.clicked.connect(self._copy_path)
            btn_row.addWidget(self.copy_path_btn)

            btn_row.addStretch(1)
            close_btn = QPushButton("Fechar")
            close_btn.clicked.connect(self.accept)
            close_btn.setDefault(True)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)

        def _selected_path(self) -> Path | None:
            item = self.list.currentItem()
            if item is None:
                return None
            data = item.data(Qt.ItemDataRole.UserRole)
            return Path(str(data)) if data else None

        def _open_file(self, item: QListWidgetItem) -> None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                return
            p = Path(str(data))
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])

        def _open_folder(self) -> None:
            open_folder_in_explorer(self.results_folder)

        def _show_in_explorer(self) -> None:
            target = self._selected_path() or self.results_folder
            if sys.platform == "win32":
                # Linha de comando pronta, com aspas SO no caminho: como lista,
                # o list2cmdline envolvia o token inteiro em aspas quando o
                # caminho tinha virgula/espaco e o Explorer abria Documentos
                # (auditoria 2026-09-02). Nomes Windows nunca contem aspas.
                subprocess.Popen(f'explorer /select,"{target}"')

        def _copy_path(self) -> None:
            target = self._selected_path() or self.results_folder
            QApplication.clipboard().setText(str(target))


    class ModelManagerDialog(QDialog):
        """Gerenciador de modelos: ver tamanho real, remover, baixar, trocar token.

        Substitui o ModelStatusDialog read-only antigo.
        """

        COL_NAME = 0
        COL_SIZE = 1
        COL_STATUS = 2
        COL_DATE = 3
        COL_ACTION = 4

        def __init__(self, context_provider, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._context_provider = context_provider  # callable -> ProjectContext | None
            self.setWindowTitle("Gerenciar modelos")
            self.resize(780, 520)
            layout = QVBoxLayout(self)

            header = QLabel("Modelos locais de transcrição, separação de falantes e AI")
            header.setStyleSheet("font-size: 14px; font-weight: 700;")
            layout.addWidget(header)

            subtitle = QLabel(
                "Os modelos ficam em cache local e são reutilizados entre projetos. "
                "Remoção libera espaço em disco; você poderá baixar novamente a qualquer momento."
            )
            subtitle.setStyleSheet(_style_muted())
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["Modelo", "Tamanho", "Status", "Baixado em", ""])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in (1, 2, 3, 4):
                self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            self.table.verticalHeader().setVisible(False)
            layout.addWidget(self.table, stretch=1)

            self.summary_label = QLabel("")
            self.summary_label.setStyleSheet(_style_muted())
            layout.addWidget(self.summary_label)

            btn_row = QHBoxLayout()
            self.open_folder_btn = QPushButton("Abrir pasta de modelos")
            self.open_folder_btn.clicked.connect(self._open_models_folder)
            btn_row.addWidget(self.open_folder_btn)
            self.remove_orphans_btn = QPushButton("Remover órfãos")
            self.remove_orphans_btn.setToolTip("Apagar modelos em cache que não estão mais catalogados pelo Transcritório.")
            self.remove_orphans_btn.clicked.connect(self._remove_orphans)
            btn_row.addWidget(self.remove_orphans_btn)
            self.download_btn = QPushButton("Baixar outros modelos…")
            self.download_btn.clicked.connect(self._open_download_wizard)
            btn_row.addWidget(self.download_btn)
            self.token_btn = QPushButton("Trocar token HF…")
            self.token_btn.clicked.connect(self._change_token)
            btn_row.addWidget(self.token_btn)
            btn_row.addStretch(1)
            close_btn = QPushButton("Fechar")
            close_btn.setDefault(True)
            close_btn.clicked.connect(self.accept)
            btn_row.addWidget(close_btn)
            layout.addLayout(btn_row)

            self._populate()

        # -- populate ------------------------------------------------------------
        def _populate(self) -> None:
            from . import model_manager
            from . import runtime
            from datetime import datetime
            self.table.setRowCount(0)
            cache_root = runtime.model_cache_dir()
            scan = model_manager.scan_cache(cache_root)
            scan_by_repo = {e["repo_id"]: e for e in scan}

            # Configured variant, for the "em uso" badge
            ctx = None
            try:
                ctx = self._context_provider()
            except Exception:
                ctx = None
            from . import app_settings as _settings
            # Sem projeto, o "Em uso" segue a escolha da MAQUINA (quem
            # instalou o tiny via o turbo marcado como em uso).
            configured = ((ctx.config.get("asr_model") if ctx else None)
                          or _settings.asr_model_default())

            rows: list[tuple[str, str, int, str, str, bool, str | None, str, str]] = []
            # (label, repo_id, size, status, date_str, can_remove,
            #  download_key, size_hint, status_tip)
            for key, info in model_manager.ASR_VARIANTS.items():
                repo = str(info.get("repo"))
                label = model_manager.friendly_name(key)
                entry = scan_by_repo.get(repo)
                if entry is not None:
                    size = int(entry.get("size_on_disk", 0))
                    status = "Em uso" if key == configured else "Instalado"
                    dt = model_manager.model_install_date(repo, cache_root)
                    date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                    rows.append((label, repo, size, status, date_str, True, None, "", ""))
                else:
                    if info.get("demo_only"):
                        # Decisao 2026-08-30 (endurecida): tiny/base NAO sao
                        # oferecidos em lugar nenhum. So aparecem quando JA
                        # instalados (ramo acima) — senao ficariam
                        # irremoviveis.
                        continue
                    gb = float(info.get("estimated_gb") or 0.0)
                    rows.append((label, repo, 0, "Disponivel", "-", False,
                                 f"asr:{key}", f"~{gb:.1f} GB" if gb else "-", ""))
            # Fixos (obrigatorios). Pendentes ganham rota (F4): ungated
            # (alinhador) baixa por item; gated (pyannote) roteia para o
            # preparador com conta/token. Antes eram becos — a dica de
            # tempos-por-palavra mandava para ca e nao havia botao nenhum.
            for asset in model_manager._FIXED_MODELS:
                repo = asset.repo_id
                label = model_manager.friendly_name(asset.key)
                entry = scan_by_repo.get(repo)
                if entry is not None:
                    size = int(entry.get("size_on_disk", 0))
                    dt = model_manager.model_install_date(repo, cache_root)
                    date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                    rows.append((label, repo, size, "Obrigatorio", date_str, False, None, "", ""))
                else:
                    chave_download = (f"setup:{asset.key}" if asset.gated
                                      else f"opt:{asset.key}")
                    rows.append((label, repo, 0, "Pendente", "-", False,
                                 chave_download, f"~{asset.estimated_gb:.1f} GB",
                                 ("O download exige conta gratuita no Hugging Face "
                                  "e aceite dos termos do modelo."
                                  if asset.gated else "")))
            # Pacotes de idioma (etapa 4): alinhadores por lingua, alem do
            # pt (que ja aparece nos fixos). Baixar por item / remover.
            for lang_code, lang_spec in sorted(
                    model_manager.ALIGN_LANGUAGES.items(),
                    key=lambda kv: kv[1]["label"]):
                if lang_code == "pt":
                    continue
                lang_asset = model_manager.align_asset_for(lang_code)
                entry = scan_by_repo.get(lang_asset.repo_id)
                instalado = False
                if entry is not None:
                    try:
                        snap = model_manager.cached_snapshot_path(
                            lang_asset.repo_id, cache_root, revision=lang_asset.revision)
                        instalado = (snap is not None
                                     and model_manager._snapshot_has_weights(snap))
                    except Exception:  # noqa: BLE001
                        instalado = False
                rotulo_lang = f"Idioma: {lang_spec['label']} (tempos por palavra)"
                if instalado:
                    size = int(entry.get("size_on_disk", 0))
                    dt = model_manager.model_install_date(lang_asset.repo_id, cache_root)
                    date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                    rows.append((rotulo_lang, lang_asset.repo_id, size, "Instalado",
                                 date_str, True, None, "", ""))
                else:
                    rows.append((rotulo_lang, lang_asset.repo_id, 0, "Disponivel",
                                 "-", False, f"opt:{lang_asset.key}",
                                 f"~{lang_asset.estimated_gb:.1f} GB", ""))
            # Pacote coringa MMS (E4-3): 1.130 idiomas, CC-BY-NC (o aviso
            # de licenca aparece na oferta de download).
            mms = model_manager.MMS_ALIGN_ASSET
            entry = scan_by_repo.get(mms.repo_id)
            if entry is not None and model_manager.mms_align_cached(cache_root):
                size = int(entry.get("size_on_disk", 0))
                dt = model_manager.model_install_date(mms.repo_id, cache_root)
                date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                rows.append((mms.label, mms.repo_id, size, "Instalado",
                             date_str, True, None, "", ""))
            else:
                rows.append((mms.label, mms.repo_id, 0, "Disponivel", "-",
                             False, f"opt:{mms.key}",
                             f"~{mms.estimated_gb:.1f} GB",
                             "Cobre 1.130 idiomas (inclusive os sem pacote "
                             "dedicado). Licença CC-BY-NC: uso não-comercial."))
            # Opcionais de IA (antes invisiveis: 8,7 GB de Qwen baixados
            # ficavam irremoviveis, e nao havia escolha por item).
            from . import capabilities as _caps
            hw = _caps.hardware_snapshot()
            for asset in model_manager._OPTIONAL_MODELS:
                entry = scan_by_repo.get(asset.repo_id)
                # "Instalado" exige o criterio REAL (pesos >= 100 KB +
                # companion quando houver) — presenca de pasta marcava um
                # download cancelado no meio como instalado, sem retomada.
                completo = False
                try:
                    snap = model_manager.cached_snapshot_path(
                        asset.repo_id, cache_root, revision=asset.revision)
                    completo = (snap is not None
                                and model_manager._snapshot_has_weights(snap)
                                and model_manager.optional_model_cached(asset, cache_root))
                except Exception:  # noqa: BLE001 - cache ilegivel = incompleto
                    completo = False
                if entry is not None and completo:
                    size = int(entry.get("size_on_disk", 0))
                    dt = model_manager.model_install_date(asset.repo_id, cache_root)
                    date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                    rows.append((asset.label, asset.repo_id, size, "Instalado",
                                 date_str, True, None, "", ""))
                    continue
                if entry is not None and not completo:
                    size = int(entry.get("size_on_disk", 0))
                    rows.append((asset.label, asset.repo_id, size, "Incompleto",
                                 "-", False, f"opt:{asset.key}",
                                 f"~{asset.estimated_gb:.1f} GB",
                                 "Download anterior incompleto — Baixar retoma "
                                 "aproveitando o que já veio."))
                    continue
                cap = _caps.capability_for_model(asset.key)
                bloqueio = _caps.hardware_blocker(cap, hw) if cap is not None else ""
                aviso = _caps.hardware_warning(cap, hw) if cap is not None else ""
                gb_hint = f"~{asset.estimated_gb:.1f} GB"
                if bloqueio:
                    rows.append((asset.label, asset.repo_id, 0, "Incompativel",
                                 "-", False, None, gb_hint, f"{cap.label} {bloqueio}."))
                else:
                    rows.append((asset.label, asset.repo_id, 0, "Disponivel",
                                 "-", False, f"opt:{asset.key}", gb_hint,
                                 (f"Atenção: {cap.label} {aviso}. Por sua conta e risco."
                                  if aviso else "")))
            # Aceleracao GPU do Parakeet — unico item NAO-HF do
            # gerenciador: pacote pip (onnxruntime-gpu) num diretorio
            # isolado (ver onnx_env.py). O sentinel "env:onnx_gpu" no
            # campo repo roteia remocao/download para os ramos proprios.
            import sys as _sys
            if _sys.platform == "win32" and not getattr(_sys, "frozen", False):
                from . import onnx_env as _onnx_env, runtime as _rt
                rotulo_gpu = "Aceleração do Parakeet na GPU"
                gb_gpu = f"~{_onnx_env.ESTIMATED_GB:.1f} GB".replace(".", ",")
                dir_gpu = _onnx_env.onnx_env_dir()
                if not hw.has_gpu:
                    rows.append((rotulo_gpu, "env:onnx_gpu", 0, "Incompativel",
                                 "-", False, None, gb_gpu,
                                 "Requer uma placa de vídeo NVIDIA."))
                elif not _rt.cuda_libs_present():
                    rows.append((rotulo_gpu, "env:onnx_gpu", 0, "Incompativel",
                                 "-", False, None, gb_gpu,
                                 "Requer o Transcritório instalado com a "
                                 "aceleração NVIDIA (CUDA)."))
                elif _onnx_env.onnx_env_ready():
                    try:
                        size_gpu = sum(f.stat().st_size
                                       for f in dir_gpu.rglob("*") if f.is_file())
                        dt_gpu = _onnx_env.marker_path().stat().st_mtime
                        data_gpu = datetime.fromtimestamp(dt_gpu).strftime("%d/%m/%Y")
                    except OSError:
                        size_gpu, data_gpu = 0, "-"
                    rows.append((rotulo_gpu, "env:onnx_gpu", size_gpu, "Instalado",
                                 data_gpu, True, None, "",
                                 "O motor Parakeet usa a GPU quando o Dispositivo "
                                 "é Automático ou GPU."))
                elif dir_gpu.exists():
                    motivo_gpu = _onnx_env.onnx_env_stale_reason()
                    rows.append((rotulo_gpu, "env:onnx_gpu", 0, "Incompleto",
                                 "-", False, "env:onnx_gpu", gb_gpu,
                                 (f"Precisa ser reinstalada: {motivo_gpu}. " if motivo_gpu else
                                  "Instalação incompleta ou desatualizada — ")
                                 + "Baixar refaz do zero."))
                else:
                    vram_gpu = _rt.total_vram_gb()
                    aviso_gpu = ("" if vram_gpu is None or vram_gpu >= 6.0 else
                                 "Atenção: pouca memória de vídeo (usa ~4,7 GB). "
                                 "Por sua conta e risco — se falhar, o app volta "
                                 "para o processador sozinho. ")
                    rows.append((rotulo_gpu, "env:onnx_gpu", 0, "Disponivel",
                                 "-", False, "env:onnx_gpu", gb_gpu,
                                 aviso_gpu + "Acelera o motor Parakeet pt-BR "
                                 "(~4x mais rápido que no processador)."))
            # Orfaos
            for repo in model_manager.orphan_repos(cache_root):
                entry = scan_by_repo.get(repo)
                size = int(entry["size_on_disk"]) if entry else 0
                dt = model_manager.model_install_date(repo, cache_root)
                date_str = datetime.fromtimestamp(dt).strftime("%d/%m/%Y") if dt else "-"
                rows.append((f"{repo} (orfao)", repo, size, "Orfao", date_str, True, None, "", ""))

            # Popular tabela
            for r_idx, (label, repo, size, status, date_str, can_remove,
                        download_key, size_hint, status_tip) in enumerate(rows):
                self.table.insertRow(r_idx)
                name_item = QTableWidgetItem(label)
                name_item.setToolTip(repo)
                self.table.setItem(r_idx, self.COL_NAME, name_item)
                size_str = model_manager._format_size(size) if size else (size_hint or "-")
                self.table.setItem(r_idx, self.COL_SIZE, QTableWidgetItem(size_str))
                status_item = QTableWidgetItem(status)
                if status == "Em uso":
                    status_item.setToolTip("Modelo atualmente selecionado na configuração de transcrição.")
                elif status_tip:
                    status_item.setToolTip(status_tip)
                self.table.setItem(r_idx, self.COL_STATUS, status_item)
                self.table.setItem(r_idx, self.COL_DATE, QTableWidgetItem(date_str))
                if can_remove:
                    btn = QPushButton("Remover")
                    btn.setToolTip(f"Remover {repo} do cache local. Voce podera baixar de novo depois.")
                    btn.clicked.connect(lambda _chk, rid=repo, st=status: self._remove_model(rid, st))
                    self.table.setCellWidget(r_idx, self.COL_ACTION, btn)
                elif download_key:
                    btn = QPushButton("Baixar")
                    btn.setToolTip("Baixar este modelo agora (tamanho e requisitos confirmados antes).")
                    btn.clicked.connect(lambda _chk, dk=download_key: self._download_row(dk))
                    self.table.setCellWidget(r_idx, self.COL_ACTION, btn)

            total_bytes = sum(int(e.get("size_on_disk", 0)) for e in scan)
            self.summary_label.setText(
                f"Espaço total em cache: {model_manager._format_size(total_bytes)}  |  Pasta: {cache_root}"
            )

        # -- actions ------------------------------------------------------------
        def _open_models_folder(self) -> None:
            from . import runtime
            folder = runtime.model_cache_dir()
            folder.mkdir(parents=True, exist_ok=True)
            open_folder_in_explorer(folder)

        def _download_row(self, download_key: str) -> None:
            kind, _, key = download_key.partition(":")
            if kind == "opt":
                self._download_optional(key)
            elif kind == "asr":
                self._download_asr_variant(key)
            elif kind == "setup":
                # Modelo gated (pyannote): o caminho e o preparador, que
                # cuida de conta/token/termos.
                parent = self.parent()
                if parent is not None and hasattr(parent, "show_model_setup"):
                    parent.show_model_setup(include_diarization=True)
                    self._populate()
            elif kind == "env":
                self._install_onnx_gpu_env()

        def _download_optional(self, key: str) -> None:
            """Delegar a oferta padrao da janela (_ensure_optional_model):
            confirmacao com GB, requisito de hardware, disco e o ambiente
            de ~3 GB quando aplicavel — e invalidacao do cache de
            capacidades no sucesso. Aceita opcionais E fixos ungated."""
            parent = self.parent()
            if parent is None or not hasattr(parent, "_ensure_optional_model"):
                return
            from . import model_manager
            asset = model_manager.asset_by_key(key)
            ok = parent._ensure_optional_model(
                key, asset.label,
                f"{asset.label} habilita: {asset.purpose}.",
                needs_llm_env=key in ("llm_qwen", "ner_gliner"))
            if ok:
                self._populate()

        def _download_asr_variant(self, key: str) -> None:
            """Baixar UMA variante do Whisper por item (fecha o caso
            "quero instalar outro modelo depois do assistente")."""
            from . import model_manager
            janela = self.parent()
            if janela is not None and getattr(janela, "_model_download_busy", False):
                QMessageBox.information(self, "Download em andamento",
                                        "Aguarde o download atual terminar.")
                return
            info = model_manager.ASR_VARIANTS.get(key) or {}
            gb = float(info.get("estimated_gb") or 0.0)
            disk = model_manager.check_disk_space(gb)
            if not disk.get("ok"):
                QMessageBox.warning(self, "Espaço em disco insuficiente",
                                    str(disk.get("message") or ""))
                return
            nome = model_manager.friendly_name(key)
            answer = QMessageBox.question(
                self, "Baixar modelo de transcrição?",
                f"Baixar {nome} agora (uma vez, ~{gb:.1f} GB)?\n"
                f"Espaço livre em disco: {disk.get('free_gb', 0):.1f} GB.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            dialog = QProgressDialog(f"Baixando {nome}...", "Cancelar", 0, 100, self)
            dialog.setWindowTitle("Baixar modelo")
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setAutoClose(False)
            dialog.show()

            def on_progress(detail: dict) -> None:
                dialog.setValue(max(0, min(100, int(detail.get("progress") or 0))))
                if detail.get("message"):
                    dialog.setLabelText(str(detail["message"]))
                QApplication.processEvents()

            if janela is not None:
                janela._model_download_busy = True
            try:
                result = app_service.download_models(
                    token="",
                    progress_callback=on_progress,
                    should_cancel=dialog.wasCanceled,
                    asr_variants=[key],
                    include_diarization=False,
                    include_alignment=False,
                )
            except Exception as exc:  # noqa: BLE001 - excecao subia sem mensagem
                QMessageBox.warning(
                    self, "Download não concluído",
                    f"Nao foi possivel baixar {nome}: {sanitize_message(str(exc))}")
                return
            finally:
                if janela is not None:
                    janela._model_download_busy = False
                dialog.close()
            if getattr(result, "failures", 0):
                QMessageBox.warning(
                    self, "Download não concluído",
                    f"Nao foi possivel baixar {nome}. Verifique a conexao e tente de novo.")
                return
            parent = self.parent()
            if parent is not None and hasattr(parent, "_invalidate_capability_cache"):
                parent._invalidate_capability_cache()
            self._populate()

        def _install_onnx_gpu_env(self) -> None:
            """Instalar a aceleracao GPU do Parakeet (pip --target isolado).

            Nao e um modelo HF: nao passa pelo download_optional_model.
            O download do uv nao e cancelavel no meio (mesma limitacao
            aceita no llm_env) — o dialogo nao promete cancelamento.
            """
            from . import onnx_env as _onnx_env, runtime as _rt
            janela = self.parent()
            if janela is not None and getattr(janela, "_model_download_busy", False):
                QMessageBox.information(self, "Download em andamento",
                                        "Aguarde o download atual terminar.")
                return
            from . import model_manager
            disk = model_manager.check_disk_space(_onnx_env.ESTIMATED_GB + 0.2)
            if not disk.get("ok"):
                QMessageBox.warning(self, "Espaço em disco insuficiente",
                                    str(disk.get("message") or ""))
                return
            vram = _rt.total_vram_gb()
            aviso = ""
            if vram is not None and vram < 6.0:
                aviso = ("\n\nAtenção: sua placa tem pouca memória de vídeo "
                         f"({vram:.0f} GB; o Parakeet usa ~4,7 GB). Por sua "
                         "conta e risco — se falhar, o app volta para o "
                         "processador sozinho.")
            answer = QMessageBox.question(
                self, "Instalar aceleração GPU?",
                "Instalar a aceleração do Parakeet na GPU agora "
                f"(uma vez, ~{_onnx_env.ESTIMATED_GB:.1f} GB)?\n"
                f"Espaço livre em disco: {disk.get('free_gb', 0):.1f} GB."
                + aviso,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            dialog = QProgressDialog("Baixando a aceleração GPU...", None, 0, 100, self)
            dialog.setWindowTitle("Aceleração GPU")
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setAutoClose(False)
            dialog.show()

            def on_progress(detail: dict) -> None:
                dialog.setValue(max(0, min(100, int(detail.get("progress") or 0))))
                if detail.get("message"):
                    dialog.setLabelText(str(detail["message"]))
                QApplication.processEvents()

            if janela is not None:
                janela._model_download_busy = True
            try:
                rc = _onnx_env.create_onnx_env(progress_callback=on_progress)
            except Exception as exc:  # noqa: BLE001 - excecao subiria sem mensagem
                rc = 1
                print(f"create_onnx_env: {exc}")
            finally:
                if janela is not None:
                    janela._model_download_busy = False
                dialog.close()
            if rc != 0:
                QMessageBox.warning(
                    self, "Instalação não concluída",
                    "Não foi possível instalar a aceleração GPU. "
                    "Verifique a conexão e tente de novo.")
                return
            self._populate()

        def _jobs_using_model_repo(self, repo_id: str) -> int:
            """Retorna numero de jobs Rodando/Na fila que estao usando este modelo.

            Heuristica simples: se o repo corresponde ao asr_model configurado e
            existem jobs ativos, conta. Diarizacao/alignment sao sempre usados
            em qualquer job."""
            try:
                ctx = self._context_provider()
            except Exception:
                return 0
            if ctx is None:
                return 0
            active = [iid for iid, j in (ctx.jobs or {}).items() if (j or {}).get("status") in ("Rodando", "Na fila")]
            if not active:
                return 0
            from . import model_manager
            configured = ctx.config.get("asr_model") or model_manager.DEFAULT_ASR_VARIANT
            configured_repo = model_manager.ASR_VARIANTS.get(configured, {}).get("repo")
            # Bloquear remocao do asr atual OU de modelos obrigatorios enquanto ha job
            known_required = {asset.repo_id for asset in model_manager._FIXED_MODELS}
            if repo_id == configured_repo or repo_id in known_required:
                return len(active)
            return 0

        def _remove_model(self, repo_id: str, status: str) -> None:
            from . import model_manager
            if repo_id == "env:onnx_gpu":
                # Sentinel do diretorio de aceleracao (nao e repo HF).
                # Enquanto o modelo Parakeet estiver em uso por um job,
                # bloquear tambem a remocao do acelerador.
                from . import onnx_env as _onnx_env
                parakeet_repo = str(model_manager.ASR_VARIANTS.get(
                    "parakeet-pt", {}).get("repo") or "")
                if parakeet_repo and self._jobs_using_model_repo(parakeet_repo):
                    QMessageBox.information(
                        self, "Acao bloqueada",
                        "Ha tarefas na fila usando o motor Parakeet. Cancele "
                        "ou aguarde antes de remover a aceleração.")
                    return
                reply = QMessageBox.question(
                    self, "Remover aceleração GPU",
                    "Remover a aceleração do Parakeet na GPU?\n\n"
                    "O motor continua funcionando no processador. Você pode "
                    "instalar de novo depois.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                if _onnx_env.remove_onnx_env():
                    QMessageBox.information(self, "Aceleração removida",
                                            "A aceleração GPU foi removida.")
                else:
                    QMessageBox.warning(
                        self, "Não foi possível remover",
                        "Feche o app e tente de novo, ou apague a pasta "
                        f"manualmente: {_onnx_env.onnx_env_dir()}")
                self._populate()
                return
            busy_n = self._jobs_using_model_repo(repo_id)
            if busy_n:
                QMessageBox.information(
                    self,
                    "Acao bloqueada",
                    f"Ha {busy_n} tarefas na fila usando este modelo. Cancele ou aguarde antes de remover.",
                )
                return
            # Aviso especial se e o modelo configurado
            warn = ""
            if status == "Em uso":
                warn = "\n\nEste modelo está selecionado em Configurar transcrição. Após remover, você precisará baixá-lo de novo ou trocar o modelo antes da próxima transcrição."
            reply = QMessageBox.question(
                self,
                "Remover modelo",
                f"Remover {repo_id} do cache local?{warn}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            result = model_manager.delete_model(repo_id)
            if result["success"]:
                freed = model_manager._format_size(int(result["bytes_freed"]))
                QMessageBox.information(self, "Modelo removido", f"{freed} liberados.")
            else:
                QMessageBox.warning(self, "Não foi possível remover", str(result["error"]))
            # Remover muda o estado das capacidades: sem isto os tooltips
            # da janela seguiam dizendo "pronta" para um modelo apagado.
            parent = self.parent()
            if parent is not None and hasattr(parent, "_invalidate_capability_cache"):
                parent._invalidate_capability_cache()
            self._populate()

        def _remove_orphans(self) -> None:
            from . import model_manager
            from . import runtime
            orphans = model_manager.orphan_repos(runtime.model_cache_dir())
            if not orphans:
                QMessageBox.information(self, "Sem orfaos", "Nao ha modelos orfaos no cache.")
                return
            reply = QMessageBox.question(
                self,
                "Remover orfaos",
                f"Remover {len(orphans)} modelo(s) orfao(s)?\n\n" + "\n".join(orphans),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            freed_total = 0
            errors: list[str] = []
            for repo in orphans:
                r = model_manager.delete_model(repo)
                if r["success"]:
                    freed_total += int(r["bytes_freed"])
                else:
                    errors.append(f"{repo}: {r['error']}")
            msg = f"{model_manager._format_size(freed_total)} liberados."
            if errors:
                msg += "\n\nFalhas:\n" + "\n".join(errors)
            QMessageBox.information(self, "Orfaos removidos", msg)
            parent = self.parent()
            if parent is not None and hasattr(parent, "_invalidate_capability_cache"):
                parent._invalidate_capability_cache()
            self._populate()

        def _open_download_wizard(self) -> None:
            parent = self.parent()
            if parent and hasattr(parent, "show_model_setup"):
                parent.show_model_setup()
                self._populate()

        def _change_token(self) -> None:
            from . import token_vault
            # Nunca pre-preencher com o token do cofre nem exibir em texto
            # claro: campo mascarado (Password). Deixar vazio + OK = fluxo
            # de apagar token (confirmado abaixo).
            new_token, ok = QInputDialog.getText(
                self,
                "Token do Hugging Face",
                "Token (fica salvo apenas neste computador; vazio = apagar o salvo):",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            new_token = new_token.strip()
            if not new_token:
                # Limpar token
                reply = QMessageBox.question(
                    self,
                    "Esquecer token",
                    "Apagar o token salvo neste computador?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        token_vault.clear()
                        QMessageBox.information(self, "Token apagado", "Token removido do cofre local.")
                    except Exception as exc:
                        QMessageBox.warning(self, "Falha ao apagar", str(exc))
                return
            try:
                token_vault.store(new_token)
                QMessageBox.information(self, "Token salvo", "Token salvo no cofre seguro local.")
            except Exception as exc:
                QMessageBox.warning(self, "Falha ao salvar", str(exc))


    class MetadataDialog(QDialog):
        def __init__(self, selected_count: int, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Editar propriedades")
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel(f"Editar propriedades de {selected_count} arquivo(s) selecionado(s)."))

            grid = QGridLayout()
            self.apply_language = QCheckBox("Aplicar língua")
            self.language_combo = QComboBox()
            # Etapa 4: gerado do registro de pacotes de idioma (uma fonte).
            from . import model_manager as _mm_lang
            _ordenados = sorted(_mm_lang.ALIGN_LANGUAGES.items(),
                                key=lambda kv: (kv[0] != "pt", kv[1]["label"]))
            self.language_combo.addItem(_ordenados[0][1]["label"], "pt")
            self.language_combo.addItem("Automático", "auto")
            for _code, _spec in _ordenados[1:]:
                self.language_combo.addItem(str(_spec["label"]), _code)
            grid.addWidget(self.apply_language, 0, 0)
            grid.addWidget(self.language_combo, 0, 1, 1, 3)

            self.apply_speakers = QCheckBox("Aplicar falantes")
            self.speaker_mode_combo = QComboBox()
            for value, label in [("exact", "Número exato"), ("auto", "Automático"), ("range", "Intervalo")]:
                self.speaker_mode_combo.addItem(label, value)
            self.speaker_count_spin = QSpinBox()
            self.speaker_count_spin.setRange(1, 20)
            self.speaker_count_spin.setValue(2)
            self.min_speakers_spin = QSpinBox()
            self.min_speakers_spin.setRange(1, 20)
            self.min_speakers_spin.setValue(3)
            self.max_speakers_spin = QSpinBox()
            self.max_speakers_spin.setRange(1, 20)
            self.max_speakers_spin.setValue(8)
            grid.addWidget(self.apply_speakers, 1, 0)
            grid.addWidget(self.speaker_mode_combo, 1, 1)
            grid.addWidget(QLabel("Exato:"), 1, 2)
            grid.addWidget(self.speaker_count_spin, 1, 3)
            grid.addWidget(QLabel("Min./máx.:"), 2, 1)
            grid.addWidget(self.min_speakers_spin, 2, 2)
            grid.addWidget(self.max_speakers_spin, 2, 3)

            self.apply_labels = QCheckBox("Aplicar rótulos")
            self.labels_edit = QLineEdit("Entrevistador | Entrevistado")
            self.labels_edit.setPlaceholderText("Entrevistador | Entrevistado")
            grid.addWidget(self.apply_labels, 3, 0)
            grid.addWidget(self.labels_edit, 3, 1, 1, 3)

            self.apply_context = QCheckBox("Aplicar contexto opcional")
            self.context_text = QTextEdit()
            self.context_text.setPlaceholderText("Use poucas frases com nomes, termos e assunto. Deixe em branco se não tiver certeza.")
            self.context_text.setMinimumHeight(90)
            self.use_context_as_prompt = QCheckBox("Usar este contexto como auxílio na transcrição")
            grid.addWidget(self.apply_context, 4, 0)
            grid.addWidget(self.context_text, 4, 1, 1, 3)
            grid.addWidget(self.use_context_as_prompt, 5, 1, 1, 3)

            layout.addLayout(grid)
            # Nota inline (2026-09-02): projeto no TAGARELA + lingua aplicada
            # que nao e portugues — avisa e ensina, sem bloquear.
            self.engine_lang_note = QLabel("")
            self.engine_lang_note.setWordWrap(True)
            self.engine_lang_note.setStyleSheet(f"color: {ui_tokens.WARN}; font-weight: 600;")
            self.engine_lang_note.setVisible(False)
            layout.addWidget(self.engine_lang_note)
            _ctx = getattr(parent, "context", None)
            self._project_model = str(((getattr(_ctx, "config", None) or {}).get("asr_model")) or "")
            self.language_combo.currentIndexChanged.connect(lambda _i: self._refresh_engine_lang_note())
            self.apply_language.toggled.connect(lambda _c: self._refresh_engine_lang_note())
            hint = QLabel("Campos não marcados não serão alterados. O contexto é opcional e pode ficar vazio.")
            hint.setStyleSheet(_style_muted())
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _refresh_engine_lang_note(self) -> None:
            texto = ""
            if self.apply_language.isChecked():
                texto = engine_language_note(self._project_model, str(self.language_combo.currentData() or ""))
            self.engine_lang_note.setText(texto)
            self.engine_lang_note.setVisible(bool(texto))

        def updates(self) -> dict[str, str]:
            updates: dict[str, str] = {}
            if self.apply_language.isChecked():
                updates["language"] = str(self.language_combo.currentData())
            if self.apply_speakers.isChecked():
                speaker_mode = str(self.speaker_mode_combo.currentData())
                updates["speaker_mode"] = speaker_mode
                if speaker_mode == "exact":
                    updates["speaker_count"] = str(self.speaker_count_spin.value())
                    updates["min_speakers"] = str(self.speaker_count_spin.value())
                    updates["max_speakers"] = str(self.speaker_count_spin.value())
                elif speaker_mode == "range":
                    updates["speaker_count"] = ""
                    updates["min_speakers"] = str(self.min_speakers_spin.value())
                    updates["max_speakers"] = str(self.max_speakers_spin.value())
                else:
                    updates["speaker_count"] = ""
                    updates["min_speakers"] = ""
                    updates["max_speakers"] = ""
            if self.apply_labels.isChecked():
                labels = [label.strip() for label in self.labels_edit.text().replace(",", "|").split("|") if label.strip()]
                updates["speaker_labels"] = "|".join(labels)
            if self.apply_context.isChecked():
                context = self.context_text.toPlainText().strip()
                updates["context_mode"] = "custom" if context else "empty"
                updates["context_text"] = context
                updates["use_context_as_prompt"] = "true" if self.use_context_as_prompt.isChecked() and context else "false"
            return updates


    class SpeakerCountDialog(QDialog):
        """Pergunta "Quantas pessoas falam?" ao transcrever arquivos ainda sem
        configuracao de falantes (plano D3.1). Uma pergunta por LOTE — a
        transcricao em massa nunca e interrompida arquivo a arquivo."""

        def __init__(
            self,
            file_count: int,
            parent: QWidget | None = None,
            *,
            estimate_text: str | None = None,
            ask_counts: bool = True,
        ) -> None:
            super().__init__(parent)
            # 2026-09-02: a mesma janela carrega, sem GPU, a estimativa
            # honesta do lote e a caixa "Separar falantes agora" (a
            # separacao domina o tempo em CPU; o texto pode vir antes).
            # ask_counts=False = contagens ja configuradas: so a estimativa.
            self.ask_counts = bool(ask_counts)
            self.setWindowTitle("Quantas pessoas falam?" if self.ask_counts else "Transcrever")
            self.setMinimumWidth(480)
            layout = QVBoxLayout(self)
            self.interview_radio = self.group_radio = self.exact_radio = self.auto_radio = None
            if self.ask_counts:
                scope = f"nestes {file_count} arquivos" if file_count > 1 else "neste arquivo"
                intro = QLabel(
                    f"Quantas pessoas falam {scope}? Isso orienta a separação de vozes — "
                    "um grupo focal forçado a 2 falantes sai errado. Dá para ajustar depois por arquivo em Editar propriedades."
                )
                intro.setWordWrap(True)
                layout.addWidget(intro)
                self.interview_radio = QRadioButton("Entrevista — 2 pessoas (entrevistador e entrevistado)")
                self.interview_radio.setChecked(True)
                layout.addWidget(self.interview_radio)
                group_row = QHBoxLayout()
                self.group_radio = QRadioButton("Grupo focal — entre")
                group_row.addWidget(self.group_radio)
                self.group_min_spin = QSpinBox()
                self.group_min_spin.setRange(2, 20)
                self.group_min_spin.setValue(3)
                group_row.addWidget(self.group_min_spin)
                group_row.addWidget(QLabel("e"))
                self.group_max_spin = QSpinBox()
                self.group_max_spin.setRange(2, 20)
                self.group_max_spin.setValue(8)
                group_row.addWidget(self.group_max_spin)
                group_row.addWidget(QLabel("pessoas"))
                group_row.addStretch()
                layout.addLayout(group_row)
                exact_row = QHBoxLayout()
                self.exact_radio = QRadioButton("Número exato:")
                exact_row.addWidget(self.exact_radio)
                self.exact_spin = QSpinBox()
                self.exact_spin.setRange(1, 20)
                self.exact_spin.setValue(3)
                exact_row.addWidget(self.exact_spin)
                exact_row.addWidget(QLabel("pessoas"))
                exact_row.addStretch()
                layout.addLayout(exact_row)
                self.auto_radio = QRadioButton("Automático — deixar o programa estimar")
                layout.addWidget(self.auto_radio)
            self.diarize_now_check: QCheckBox | None = None
            if estimate_text:
                estimate_label = QLabel(estimate_text)
                estimate_label.setWordWrap(True)
                estimate_label.setStyleSheet(_style_muted())
                layout.addWidget(estimate_label)
                self.diarize_now_check = QCheckBox("Separar falantes agora")
                self.diarize_now_check.setChecked(True)
                self.diarize_now_check.setToolTip(
                    "Desmarcada: este lote sai só com o texto; a lista oferece "
                    "\"Separar falantes agora\" para completar depois, em lote.")
                layout.addWidget(self.diarize_now_check)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Transcrever")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def diarize_now(self) -> bool:
            """Decisao do lote: True sem a caixa (GPU) ou com ela marcada."""
            return True if self.diarize_now_check is None else self.diarize_now_check.isChecked()

        def updates(self) -> dict[str, str]:
            """Mesmas chaves do MetadataDialog + marcador speaker_setup."""
            if not self.ask_counts:
                return {}
            result: dict[str, str] = {"speaker_setup": "true"}
            if self.interview_radio.isChecked():
                result.update({
                    "speaker_mode": "exact", "speaker_count": "2", "min_speakers": "2", "max_speakers": "2",
                    "speaker_labels": "|".join(project_store.default_speaker_labels(2)),
                })
            elif self.group_radio.isChecked():
                low = min(self.group_min_spin.value(), self.group_max_spin.value())
                high = max(self.group_min_spin.value(), self.group_max_spin.value())
                result.update({
                    "speaker_mode": "range", "speaker_count": "", "min_speakers": str(low), "max_speakers": str(high),
                    "speaker_labels": "|".join(project_store.default_speaker_labels(high)),
                })
            elif self.exact_radio.isChecked():
                count = self.exact_spin.value()
                result.update({
                    "speaker_mode": "exact", "speaker_count": str(count), "min_speakers": str(count), "max_speakers": str(count),
                    "speaker_labels": "|".join(project_store.default_speaker_labels(count)),
                })
            else:
                result.update({"speaker_mode": "auto", "speaker_count": "", "min_speakers": "", "max_speakers": ""})
            return result


    class SpellingReviewDialog(QDialog):
        """Revisao de grafias (lote 6b): a UNICA porta que altera o texto
        das entrevistas, entao a evidencia fica a vista e a decisao e por
        OCORRENCIA — nunca por palavra. Nada vem marcado: o usuario
        escolhe ativamente o que muda. Clicar num trecho abre a entrevista
        naquele ponto para ouvir antes de decidir."""

        def __init__(self, window, grupos: list[dict[str, Any]]) -> None:
            super().__init__(window)
            self._window = window
            self.setWindowTitle("✨ Revisar grafias de nomes")
            self.setMinimumSize(760, 520)
            self._checks: list[tuple[QCheckBox, dict[str, Any], QLineEdit]] = []

            layout = QVBoxLayout(self)
            intro = QLabel(
                "A AI encontrou nomes escritos de formas diferentes. Marque só as "
                "ocorrências que são erro de transcrição — cada uma é decidida "
                "separadamente, porque a mesma palavra pode ser um nome legítimo "
                "em outro trecho. A forma corrigida é uma sugestão: você pode "
                "digitar a grafia certa no campo de cada nome.\nO áudio e a "
                "transcrição original não são alterados; Ctrl+Z desfaz na "
                "entrevista aberta."
            )
            intro.setWordWrap(True)
            intro.setStyleSheet(_style_muted())
            layout.addWidget(intro)

            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            for grupo in grupos:
                box = QGroupBox(f"\"{grupo['variante']}\"")
                box_layout = QVBoxLayout(box)
                # Sugestao EDITAVEL (teste real 2026-08-31: a AI sugeriu
                # "UEG" onde o certo era "UERJ" e nao havia como corrigir a
                # sugestao — so aceitar ou desistir).
                alvo_row = QHBoxLayout()
                alvo_row.addWidget(QLabel("Corrigir para:"))
                alvo_edit = QLineEdit(str(grupo["canonico"]))
                alvo_edit.setPlaceholderText(str(grupo["canonico"]))
                alvo_edit.setToolTip(
                    "Sugestão da AI — edite se a grafia certa for outra.\n"
                    "Vazio volta para a sugestão.")
                alvo_edit.setMaximumWidth(320)
                alvo_row.addWidget(alvo_edit)
                alvo_row.addStretch(1)
                box_layout.addLayout(alvo_row)
                ocorrencias = grupo["ocorrencias"]
                todas = QCheckBox(f"marcar as {len(ocorrencias)} ocorrência(s)")
                box_layout.addWidget(todas)
                grupo_checks: list[QCheckBox] = []
                for ocorrencia in ocorrencias:
                    row = QHBoxLayout()
                    check = QCheckBox()
                    grupo_checks.append(check)
                    self._checks.append((check, ocorrencia, alvo_edit))
                    row.addWidget(check)
                    rotulo = QPushButton(
                        f"{ocorrencia['interview_id']} • {format_clock(ocorrencia['start'])} • "
                        f"{ocorrencia['trecho']}")
                    rotulo.setFlat(True)
                    rotulo.setStyleSheet("text-align: left;")
                    rotulo.setToolTip("Abrir a entrevista neste ponto para ouvir")
                    rotulo.clicked.connect(
                        lambda _checked=False, o=ocorrencia: self._window.open_search_hit(
                            str(o["interview_id"]), float(o["start"])))
                    row.addWidget(rotulo, 1)
                    box_layout.addLayout(row)
                todas.toggled.connect(
                    lambda checked, items=grupo_checks: [c.setChecked(checked) for c in items])
                container_layout.addWidget(box)
            container_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(container)
            layout.addWidget(scroll, 1)

            self.count_label = QLabel("")
            self.count_label.setStyleSheet(_style_muted())
            layout.addWidget(self.count_label)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar correções")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Agora não")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            for check, _ocorrencia, _edit in self._checks:
                check.toggled.connect(self._update_count)
            self._update_count()

        def _update_count(self) -> None:
            total = len(self.selected())
            self.count_label.setText(
                "Nenhuma ocorrência marcada — nada será alterado."
                if not total else f"{total} ocorrência(s) serão corrigidas.")

        def selected(self) -> list[dict[str, Any]]:
            # A forma corrigida sai do campo do grupo (editavel); vazio
            # volta para a sugestao original da AI.
            return [
                {**ocorrencia,
                 "canonico": edit.text().strip() or str(ocorrencia["canonico"])}
                for check, ocorrencia, edit in self._checks if check.isChecked()
            ]


    class SpeakerNamingDialog(QDialog):
        """Dialogo "De quem é esta voz?" (planos D2.1/D2.5/D2.6): trechos com
        timestamp + prévia do texto por voz, ▶/⏹ com destaque do que toca, e a
        saída de emergência para agrupamento contaminado (vozes misturadas =
        número de falantes errado, não um nome a escolher). Player próprio —
        o diálogo é modal e não pode depender do player da janela."""

        def __init__(self, media_path: Path, rows: list[dict[str, Any]], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("De quem é esta voz?")
            self.setMinimumWidth(640)
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            # Mesmo ajuste do player principal: seguir o dispositivo padrao
            # quando um fone e conectado com o dialogo aberto.
            self._media_devices = QMediaDevices(self)
            self._media_devices.audioOutputsChanged.connect(
                lambda: self._audio_output.setDevice(QMediaDevices.defaultAudioOutput()))
            self._player.setSource(QUrl.fromLocalFile(str(media_path)))
            self._stop_at_ms: int | None = None
            self._sample_start_ms: int | None = None
            self._playing_button: QPushButton | None = None
            self._pending_sample: tuple[QPushButton, float, float] | None = None
            self._player.positionChanged.connect(self._stop_when_sample_ends)
            self._player.mediaStatusChanged.connect(self._play_pending_when_loaded)
            self.combos: list[QComboBox] = []

            layout = QVBoxLayout(self)
            intro = QLabel(
                "Ouça um trecho de cada voz e diga quem é — escolha uma sugestão ou digite qualquer nome. "
                "O nome vale para a transcrição inteira."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            # Vozes numa area rolavel: um grupo focal com 6+ vozes estourava a
            # altura da tela e os botoes OK/Cancelar ficavam inalcancaveis
            # (bug pego no 1o teste real de grupo focal, 2026-08-23).
            voices_container = QWidget()
            voices_layout = QVBoxLayout(voices_container)
            voices_layout.setContentsMargins(0, 0, 0, 0)
            for index, row in enumerate(rows):
                chip = VOICE_CHIP_COLORS[index % len(VOICE_CHIP_COLORS)]
                group = QGroupBox()
                group_layout = QVBoxLayout(group)
                title = QLabel(f"<span style='color:{chip}; font-size:14px;'>●</span> <b>{row['title']}</b>")
                title.setTextFormat(Qt.TextFormat.RichText)
                group_layout.addWidget(title)
                for sample in row["samples"]:
                    preview = str(sample.get("text") or "")
                    if len(preview) > 70:
                        preview = preview[:67].rstrip() + "..."
                    start = float(sample["start"])
                    end = float(sample["end"])
                    button = QPushButton(f"▶  {format_clock(start)}   “{preview}”")
                    button.setStyleSheet("text-align: left; padding: 4px 10px;")
                    button.setToolTip("Tocar/parar este trecho.")
                    button.clicked.connect(
                        lambda _checked=False, b=button, s=start, e=end: self._toggle_sample(b, s, e)
                    )
                    group_layout.addWidget(button)
                combo_row = QHBoxLayout()
                combo_row.addWidget(QLabel("Quem é?"))
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                combo.addItems(list(row["suggestions"]))
                combo.setMinimumWidth(220)
                combo_row.addWidget(combo, 1)
                self.combos.append(combo)
                group_layout.addLayout(combo_row)
                voices_layout.addWidget(group)
            voices_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(voices_container)
            layout.addWidget(scroll, 1)
            mixed_note = QLabel(
                "⚠ Ouviu vozes DIFERENTES nos trechos de uma mesma linha? O número de falantes pode estar "
                "errado — cancele, ajuste o número de falantes na aba Propriedades e use "
                "Entrevista → Refazer separação de falantes…"
            )
            mixed_note.setWordWrap(True)
            mixed_note.setStyleSheet(_style_muted())
            layout.addWidget(mixed_note)
            skip_note = QLabel("Deixe em branco para manter o nome atual e decidir depois.")
            skip_note.setStyleSheet(_style_muted())
            layout.addWidget(skip_note)
            self.dont_ask_checkbox = QCheckBox("Não perguntar ao abrir transcrições deste projeto (reative em Ferramentas)")
            layout.addWidget(self.dont_ask_checkbox)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar nomes")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Agora não")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(min(760, available.width() - 80), min(860, available.height() - 80))

        def _toggle_sample(self, button: QPushButton, start: float, end: float) -> None:
            if self._playing_button is button:
                self._stop_at_ms = None
                self._sample_start_ms = None
                self._pending_sample = None
                self._player.pause()
                self._reset_playing_button()
                return
            self._reset_playing_button()
            # setPosition antes da midia carregar e ignorado pelo QMediaPlayer
            # (tocaria do inicio do arquivo) — adiar ate LoadedMedia.
            if self._player.mediaStatus() not in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
            ):
                self._pending_sample = (button, start, end)
                return
            self._start_sample(button, start, end)

        def _play_pending_when_loaded(self, status: "QMediaPlayer.MediaStatus") -> None:
            if self._pending_sample is None:
                return
            if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
                button, start, end = self._pending_sample
                self._pending_sample = None
                self._start_sample(button, start, end)

        def _start_sample(self, button: QPushButton, start: float, end: float) -> None:
            self._playing_button = button
            button.setText("⏹" + button.text()[1:])
            target_ms = int(start * 1000)
            self._sample_start_ms = target_ms
            self._stop_at_ms = int(end * 1000)
            self._player.setPosition(target_ms)
            self._player.play()

            # O backend de midia do Windows DESCARTA silenciosamente o seek
            # feito com o player pausado (pause->setPosition->play retoma de
            # onde parou; reproduzido empiricamente em 2026-08-24). Confirmar
            # a posicao logo apos o play e reaplicar se foi ignorada.
            def _ensure_position() -> None:
                if self._playing_button is button and abs(self._player.position() - target_ms) > 1500:
                    self._player.setPosition(target_ms)

            QTimer.singleShot(80, _ensure_position)
            QTimer.singleShot(300, _ensure_position)

        def _reset_playing_button(self) -> None:
            if self._playing_button is not None:
                self._playing_button.setText("▶" + self._playing_button.text()[1:])
                self._playing_button = None

        def _stop_when_sample_ends(self, position_ms: int) -> None:
            if self._stop_at_ms is None:
                return
            # Posicao fora da janela da amostra = seek ainda nao aplicado (ou
            # descartado, em correcao pelo _ensure_position) — nao e o fim da
            # amostra; ignorar para nao pausar antes de o trecho tocar.
            if self._sample_start_ms is not None and (
                position_ms < self._sample_start_ms - 500 or position_ms > self._stop_at_ms + 2000
            ):
                return
            if position_ms >= self._stop_at_ms:
                self._stop_at_ms = None
                self._sample_start_ms = None
                self._player.pause()
                self._reset_playing_button()

        def labels(self) -> list[str]:
            return [" ".join(combo.currentText().split()) for combo in self.combos]

        def dont_ask(self) -> bool:
            return self.dont_ask_checkbox.isChecked()

        def done(self, result: int) -> None:
            self._player.stop()
            super().done(result)


    class CallableWorker(QThread):
        """Roda um callable em thread; progresso e resultado viram sinais.

        Cancelavel (2026-09-03): fn pode aceitar um segundo parametro
        `should_cancel` (callable -> bool); request_cancel() aciona o
        Event — run_ask/run_visao_geral ja honram e matam o subprocesso."""
        progress = Signal(dict)
        done = Signal(object, str)

        def __init__(self, fn, parent=None) -> None:
            super().__init__(parent)
            import threading as _threading
            self._fn = fn  # fn(progress_emit[, should_cancel]) -> result
            self.cancelled = _threading.Event()

        def request_cancel(self) -> None:
            self.cancelled.set()

        def run(self) -> None:  # noqa: D102
            import inspect as _inspect
            try:
                try:
                    aceita_cancel = len(_inspect.signature(self._fn).parameters) >= 2
                except (TypeError, ValueError):
                    aceita_cancel = False
                if aceita_cancel:
                    result = self._fn(self.progress.emit, self.cancelled.is_set)
                else:
                    result = self._fn(self.progress.emit)
                self.done.emit(result, "")
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self.done.emit(None, str(exc)[:500])


    class _SearchDialogBase(QDialog):
        """Base das janelas de busca/exploracao: worker seguro, lista de
        resultados clicavel (abre a entrevista no bloco certo) e fechar
        que SEMPRE fecha."""

        def __init__(self, window, title: str) -> None:
            super().__init__(window)
            self._window = window
            self._worker: CallableWorker | None = None
            self.setWindowTitle(title)
            self.setModal(False)

        _scope_subject = "A busca"

        def _project_ids(self) -> list[str]:
            from . import search as _search
            ctx = self._window.context
            if ctx is None:
                return []
            return [
                r["interview_id"] for r in ctx.rows
                if _search.source_path_for(ctx.paths, r["interview_id"]) is not None
            ]

        def _scope_key(self) -> str:
            combo = getattr(self, "scope_combo", None)
            return str(combo.currentData() or "all") if combo is not None else "all"

        def _friendly_title(self, interview_id: str) -> str:
            ctx = self._window.context
            if ctx is None:
                return interview_id
            metadata = ctx.metadata.get(interview_id, {})
            return str(metadata.get("title") or "").strip() or interview_id

        def _chosen_ids(self) -> list[str]:
            """Ids marcados na lista interna de escolha (modo choose)."""
            chosen: list[str] = []
            scope_list = getattr(self, "scope_list", None)
            if scope_list is None:
                return chosen
            for row in range(scope_list.count()):
                item = scope_list.item(row)
                if item.checkState() == Qt.CheckState.Checked:
                    chosen.append(str(item.data(Qt.ItemDataRole.UserRole)))
            return chosen

        def _scope_counts(self) -> tuple[str, int, int]:
            """(chave, itens no escopo, itens com transcricao)."""
            scope = self._scope_key()
            transcribed = set(self._project_ids())
            if scope == "open":
                open_id = self._window.current_interview_id
                return scope, (1 if open_id else 0), (1 if open_id in transcribed else 0)
            if scope == "choose":
                chosen = [i for i in self._chosen_ids() if i in transcribed]
                return scope, len(chosen), len(chosen)
            ctx = self._window.context
            return scope, (len(ctx.rows) if ctx is not None else 0), len(transcribed)

        def _scope_ids(self) -> list[str]:
            """Ids do escopo escolhido que TEM transcricao (ordem da lista)."""
            scope = self._scope_key()
            transcribed = set(self._project_ids())
            if scope == "open":
                open_id = self._window.current_interview_id
                return [open_id] if open_id in transcribed else []
            if scope == "choose":
                return [i for i in self._chosen_ids() if i in transcribed]
            return self._project_ids()

        def _scope_text(self) -> str:
            scope, total, ready = self._scope_counts()
            return search_scope_text(scope, total, ready, self._scope_subject)

        def _build_scope_widgets(self, layout: QVBoxLayout) -> None:
            """Linha Onde: combo + lista interna de escolha + explicacao
            dinamica (feedback 2026-08-26: o usuario nao sabia ONDE a busca
            opera nem que ela le transcricoes, nao o audio). O escopo e
            escolhido INTEIRAMENTE nesta janela — nunca referencia as
            marcacoes ☑ do painel, que significam "o que transcrever".
            Itens por chave (userData) para escopos futuros (codigo QDA,
            entidade) encaixarem sem redesenho."""
            row = QHBoxLayout()
            row.addWidget(QLabel("Onde:"))
            self.scope_combo = QComboBox()
            self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
            row.addWidget(self.scope_combo, 1)
            layout.addLayout(row)
            self.scope_list = QListWidget()
            self.scope_list.setMaximumHeight(120)
            self.scope_list.setVisible(False)
            self.scope_list.itemChanged.connect(
                lambda _item: self.scope_label.setText(self._scope_text()))
            layout.addWidget(self.scope_list)
            self.scope_label = QLabel("")
            self.scope_label.setWordWrap(True)
            self.scope_label.setStyleSheet(_style_muted())
            layout.addWidget(self.scope_label)

        def _on_scope_changed(self, _index: int) -> None:
            self.scope_list.setVisible(self._scope_key() == "choose")
            self.scope_label.setText(self._scope_text())

        def _refresh_scope(self) -> None:
            """Reconstroi combo e lista (janela cacheada; transcritas e a
            aberta mudam por fora) preservando escolha e checks por id."""
            combo = getattr(self, "scope_combo", None)
            if combo is None or self._window.context is None:
                return
            transcribed = self._project_ids()
            current = self._scope_key()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"Todas as entrevistas transcritas ({len(transcribed)})", "all")
            open_id = self._window.current_interview_id
            if open_id:
                combo.addItem(
                    f"Somente a entrevista aberta ({self._friendly_title(open_id)})", "open")
            combo.addItem("Escolher quais…", "choose")
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
            checked_before = set(self._chosen_ids())
            scope_list = self.scope_list
            scope_list.blockSignals(True)
            scope_list.clear()
            for interview_id in transcribed:
                title = self._friendly_title(interview_id)
                text = title if title == interview_id else f"{title} ({interview_id})"
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(Qt.ItemDataRole.UserRole, interview_id)
                item.setCheckState(
                    Qt.CheckState.Checked if interview_id in checked_before
                    else Qt.CheckState.Unchecked)
                scope_list.addItem(item)
            scope_list.blockSignals(False)
            scope_list.setVisible(self._scope_key() == "choose")
            self.scope_label.setText(self._scope_text())

        def showEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            self._refresh_scope()
            super().showEvent(event)

        def changeEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            # Voltar do painel principal para a janela atualiza contagens e
            # a opcao "aberta" — o print de 2026-08-26 mostrou o combo
            # desatualizado por depender so do showEvent.
            if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
                self._refresh_scope()
            super().changeEvent(event)

        def _make_results_list(self) -> QListWidget:
            results = QListWidget()
            results.itemActivated.connect(self._open_hit)
            results.itemClicked.connect(self._open_hit)
            return results

        def _add_hit(self, results: QListWidget, prefix: str, hit: dict) -> None:
            text = hit["text"]
            if len(text) > 110:
                first_span = (hit.get("spans") or [(0, 0)])[0]
                left = max(0, first_span[0] - 40)
                text = ("…" if left else "") + text[left:left + 110] + "…"
            label = f"{prefix}{hit['interview_id']}  {format_clock(hit['start'])}  {hit['label']}: {text}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (hit["interview_id"], hit["start"]))
            results.addItem(item)

        def _open_hit(self, item) -> None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                interview_id, start = data
                self._window.open_search_hit(str(interview_id), float(start))

        def _start_worker(self, fn, on_done, on_progress=None) -> None:
            worker = CallableWorker(fn, self)
            self._worker = worker
            if on_progress is not None:
                worker.progress.connect(on_progress)
            worker.done.connect(on_done)
            # Sem isto, cada etapa deixava um QThread terminado como filho do
            # dialogo (que fica cacheado pela sessao inteira) e, com ele, a
            # closure — na janela de temas, uma copia inteira das passagens.
            # `done` e emitido antes de `finished`, entao os tratadores ainda
            # veem self._worker.
            worker.finished.connect(lambda: self._retire_worker(worker))
            worker.start()

        def _retire_worker(self, worker) -> None:
            if self._worker is worker:
                self._worker = None
            worker.deleteLater()

        def closeEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            worker = self._worker
            if worker is not None and worker.isRunning():
                # Fechar cancela a etapa em curso (o Qwen nao fica rodando
                # na placa depois que a janela sumiu).
                try:
                    worker.request_cancel()
                except AttributeError:
                    pass
                try:
                    worker.done.disconnect()
                    worker.progress.disconnect()
                except (RuntimeError, TypeError):
                    pass
            event.accept()


    class WordSearchDialog(_SearchDialogBase):
        """Buscar palavras e expressoes (identidade A): utilitaria e
        minima — campo, ocorrencias exatas, nada de modelos/indices."""

        def __init__(self, window) -> None:
            super().__init__(window, "Buscar palavras")
            self.setMinimumSize(640, 420)
            layout = QVBoxLayout(self)
            row = QHBoxLayout()
            self.query_input = QLineEdit()
            self.query_input.setPlaceholderText("palavra ou expressao exata…")
            self.query_input.returnPressed.connect(self.run_search)
            row.addWidget(self.query_input, 1)
            search_button = QPushButton("Buscar")
            search_button.clicked.connect(self.run_search)
            row.addWidget(search_button)
            layout.addLayout(row)
            self._build_scope_widgets(layout)
            self.results = self._make_results_list()
            layout.addWidget(self.results, 1)
            bottom = QHBoxLayout()
            self.count_label = QLabel("")
            bottom.addWidget(self.count_label, 1)
            close_button = QPushButton("Fechar")
            close_button.clicked.connect(self.close)
            bottom.addWidget(close_button)
            layout.addLayout(bottom)

        def set_query(self, query: str) -> None:
            self.query_input.setText(query)
            self.run_search()

        def run_search(self) -> None:
            from . import search as _search
            ctx = self._window.context
            query = self.query_input.text().strip()
            if not query:
                self.count_label.setText(
                    "Digite uma palavra ou expressão para buscar.")
                return
            if ctx is None:
                return
            self._refresh_scope()
            ids = self._scope_ids()
            self.results.clear()
            if not ids:
                # Nunca o enganoso "0 ocorrencias" quando o problema e
                # falta de transcricao no escopo.
                self.count_label.setText(self._scope_text())
                return
            hits = _search.project_literal_search(ctx.paths, ids, query)
            for hit in hits:
                self._add_hit(self.results, "", hit)
            plural = "s" if len(hits) != 1 else ""
            self.count_label.setText(f"{len(hits)} ocorrencia{plural} exata{plural}")


    class ExploreDialog(_SearchDialogBase):
        """Perguntar às entrevistas (identidade B), v3 (2026-09-03): UM gesto em
        dois estagios. Estagio 1: os trechos que tratam do tema (busca por
        sentido v3 — passagens, encoder de recuperacao, hibrido com o literal,
        reordenador quando instalado, corte de relevancia), em segundos e em
        qualquer maquina. Estagio 2, so com o modelo de analise: a resposta
        escrita citando [n], cancelavel — os trechos ficam. Perguntas sobre o
        CONJUNTO (ask.question_kind) vao pelos resumos (visao geral), com o
        escape "responder pelos trechos mesmo assim". O botao "Encontrar
        trechos" morreu: era a mesma busca sem a redacao (decisao do usuario)."""

        _scope_subject = "A AI"

        def __init__(self, window) -> None:
            super().__init__(window, "✨ Buscar por sentido e perguntar")
            self.setMinimumSize(760, 560)
            self._last_result: dict[str, Any] | None = None
            self._current_query = ""
            self._current_ids: list[str] = []
            self._row_by_n: dict[int, int] = {}
            layout = QVBoxLayout(self)
            intro = QLabel(
                "Escreva uma pergunta ou um tema. Aparecem os trechos das entrevistas que "
                "tratam disso — pelo sentido, mesmo sem as palavras exatas, e em qualquer "
                "computador. Havendo placa NVIDIA, a AI também escreve uma resposta "
                "citando-os [n]. Tudo local — nada sai do seu computador.")
            intro.setWordWrap(True)
            intro.setStyleSheet(_style_muted())
            layout.addWidget(intro)
            row = QHBoxLayout()
            self.query_input = QLineEdit()
            self.query_input.setPlaceholderText("uma pergunta ou um tema — ex.: dificuldade para entrar em condomínios")
            self.query_input.returnPressed.connect(self.run_question)
            row.addWidget(self.query_input, 1)
            # O rótulo segue a máquina (_announce_readiness): onde a resposta
            # escrita não roda, o botão não pode dizer "Perguntar".
            self.ask_button = QPushButton("✨ Perguntar")
            self.ask_button.setToolTip(
                "Encontra os trechos em segundos e, quando o modelo de análise está "
                "disponível, escreve a resposta citando-os.")
            self.ask_button.clicked.connect(self.run_question)
            row.addWidget(self.ask_button)
            row.addWidget(QLabel("até"))
            from . import app_settings as _settings_n
            self.max_results_spin = QSpinBox()
            lo, hi = _settings_n.SEARCH_MAX_RESULTS_RANGE
            self.max_results_spin.setRange(lo, hi)
            self.max_results_spin.setValue(_settings_n.search_max_results())
            self.max_results_spin.setToolTip(
                "Quantos trechos no máximo. A busca só devolve os que tratam do tema — "
                "pode vir menos.")
            self.max_results_spin.valueChanged.connect(self._save_max_results)
            row.addWidget(self.max_results_spin)
            row.addWidget(QLabel("trechos"))
            layout.addLayout(row)
            self._build_scope_widgets(layout)
            # "Nesta máquina: …" — o que roda aqui, dito na abertura.
            self.state_label = QLabel("")
            self.state_label.setWordWrap(True)
            self.state_label.setStyleSheet(_style_muted())
            layout.addWidget(self.state_label)
            # Faixa de rota: pergunta sobre o conjunto / nada responde.
            self.route_banner = QFrame()
            self.route_banner.setVisible(False)
            self.route_banner.setStyleSheet(f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}")
            route_layout = QHBoxLayout(self.route_banner)
            route_layout.setContentsMargins(10, 6, 10, 6)
            self.route_label = QLabel("")
            self.route_label.setWordWrap(True)
            route_layout.addWidget(self.route_label, 1)
            self.route_button = QPushButton("")
            self.route_button.setVisible(False)
            route_layout.addWidget(self.route_button)
            layout.addWidget(self.route_banner)
            self.answer_view = QTextBrowser()
            self.answer_view.setOpenLinks(False)
            self.answer_view.setOpenExternalLinks(False)
            self.answer_view.anchorClicked.connect(self._on_answer_anchor)
            self.answer_view.setVisible(False)
            self.answer_view.setMaximumHeight(220)
            layout.addWidget(self.answer_view)
            self.results = self._make_results_list()
            self.results.setWordWrap(True)
            layout.addWidget(self.results, 1)
            self.prepare_button = QPushButton("Preparar")
            self.prepare_button.setVisible(False)
            self.prepare_button.clicked.connect(self._prepare)
            layout.addWidget(self.prepare_button)
            bottom = QHBoxLayout()
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            bottom.addWidget(self.status_label, 1)
            self.cancel_answer_button = QPushButton("Cancelar a resposta (os trechos ficam)")
            self.cancel_answer_button.setVisible(False)
            self.cancel_answer_button.clicked.connect(self._cancel_answer)
            bottom.addWidget(self.cancel_answer_button)
            close_button = QPushButton("Fechar")
            close_button.clicked.connect(self.close)
            bottom.addWidget(close_button)
            layout.addLayout(bottom)
            self._announce_readiness()

        # ---- estado na abertura -------------------------------------------
        def _llm_state(self) -> tuple[str, str, float]:
            try:
                return self._window._capability_state("resumo_perguntar")
            except Exception:  # noqa: BLE001 - sonda nunca derruba a janela
                return "pronta", "", 0.0

        def _announce_readiness(self) -> None:
            """Estado visivel JA NA ABERTURA (feedback 2026-08-30): uma linha
            "Nesta máquina: …" com os dois estagios. O botao nunca fica cinza —
            os trechos funcionam em qualquer maquina; so a resposta escrita
            depende do modelo de analise (placa NVIDIA). IDEMPOTENTE: o dialogo
            e cacheado pela janela e cada reabertura re-anuncia."""
            from . import search as _search
            resumo_estado, resumo_motivo, resumo_gb = self._llm_state()
            try:
                encoder_ok = _search.encoder_cached()
                reranker_ok = _search.reranker_cached()
                _busca_estado, _m, busca_gb = self._window._capability_state("busca_semantica")
            except Exception:  # noqa: BLE001
                encoder_ok, reranker_ok, busca_gb = True, False, 0.5
            partes: list[str] = []
            if encoder_ok:
                partes.append("trechos pelo sentido — pronta"
                              + (" (com reordenador)" if reranker_ok else ""))
            else:
                partes.append(f"trechos pelo sentido — falta baixar (~{fmt_gb(busca_gb)}; o clique oferece)")
            if resumo_estado == "incompativel":
                partes.append("resposta escrita — não roda neste computador: precisa de placa "
                              "NVIDIA (os trechos funcionam normalmente)")
            elif resumo_estado == "instalavel":
                partes.append(f"resposta escrita — falta baixar o modelo de análise "
                              f"(~{fmt_gb(resumo_gb)}; o clique oferece)")
            else:
                partes.append("resposta escrita — pronta (cerca de 1 min)")
            try:
                aviso_resumo = self._window._capability_warning("resumo_perguntar")
            except Exception:  # noqa: BLE001
                aviso_resumo = ""
            texto = "Nesta máquina: " + " · ".join(partes) + "."
            if resumo_estado != "incompativel" and aviso_resumo:
                texto += f" Atenção: a resposta escrita {aviso_resumo} — por sua conta e risco."
            self.state_label.setText(texto)
            # Onde a resposta escrita não roda, o botão promete só o que entrega.
            if resumo_estado == "incompativel":
                self.ask_button.setText("✨ Buscar trechos")
                self.ask_button.setToolTip(
                    "Encontra em segundos os trechos que tratam do que você escreveu.\n"
                    "A resposta escrita precisa de placa NVIDIA — neste computador, "
                    "ficam os trechos.")
            else:
                self.ask_button.setText("✨ Perguntar")
                self.ask_button.setToolTip(
                    "Encontra os trechos em segundos e, quando o modelo de análise está "
                    "disponível, escreve a resposta citando-os.")
            self.ask_button.setEnabled(True)
            self.status_label.setText("")

        def _save_max_results(self, value: int) -> None:
            from . import app_settings as _settings_n
            try:
                _settings_n.save({"search_max_results": int(value)})
            except Exception as exc:  # noqa: BLE001 - preferencia nunca bloqueia
                _logger.warning("Nao foi possivel guardar 'até N trechos': %s", exc)

        # ---- gating comum ----------------------------------------------------
        def _ready_query(self) -> tuple[list[str], str] | None:
            """Contexto, consulta, escopo com transcricao e encoder baixado.
            None = ainda nao da — sempre com o motivo visivel, nunca clique
            morto. Indices desatualizados sao refeitos DENTRO da busca."""
            from . import search as _search
            ctx = self._window.context
            query = self.query_input.text().strip()
            if ctx is None or not query or (self._worker and self._worker.isRunning()):
                return None
            self._refresh_scope()
            ids = self._scope_ids()
            self._clear_results()
            if not ids:
                self.status_label.setText(self._scope_text())
                return None
            if not _search.encoder_cached():
                self.prepare_button.setText("Preparar (baixa um modelo de ~0,5 GB, uma vez)")
                self.prepare_button.setVisible(True)
                self.status_label.setText(
                    "Esta janela usa um modelo pequeno e local que ainda não foi baixado.")
                return None
            return ids, query

        def _clear_results(self) -> None:
            self.results.clear()
            self._row_by_n = {}
            self.answer_view.clear()
            self.answer_view.setVisible(False)
            self.route_banner.setVisible(False)
            self.route_button.setVisible(False)
            self.prepare_button.setVisible(False)
            self.cancel_answer_button.setVisible(False)
            self.status_label.setText("")

        # ---- estagio 1: trechos ---------------------------------------------
        def run_question(self) -> None:
            from . import ask as _ask
            if not self.query_input.text().strip():
                self.status_label.setText("Escreva uma pergunta ou um tema antes de perguntar.")
                return
            if self._worker and self._worker.isRunning():
                self.status_label.setText("Aguarde a consulta atual terminar.")
                return
            ready = self._ready_query()
            if ready is None:
                return
            ids, query = ready
            self._current_ids, self._current_query = ids, query
            paths = self._window.context.paths
            max_results = int(self.max_results_spin.value())
            self.status_label.setText("Procurando os trechos que tratam disso…")
            # Cancelar durante a reordenacao devolve a lista por semelhanca
            # (ja calculada) em vez de esperar 10-40 s numa maquina sem placa.
            self.cancel_answer_button.setText("Cancelar a reordenação (mostrar só por semelhança)")
            self.cancel_answer_button.setVisible(True)

            def fn(emit, should_cancel):
                return _ask.retrieve(paths, ids, query, max_results=max_results,
                                     progress_callback=emit, should_cancel=should_cancel)

            self._start_worker(
                fn, self._on_retrieve_done,
                on_progress=lambda d: self.status_label.setText(str(d.get("message") or "")))

        def _on_retrieve_done(self, result, error: str) -> None:
            self.cancel_answer_button.setVisible(False)
            self.cancel_answer_button.setText("Cancelar a resposta (os trechos ficam)")
            if error:
                self.status_label.setText(f"Busca indisponível: {error}")
                return
            result = result or {}
            self._last_result = result
            self._render_sections(result)
            self.status_label.setText(results_footer_text(result))
            if result.get("rerank_cancelled"):
                self.status_label.setText(
                    results_footer_text(result) + " Reordenação cancelada: ordem só por semelhança.")
                return
            if result.get("kind") == "global":
                self._route_global(result)
                return
            self._maybe_answer(result)

        def _render_sections(self, result: dict[str, Any]) -> None:
            self.results.clear()
            self._row_by_n = {}
            n = 0
            for section in result.get("sections") or []:
                if section.get("weak"):
                    titulo = "Nada realmente próximo — os trechos abaixo são só os menos distantes"
                else:
                    titulo = f"{section.get('label')} ({len(section.get('hits') or [])})"
                header = QListWidgetItem(titulo)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                fonte = header.font()
                fonte.setBold(True)
                header.setFont(fonte)
                self.results.addItem(header)
                for hit in section.get("hits") or []:
                    n += 1
                    self._add_passage(n, hit)

        def _add_passage(self, n: int, hit: dict[str, Any]) -> None:
            titulo = self._friendly_title(str(hit.get("interview_id") or ""))
            texto = " ".join(str(hit.get("text") or "").split())
            item = QListWidgetItem(f"[{n}]  {titulo} · {format_clock(float(hit.get('start', 0) or 0))}\n{texto}")
            item.setData(Qt.ItemDataRole.UserRole, (hit.get("interview_id"), hit.get("start", 0)))
            self.results.addItem(item)
            self._row_by_n[n] = self.results.count() - 1

        # ---- estagio 2: resposta ----------------------------------------------
        def _maybe_answer(self, result: dict[str, Any]) -> None:
            from . import ask as _ask
            if not result.get("trechos"):
                return
            if not result.get("worth_answer"):
                # Nao gastar ~1 min de AI com trechos que nao respondem.
                self.status_label.setText(results_footer_text(result) + " " + _ask.SEM_TRECHOS)
                return
            estado, _motivo, _gb = self._llm_state()
            if estado == "incompativel":
                self.status_label.setText(
                    results_footer_text(result) + " Nesta máquina a AI encontra os trechos, "
                    "mas não escreve a resposta (precisa de placa NVIDIA).")
                return
            if estado == "instalavel" and not self._window._ensure_llm_model():
                self.status_label.setText(
                    results_footer_text(result) + " A resposta escrita fica para quando o "
                    "modelo de análise estiver instalado.")
                return
            self._start_answer(result)

        def _start_answer(self, result: dict[str, Any]) -> None:
            from . import ask as _ask
            paths = self._window.context.paths
            question = self._current_query
            trechos = list(result.get("trechos") or [])
            self.cancel_answer_button.setVisible(True)
            self.status_label.setText("Escrevendo a resposta com base nos trechos (cerca de 1 min)…")

            def fn(emit, should_cancel):
                return _ask.answer_from_trechos(paths, question, trechos,
                                                progress_callback=emit, should_cancel=should_cancel)

            self._start_worker(
                fn, self._on_answer_done,
                on_progress=lambda d: self.status_label.setText(str(d.get("message") or "")))

        def _cancel_answer(self) -> None:
            worker = self._worker
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                self.status_label.setText("Cancelando a resposta…")

        def _on_answer_done(self, result, error: str) -> None:
            self.cancel_answer_button.setVisible(False)
            rodape = results_footer_text(self._last_result or {})
            worker = self._worker
            if worker is not None and worker.cancelled.is_set():
                self.status_label.setText("Resposta cancelada — os trechos acima continuam válidos.")
                return
            if error:
                self.status_label.setText(f"{rodape} Não foi possível responder: {error}")
                return
            payload = result or {}
            if payload.get("erro"):
                self.status_label.setText(f"{rodape} {payload['erro']}")
                return
            resposta = str(payload.get("resposta") or "")
            n = len((self._last_result or {}).get("trechos") or [])
            self.answer_view.setHtml(render_answer_html(resposta, n))
            self.answer_view.setVisible(True)
            self.status_label.setText(rodape)

        def _on_answer_anchor(self, url) -> None:
            alvo = str(url.toString() if hasattr(url, "toString") else url)
            if alvo.startswith("trecho:"):
                try:
                    n = int(alvo.split(":", 1)[1])
                except ValueError:
                    return
                row = self._row_by_n.get(n)
                if row is not None:
                    self.results.setCurrentRow(row)
                    self.results.scrollToItem(self.results.item(row))
            elif alvo.startswith("entrevista:"):
                self._window.open_search_hit(alvo.split(":", 1)[1], 0.0)

        # ---- pergunta sobre o conjunto ---------------------------------------
        def _route_global(self, result: dict[str, Any]) -> None:
            from . import ask as _ask
            ctx = self._window.context
            ids = list(self._current_ids)
            titles = {iid: self._friendly_title(iid) for iid in ids}
            resumos = _ask.resumos_for_scope(ctx.paths, ids, titles)
            com, total = len(resumos), len(ids)
            estado, _motivo, _gb = self._llm_state()
            self.route_banner.setVisible(True)
            slot = getattr(self, "_route_slot", None)
            if slot is not None:
                try:
                    self.route_button.clicked.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
                self._route_slot = None
            if com == 0:
                self.route_label.setText(
                    "Pergunta sobre o conjunto: para responder, a AI usa os resumos por "
                    f"entrevista — nenhuma das {total} tem resumo ainda. Os trechos abaixo "
                    "são só os menos distantes.")
                if estado != "incompativel":
                    self.route_button.setText(f"Resumir as {total} entrevistas agora")
                    self.route_button.setVisible(True)
                    self._route_slot = lambda: self._window.run_summarize_job(ids=ids)
                    self.route_button.clicked.connect(self._route_slot)
                return
            if estado == "incompativel":
                self.route_label.setText(
                    f"Pergunta sobre o conjunto: a AI responderia pelos resumos ({com} das {total} "
                    "têm resumo), mas nesta máquina a resposta escrita não roda (precisa de placa "
                    "NVIDIA). Os trechos abaixo são só os menos distantes.")
                return
            if estado == "instalavel" and not self._window._ensure_llm_model():
                self.route_label.setText(
                    "Pergunta sobre o conjunto: a AI responde pelos resumos quando o modelo de "
                    "análise estiver instalado.")
                return
            self.route_label.setText(
                f"Pergunta sobre o conjunto: a AI responde pelos resumos ({com} das {total} "
                "têm resumo).")
            self.route_button.setText("Responder pelos trechos mesmo assim")
            self.route_button.setVisible(True)
            self._route_slot = lambda: self._answer_by_passages_anyway(result)
            self.route_button.clicked.connect(self._route_slot)
            self._start_visao_geral(ids, titles)

        def _answer_by_passages_anyway(self, result: dict[str, Any]) -> None:
            if self._worker and self._worker.isRunning():
                self._worker.request_cancel()
            self.route_banner.setVisible(False)
            self.route_button.setVisible(False)
            self._maybe_answer(result)

        def _start_visao_geral(self, ids: list[str], titles: dict[str, str]) -> None:
            from . import ask as _ask
            paths = self._window.context.paths
            question = self._current_query
            self.cancel_answer_button.setVisible(True)
            self.status_label.setText("Lendo os resumos das entrevistas…")

            def fn(emit, should_cancel):
                return _ask.run_visao_geral(paths, ids, question, progress_callback=emit,
                                            should_cancel=should_cancel, titles=titles)

            self._start_worker(
                fn, self._on_visao_done,
                on_progress=lambda d: self.status_label.setText(str(d.get("message") or "")))

        def _on_visao_done(self, result, error: str) -> None:
            self.cancel_answer_button.setVisible(False)
            worker = self._worker
            if worker is not None and worker.cancelled.is_set():
                self.status_label.setText("Visão geral cancelada — os trechos acima continuam válidos.")
                return
            if error:
                self.status_label.setText(f"Não foi possível responder pelos resumos: {error}")
                return
            payload = result or {}
            if payload.get("erro"):
                self.status_label.setText(str(payload["erro"]))
                return
            citadas = [str(c) for c in (payload.get("citadas") or [])]
            self.answer_view.setHtml(render_answer_html(str(payload.get("resposta") or ""), 0, tuple(citadas)))
            self.answer_view.setVisible(True)
            self.results.clear()
            self._row_by_n = {}
            if citadas:
                header = QListWidgetItem(f"Entrevistas citadas ({len(citadas)})")
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                fonte = header.font()
                fonte.setBold(True)
                header.setFont(fonte)
                self.results.addItem(header)
                for iid in citadas:
                    item = QListWidgetItem(f"[{iid}]  {self._friendly_title(iid)}")
                    item.setData(Qt.ItemDataRole.UserRole, (iid, 0.0))
                    self.results.addItem(item)
            sem = payload.get("sem_resumo") or []
            com = payload.get("com_resumo") or []
            nota = f"Com base nos resumos de {len(com)} entrevista(s)."
            if sem:
                nota += f" {len(sem)} sem resumo ficaram de fora."
            self.status_label.setText(nota)

        # ---- preparo do encoder ------------------------------------------------
        def _prepare(self) -> None:
            from . import search as _search
            ctx = self._window.context
            if ctx is None or (self._worker and self._worker.isRunning()):
                return
            # Indexar o ESCOPO escolhido: o rotulo do botao prometia o
            # escopo e o codigo indexava o projeto inteiro (num projeto
            # grande, "1 arquivo, ~1 min" virava dezenas de minutos).
            self._refresh_scope()
            ids = self._scope_ids() or self._project_ids()
            paths = ctx.paths
            need_download = not _search.encoder_cached()
            self.prepare_button.setEnabled(False)

            def fn(emit):
                if need_download:
                    from . import model_manager as _mm
                    if _mm.download_optional_model("search_encoder", progress_callback=emit) != 0:
                        raise RuntimeError("download do modelo falhou")
                return _search.build_indexes(paths, ids, progress_callback=emit)

            self._start_worker(
                fn, self._on_prepare_done,
                on_progress=lambda d: self.status_label.setText(str(d.get("message") or "")))

        def _on_prepare_done(self, result, error: str) -> None:
            self.prepare_button.setEnabled(True)
            if error:
                self.status_label.setText(f"Não foi possível preparar: {error}")
                return
            self.prepare_button.setVisible(False)
            # O encoder baixado aqui muda o estado das capacidades na
            # janela principal (tooltips/notas) — invalidar o cache dela
            # e re-anunciar o estado desta janela.
            try:
                self._window._invalidate_capability_cache()
                self._announce_readiness()
            except Exception:  # noqa: BLE001 - estado da janela nunca derruba o preparo
                pass
            self.status_label.setText("Busca pronta.")
            self.run_question()


    class ThemesDialog(_SearchDialogBase):
        """Temas das entrevistas (Parte B, 2026-09-03): agrupa TODAS as
        passagens do escopo por semelhanca de sentido (themes.discover —
        numpy, qualquer maquina, segundos) e lista os temas mais tratados com
        todos os seus trechos, sem teto. Nomes: termos caracteristicos na
        hora; com o modelo de analise, nomes e descricoes pela AI em SEGUNDO
        PLANO (a janela nunca espera pela LLM — regra do plano de camadas).
        Codificacao (coding.py): um ou mais codigos por trecho, tema inteiro
        como codigo, exportacao .qualilab (QualiLab) ou CSV. Duplo clique no
        trecho abre a entrevista no ponto."""

        _scope_subject = "A descoberta de temas"
        OUTROS_ID = "__outros__"

        def __init__(self, window) -> None:
            super().__init__(window, "✨ Temas das entrevistas")
            self.setMinimumSize(980, 640)
            self._payload: dict[str, Any] | None = None
            self._codes: list[dict[str, Any]] = []
            self._codings: list[dict[str, Any]] = []
            self._codes_dirty = False
            # Retrato do codebook e da codificacao ANTES da ultima aplicacao em
            # massa. Vale so para ela: qualquer outra mudanca o descarta.
            self._bulk_undo: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None
            self._naming = False
            self._speakers: list[str] | None = None   # None = todos os falantes
            self._inventory: list[dict[str, Any]] = []
            layout = QVBoxLayout(self)
            intro = QLabel(
                "Agrupa os trechos das entrevistas por semelhança de sentido e lista os temas "
                "mais tratados, cada um com todos os seus trechos. Dá para renomear, juntar, "
                "aplicar códigos (um ou mais por trecho) e exportar para o QualiLab. "
                "Tudo local — nada sai do seu computador.")
            intro.setWordWrap(True)
            intro.setStyleSheet(_style_muted())
            layout.addWidget(intro)
            self._build_scope_widgets(layout)
            # "Quem entra": em entrevista não se codifica quem pergunta, em
            # grupo focal não se codifica quem modera — e os nomes desses
            # papéis variam. O app pergunta (decisão do usuário 2026-09-03).
            quem_row = QHBoxLayout()
            quem_row.addWidget(QLabel("Quem entra:"))
            self.speakers_label = QLabel("todos os falantes")
            self.speakers_label.setWordWrap(True)
            quem_row.addWidget(self.speakers_label, 1)
            self.speakers_button = QPushButton("Escolher…")
            self.speakers_button.setToolTip(
                "Escolha os falantes que contam nos temas e na codificação. A fala de quem "
                "ficar de fora continua na transcrição e no arquivo exportado, como contexto — "
                "só não influencia o agrupamento nem recebe código.")
            self.speakers_button.clicked.connect(self._choose_speakers)
            quem_row.addWidget(self.speakers_button)
            layout.addLayout(quem_row)
            row = QHBoxLayout()
            row.addWidget(QLabel("Quantos temas:"))
            self.n_themes_spin = QSpinBox()
            self.n_themes_spin.setRange(0, 40)
            self.n_themes_spin.setSpecialValueText("automático")
            self.n_themes_spin.setValue(0)
            self.n_themes_spin.setToolTip(
                "Automático depende da quantidade de trechos. Menos temas = mais amplos; "
                "mais temas = mais finos. Grupos com menos de 3 trechos — ou com menos de 6 "
                "quando todos vêm de uma única entrevista — viram «sem tema definido», por "
                "isso podem aparecer menos temas do que você pediu.")
            row.addWidget(self.n_themes_spin)
            self.discover_button = QPushButton("Descobrir os temas")
            self.discover_button.setToolTip(
                "Lê os trechos de todas as entrevistas do escopo e agrupa por semelhança "
                "(segundos; a primeira vez prepara o índice de busca).")
            self.discover_button.clicked.connect(self.run_discover)
            row.addWidget(self.discover_button)
            self.names_button = QPushButton("✨ Nomear os temas com a AI")
            self.names_button.setVisible(False)
            self.names_button.clicked.connect(lambda: self._start_naming(user_click=True))
            row.addWidget(self.names_button)
            self.cancel_button = QPushButton("Cancelar")
            self.cancel_button.setVisible(False)
            self.cancel_button.clicked.connect(self._cancel)
            row.addWidget(self.cancel_button)
            row.addStretch(1)
            layout.addLayout(row)
            self.state_label = QLabel("")
            self.state_label.setWordWrap(True)
            self.state_label.setStyleSheet(_style_muted())
            layout.addWidget(self.state_label)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            self.themes_list = QListWidget()
            self.themes_list.setWordWrap(True)
            self.themes_list.currentRowChanged.connect(self._on_theme_selected)
            left_layout.addWidget(self.themes_list, 1)
            left_buttons = QHBoxLayout()
            self.rename_button = QPushButton("Renomear…")
            self.rename_button.clicked.connect(self._rename)
            left_buttons.addWidget(self.rename_button)
            self.merge_button = QPushButton("Juntar com outro tema…")
            self.merge_button.clicked.connect(self._merge)
            left_buttons.addWidget(self.merge_button)
            left_layout.addLayout(left_buttons)
            # O passo que faltava entre descobrir os temas e ter um codebook
            # utilizável: aplicar tema por tema, marcando trecho por trecho,
            # é o que o relato de campo chamou de "não dá para aplicar todos
            # os códigos de uma só vez".
            all_buttons = QHBoxLayout()
            self.code_all_button = QPushButton("Aplicar todos os temas como códigos…")
            self.code_all_button.setToolTip(
                "Cria um código por tema (reaproveitando os que já existem com o mesmo nome)\n"
                "e o aplica a todos os trechos daquele tema, de uma vez só.")
            self.code_all_button.clicked.connect(self._apply_all_themes_as_codes)
            all_buttons.addWidget(self.code_all_button)
            self.undo_bulk_button = QPushButton("Desfazer")
            self.undo_bulk_button.setToolTip(
                "Devolve o codebook e a codificação ao que eram antes da última aplicação em massa.")
            self.undo_bulk_button.setVisible(False)
            self.undo_bulk_button.clicked.connect(self._undo_bulk_coding)
            all_buttons.addWidget(self.undo_bulk_button)
            left_layout.addLayout(all_buttons)
            splitter.addWidget(left)
            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 0, 0)
            self.theme_title = QLabel("")
            self.theme_title.setWordWrap(True)
            fonte = self.theme_title.font()
            fonte.setBold(True)
            self.theme_title.setFont(fonte)
            right_layout.addWidget(self.theme_title)
            self.theme_desc = QLabel("")
            self.theme_desc.setWordWrap(True)
            self.theme_desc.setStyleSheet(_style_muted())
            right_layout.addWidget(self.theme_desc)
            self.passages = QListWidget()
            self.passages.setWordWrap(True)
            self.passages.setToolTip(
                "Duplo clique abre a entrevista neste trecho; a caixa marca o trecho para codificar.")
            self.passages.itemActivated.connect(self._open_hit)
            right_layout.addWidget(self.passages, 1)
            right_buttons = QHBoxLayout()
            self.code_theme_button = QPushButton("Aplicar código aos trechos marcados…")
            self.code_theme_button.setToolTip(
                "Cria (ou reutiliza) um código e o aplica a todos os trechos marcados deste tema.")
            self.code_theme_button.clicked.connect(self._apply_code_to_checked)
            right_buttons.addWidget(self.code_theme_button)
            self.passage_codes_button = QPushButton("Códigos deste trecho…")
            self.passage_codes_button.setToolTip(
                "Acrescenta ou tira um código do trecho selecionado (um trecho pode ter vários).")
            self.passage_codes_button.clicked.connect(self._passage_codes)
            right_buttons.addWidget(self.passage_codes_button)
            self.export_button = QPushButton("Exportar codificação…")
            self.export_button.setToolTip(
                "Grava um projeto do QualiLab (.qualilab: um documento por entrevista, códigos e "
                "trechos codificados) ou uma planilha CSV.")
            self.export_button.clicked.connect(self._export)
            right_buttons.addWidget(self.export_button)
            right_layout.addLayout(right_buttons)
            splitter.addWidget(right)
            splitter.setSizes([340, 640])
            layout.addWidget(splitter, 1)
            bottom = QHBoxLayout()
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            bottom.addWidget(self.status_label, 1)
            close_button = QPushButton("Fechar")
            close_button.clicked.connect(self.close)
            bottom.addWidget(close_button)
            layout.addLayout(bottom)
            self._announce_readiness()

        # ---- estado ---------------------------------------------------------
        def _paths(self):
            ctx = self._window.context
            return ctx.paths if ctx is not None else None

        def _llm_state(self) -> tuple[str, str, float]:
            try:
                return self._window._capability_state("resumo_perguntar")
            except Exception:  # noqa: BLE001 - sonda nunca derruba a janela
                return "pronta", "", 0.0

        def _announce_readiness(self) -> None:
            """Uma linha "Nesta máquina: …" na abertura, idempotente. O
            agrupamento roda em qualquer maquina; so os NOMES pela AI dependem
            do modelo de analise (placa NVIDIA)."""
            from . import search as _search
            try:
                encoder_ok = _search.encoder_cached()
                _e, _m, busca_gb = self._window._capability_state("busca_semantica")
            except Exception:  # noqa: BLE001
                encoder_ok, busca_gb = True, 0.5
            estado, _motivo, resumo_gb = self._llm_state()
            partes = ["temas por semelhança — pronta (segundos)" if encoder_ok else
                      f"temas por semelhança — falta baixar (~{fmt_gb(busca_gb)}; o clique oferece)"]
            if estado == "incompativel":
                partes.append("nomes pela AI — não roda neste computador (precisa de placa NVIDIA); "
                              "os temas ficam com os termos característicos")
            elif estado == "instalavel":
                partes.append(f"nomes pela AI — falta baixar o modelo de análise (~{fmt_gb(resumo_gb)})")
            else:
                partes.append("nomes pela AI — pronta (alguns minutos, em segundo plano)")
            self.state_label.setText("Nesta máquina: " + " · ".join(partes) + ".")

        def showEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            super().showEvent(event)
            if self._payload is None and self._window.context is not None:
                self._load_existing()

        def closeEvent(self, event) -> None:  # noqa: N802 - assinatura Qt
            # A base cancela o worker e desliga os sinais — o tratador de fim
            # nao roda mais, entao os botoes tem de voltar AQUI: senao
            # "Descobrir os temas" ficava cinza para sempre (a janela e
            # cacheada) e o "Cancelar" seguia visivel sem efeito.
            super().closeEvent(event)
            self._idle_buttons()

        def _idle_buttons(self) -> None:
            self.discover_button.setEnabled(True)
            self.cancel_button.setVisible(False)
            self._naming = False

        def reset_for_project(self) -> None:
            """Esquece temas e códigos do projeto ANTERIOR (a janela é cacheada
            pela janela principal). Sem isto, renomear um tema depois de trocar
            de projeto gravaria os temas do projeto antigo dentro do novo —
            `_save_themes`/`_save_coding` escrevem sempre no projeto ATUAL."""
            worker = self._worker
            if worker is not None and worker.isRunning():
                try:
                    worker.request_cancel()
                    worker.done.disconnect()
                    worker.progress.disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
            self._worker = None
            self._payload = None
            self._codes = []
            self._codings = []
            self._bulk_undo = None
            if hasattr(self, "undo_bulk_button"):
                self.undo_bulk_button.setVisible(False)
            self._speakers = None
            self._inventory = []
            self._sync_speakers_label()
            self.themes_list.clear()
            self.passages.clear()
            self.theme_title.setText("")
            self.theme_desc.setText("")
            self.names_button.setVisible(False)
            self._idle_buttons()
            self.status_label.setText("")
            self._announce_readiness()
            if self.isVisible() and self._window.context is not None:
                self._load_existing()

        def _load_existing(self) -> None:
            from . import themes as _themes
            paths = self._paths()
            if paths is None:
                return
            self._load_coding()
            self._load_speakers_choice()
            payload = _themes.load_themes(paths)
            if not payload:
                # Primeira vez: dizer qual e o primeiro passo, senao a janela
                # abre com duas listas vazias e nenhuma orientacao.
                self.status_label.setText(
                    "Comece por «Descobrir os temas» — os trechos de cada tema aparecem à "
                    "direita, e aí dá para aplicar códigos e exportar.")
                return
            self._payload = payload
            self._render()
            quando = str(payload.get("created_at") or "").replace("T", " ")
            n_ids = len(payload.get("interview_ids") or [])
            self.status_label.setText(
                f"Temas descobertos{' em ' + quando if quando else ''} para {n_ids} entrevista(s). "
                "«Descobrir os temas» refaz com o escopo atual.")
            # Ao ABRIR, a AI nunca começa sozinha: ocupar a placa por minutos
            # sem o usuário ter pedido nada contraria "dizer o custo antes do
            # clique" (e ainda bloquearia «Descobrir os temas»). Só oferece.
            self._maybe_offer_naming(auto=False)

        def _load_coding(self) -> None:
            from . import coding as _coding
            from . import research_context as _rc
            paths = self._paths()
            if paths is None:
                return
            self._codes = _coding.load_codebook(paths)
            self._codings = _coding.load_codings(paths)
            self._codes_dirty = False
            if not self._codes:
                # Sementes do "## Codebook inicial" do contexto da pesquisa
                # (em memoria; gravadas na primeira codificacao).
                try:
                    for name, desc in _coding.parse_codebook_seed(_rc.load_research_context(paths)):
                        _coding.add_code(self._codes, name, desc)
                except Exception:  # noqa: BLE001 - contexto ausente/ilegivel = sem sementes
                    pass

        # ---- quem entra (falantes) --------------------------------------------
        def _speakers_set(self) -> set[str] | None:
            return None if self._speakers is None else set(self._speakers)

        def _load_speakers_choice(self) -> None:
            """Lê a escolha gravada no projeto. Ausente = ainda não perguntado."""
            ctx = self._window.context
            escolha = (ctx.project.get("coding_speakers") if ctx is not None else None)
            self._speakers = [str(s) for s in escolha] if isinstance(escolha, list) else None
            self._sync_speakers_label()

        def _save_speakers_choice(self) -> None:
            ctx = self._window.context
            if ctx is None:
                return
            ctx.project["coding_speakers"] = list(self._speakers) if self._speakers is not None else None
            ctx.project["coding_speakers_asked"] = True
            try:
                app_service.save_project_metadata(ctx)
            except Exception as exc:  # noqa: BLE001 - escolha nunca derruba a janela
                _logger.warning("Nao foi possivel guardar 'quem entra': %s", exc)

        def _speaker_inventory(self) -> list[dict[str, Any]]:
            """Falantes do escopo, com o peso de cada um (relê a cada abertura:
            identificar vozes muda os rótulos por fora desta janela)."""
            from . import search as _search
            from . import speakers as _speakers
            paths = self._paths()
            if paths is None:
                return []
            self._refresh_scope()
            ids = self._scope_ids() or self._project_ids()
            turns_by_id = {iid: _search.load_source_turns(paths, iid) for iid in ids}
            self._inventory = _speakers.speaker_inventory(turns_by_id)
            return self._inventory

        def _sync_speakers_label(self) -> None:
            from . import speakers as _speakers
            self.speakers_label.setText(
                _speakers.describe_selection(self._inventory, self._speakers))

        def _choose_speakers(self, primeira_vez: bool = False) -> bool:
            """Janela de escolha; True se o usuário confirmou."""
            from . import speakers as _speakers
            inventario = self._speaker_inventory()
            if not inventario:
                self.status_label.setText(
                    "Nenhuma transcrição no escopo para ler os falantes.")
                return False
            rotulos = [i["label"] for i in inventario]
            marcados = set(self._speakers) if self._speakers is not None else (
                set(_speakers.default_selection(rotulos)) if primeira_vez else set(rotulos))
            dialogo = QDialog(self)
            dialogo.setWindowTitle("Quem entra na análise")
            dlayout = QVBoxLayout(dialogo)
            explicacao = QLabel(
                "Marque os falantes que contam nos temas e na codificação. Em entrevistas "
                "não se costuma codificar quem pergunta; em grupos focais, quem modera — "
                "mas a decisão é sua.\n\nA fala de quem ficar de fora continua na "
                "transcrição e no arquivo exportado, como contexto: ela só deixa de "
                "influenciar o agrupamento e de receber código.")
            explicacao.setWordWrap(True)
            dlayout.addWidget(explicacao)
            lista = QListWidget()
            sugeridos = _speakers.suggest_excluded(rotulos)
            for item_inv in inventario:
                rotulo = item_inv["label"]
                texto = (f"{rotulo} — {item_inv['turns']} turnos, {item_inv['words']} palavras, "
                         f"{item_inv['interviews']} entrevista(s)")
                if rotulo in sugeridos:
                    texto += "  · sugerido de fora (parece quem conduz)"
                item = QListWidgetItem(texto)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setData(Qt.ItemDataRole.UserRole, rotulo)
                item.setCheckState(Qt.CheckState.Checked if rotulo in marcados
                                   else Qt.CheckState.Unchecked)
                lista.addItem(item)
            dlayout.addWidget(lista, 1)
            aviso = QLabel("")
            aviso.setWordWrap(True)
            aviso.setStyleSheet(f"color: {ui_tokens.WARN};")
            dlayout.addWidget(aviso)
            botoes = QHBoxLayout()
            botoes.addStretch(1)
            cancelar = QPushButton("Cancelar")
            cancelar.clicked.connect(dialogo.reject)
            botoes.addWidget(cancelar)
            confirmar = QPushButton("Usar estes falantes")
            confirmar.setDefault(True)
            botoes.addWidget(confirmar)
            dlayout.addLayout(botoes)

            def _confirmar() -> None:
                escolhidos = [str(lista.item(r).data(Qt.ItemDataRole.UserRole))
                              for r in range(lista.count())
                              if lista.item(r).checkState() == Qt.CheckState.Checked]
                if not escolhidos:
                    aviso.setText("Marque ao menos um falante — sem nenhum não há o que agrupar.")
                    return
                dialogo._escolhidos = escolhidos  # type: ignore[attr-defined]
                dialogo.accept()

            confirmar.clicked.connect(_confirmar)
            if dialogo.exec() != QDialog.DialogCode.Accepted:
                return False
            escolhidos = list(getattr(dialogo, "_escolhidos", rotulos))
            # Todos marcados = "todos os falantes" (None), para o índice não
            # ganhar um escopo que não muda nada.
            self._speakers = None if set(escolhidos) == set(rotulos) else escolhidos
            self._save_speakers_choice()
            self._sync_speakers_label()
            return True

        def _save_coding(self) -> None:
            from . import coding as _coding
            paths = self._paths()
            if paths is None:
                return
            _coding.save_codebook(paths, self._codes)
            _coding.save_codings(paths, self._codings)
            self._codes_dirty = False

        def _save_themes(self) -> None:
            from . import themes as _themes
            paths = self._paths()
            if paths is not None and self._payload is not None:
                _themes.save_themes(paths, self._payload)

        # ---- descoberta ------------------------------------------------------
        def run_discover(self) -> None:
            from . import search as _search
            from . import themes as _themes
            ctx = self._window.context
            if ctx is None:
                return
            if self._worker and self._worker.isRunning():
                self.status_label.setText("Aguarde a etapa atual terminar (ou cancele).")
                return
            self._refresh_scope()
            ids = self._scope_ids()
            if not ids:
                self.status_label.setText(self._scope_text())
                return
            if not _search.encoder_cached() and not self._window._ensure_optional_model(
                    "search_encoder", "o modelo de busca por sentido",
                    "Os temas são descobertos pelo sentido dos trechos — para isso o "
                    "Transcritório usa o modelo de busca por sentido, que ainda não está "
                    "instalado neste computador."):
                return
            self._load_coding()
            # Primeira descoberta do projeto: PERGUNTAR quem entra antes de
            # rodar, em vez de decidir por conta própria.
            if not bool(ctx.project.get("coding_speakers_asked")):
                if not self._choose_speakers(primeira_vez=True):
                    self.status_label.setText(
                        "Escolha quem entra na análise para descobrir os temas.")
                    return
            n = int(self.n_themes_spin.value()) or None
            speakers = list(self._speakers) if self._speakers is not None else None
            paths = ctx.paths
            self.names_button.setVisible(False)
            self.cancel_button.setText("Cancelar")
            self.cancel_button.setVisible(True)
            self.discover_button.setEnabled(False)
            self.status_label.setText("Lendo os trechos das entrevistas…")

            def fn(emit, should_cancel):
                return _themes.discover(paths, ids, n_themes=n, progress_callback=emit,
                                        should_cancel=should_cancel, speakers=speakers)

            self._start_worker(
                fn, self._on_discover_done,
                on_progress=lambda d: self.status_label.setText(str(d.get("message") or "")))

        def _on_discover_done(self, result, error: str) -> None:
            self._idle_buttons()
            worker = self._worker
            payload = result or {}
            if payload.get("cancelado") or (worker is not None and worker.cancelled.is_set()):
                self.status_label.setText(
                    "Descoberta cancelada — os temas anteriores continuam valendo.")
                return
            if error:
                self.status_label.setText(f"Não foi possível descobrir os temas: {error}")
                return
            if not payload.get("themes") and not payload.get("outros"):
                self.status_label.setText(
                    "Nenhum trecho para agrupar — as entrevistas do escopo ainda não têm índice de busca.")
                return
            self._payload = payload
            self._render()
            temas = payload.get("themes") or []
            outros = payload.get("outros") or []
            self.status_label.setText(
                f"{len(temas)} tema(s) em {int(payload.get('n_passages') or 0)} trechos de "
                f"{len(payload.get('interview_ids') or [])} entrevista(s); {len(outros)} trecho(s) sem tema definido.")
            self._maybe_offer_naming()

        def _cancel(self) -> None:
            worker = self._worker
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                self.status_label.setText("Cancelando…")

        # ---- nomes pela AI (segundo plano) ------------------------------------
        def _needs_names(self) -> bool:
            return any(t.get("name_source") == "termos" for t in (self._payload or {}).get("themes") or [])

        def _maybe_offer_naming(self, auto: bool = True) -> None:
            """Com o modelo de analise pronto, nomeia sozinho em segundo plano
            LOGO APOS uma descoberta pedida pelo usuario (auto=True); ao abrir a
            janela (auto=False), so oferece o botao. Modelo instalavel: botao
            com o tamanho do download. Incompativel: diz por que os nomes ficam
            pelos termos."""
            if not self._needs_names():
                self.names_button.setVisible(False)
                return
            estado, _motivo, gb = self._llm_state()
            if estado == "incompativel":
                self.names_button.setVisible(False)
                self.status_label.setText(
                    self.status_label.text() + " Nomes pelos termos característicos — a AI que nomeia "
                    "precisa de placa NVIDIA.")
                return
            if estado == "instalavel":
                self.names_button.setText(f"✨ Nomear os temas com a AI (baixa ~{fmt_gb(gb)})")
                self.names_button.setVisible(True)
                return
            self.names_button.setText("✨ Nomear os temas com a AI")
            if not auto:
                self.names_button.setVisible(True)
                return
            self._start_naming(user_click=False)

        def _start_naming(self, user_click: bool = False) -> None:
            import copy as _copy
            from . import themes as _themes
            if self._payload is None or (self._worker and self._worker.isRunning()):
                return
            estado, _motivo, _gb = self._llm_state()
            if estado == "incompativel":
                return
            if estado == "instalavel" and (not user_click or not self._window._ensure_llm_model()):
                return
            paths = self._paths()
            if paths is None:
                return
            snapshot = _copy.deepcopy(self._payload)   # a thread nunca toca no payload da janela
            self._naming = True
            self.names_button.setVisible(False)
            self.cancel_button.setText("Cancelar os nomes pela AI (os temas ficam)")
            self.cancel_button.setVisible(True)
            self.status_label.setText(
                self.status_label.text() + " Nomeando os temas com a AI em segundo plano (alguns minutos)…")

            def fn(emit, should_cancel):
                return _themes.name_with_llm(paths, snapshot, progress_callback=emit,
                                             should_cancel=should_cancel)

            self._start_worker(fn, self._on_naming_done)

        def _on_naming_done(self, result, error: str) -> None:
            from . import themes as _themes
            self._naming = False
            self.cancel_button.setVisible(False)
            worker = self._worker
            if worker is not None and worker.cancelled.is_set():
                self.status_label.setText("Nomes pela AI cancelados — os temas continuam com os termos.")
                self.names_button.setVisible(self._needs_names())
                return
            payload = result or {}
            if error or payload.get("erro"):
                self.status_label.setText(f"A AI não conseguiu nomear os temas: {error or payload.get('erro')}")
                self.names_button.setVisible(self._needs_names())
                return
            if self._payload is None:
                return
            n = _themes.apply_names(self._payload, payload.get("nomes") or [])
            self._save_themes()
            # A nomeação roda por minutos JUSTAMENTE para o usuário continuar
            # trabalhando: reconstruir a lista de trechos apagaria as caixas
            # que ele desmarcou nesse intervalo — e o código seguinte iria
            # para trechos que ele tinha excluído de propósito.
            self._render(keep_marks=True)
            if n == 0:
                # Sem saída era o pior caso: JSON malformado ou ids inventados
                # deixavam a janela sem botão para tentar de novo.
                self.status_label.setText(
                    "A AI não conseguiu nomear os temas desta vez — eles continuam com os "
                    "termos característicos. Dá para tentar de novo.")
            else:
                faltam = sum(1 for t in self._themes() if t.get("name_source") == "termos")
                self.status_label.setText(
                    f"A AI nomeou {n} tema(s)."
                    + (f" {faltam} continuam com os termos característicos." if faltam else ""))
            self.names_button.setVisible(self._needs_names())

        # ---- lista de temas e trechos -----------------------------------------
        def _themes(self) -> list[dict[str, Any]]:
            return list((self._payload or {}).get("themes") or [])

        def _current_theme(self) -> dict[str, Any] | None:
            item = self.themes_list.currentItem()
            if item is None:
                return None
            theme_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            for theme in self._themes():
                if theme.get("id") == theme_id:
                    return theme
            return None

        @staticmethod
        def _passage_key(passage: dict[str, Any]) -> tuple[str, int, int]:
            return (str(passage.get("interview_id") or ""),
                    int(passage.get("t_from", 0)), int(passage.get("t_to", 0)))

        def _current_marks(self) -> dict[tuple[str, int, int], Any]:
            """Marcações atuais por TRECHO (não por linha): a lista pode mudar
            de tamanho entre uma reconstrução e outra."""
            marks: dict[tuple[str, int, int], Any] = {}
            for row in range(self.passages.count()):
                item = self.passages.item(row)
                passage = item.data(Qt.ItemDataRole.UserRole + 1) or {}
                if passage:
                    marks[self._passage_key(passage)] = item.checkState()
            return marks

        def _render(self, keep_marks: bool = False) -> None:
            marks = self._current_marks() if keep_marks else None
            current = self.themes_list.currentItem()
            keep_id = str(current.data(Qt.ItemDataRole.UserRole)) if current is not None else ""
            self.themes_list.blockSignals(True)
            self.themes_list.clear()
            for theme in self._themes():
                item = QListWidgetItem(
                    f"{theme.get('name') or theme.get('id')}  —  {int(theme.get('n_passages') or 0)} trechos · "
                    f"{int(theme.get('n_interviews') or 0)} entrevistas")
                item.setData(Qt.ItemDataRole.UserRole, theme.get("id"))
                self.themes_list.addItem(item)
            outros = (self._payload or {}).get("outros") or []
            if outros:
                item = QListWidgetItem(f"Sem tema definido  —  {len(outros)} trechos")
                item.setData(Qt.ItemDataRole.UserRole, self.OUTROS_ID)
                self.themes_list.addItem(item)
            self.themes_list.blockSignals(False)
            row = 0
            for index in range(self.themes_list.count()):
                if str(self.themes_list.item(index).data(Qt.ItemDataRole.UserRole)) == keep_id:
                    row = index
                    break
            if self.themes_list.count():
                self.themes_list.setCurrentRow(row)
                self._on_theme_selected(row, marks)
            else:
                self._on_theme_selected(-1)

        def _turns_for(self, interview_id: str) -> list[dict[str, Any]]:
            """Turnos da entrevista, com cache por sessão da janela."""
            from . import search as _search
            cache = getattr(self, "_turns_cache", None)
            if cache is None:
                cache = self._turns_cache = {}
            if interview_id not in cache:
                paths = self._paths()
                cache[interview_id] = _search.load_source_turns(paths, interview_id) if paths else []
            return cache[interview_id]

        def _passage_ranges(self, passage: dict[str, Any]) -> list[tuple[int, int]]:
            """As faixas de turnos que o código pinta neste trecho: só as
            falas de quem está marcado em "Quem entra"."""
            from . import coding as _coding
            iid = str(passage.get("interview_id") or "")
            return _coding.contiguous_ranges(
                self._turns_for(iid), int(passage.get("t_from", 0)),
                int(passage.get("t_to", 0)), self._speakers_set())

        def _range_quote(self, interview_id: str, t_from: int, t_to: int) -> str:
            from . import search as _search
            faixa = {"t_from": t_from, "t_to": t_to, "c_from": 0, "c_to": -1}
            return _search.passage_scope_text(self._turns_for(interview_id), faixa, None)

        def _code_names_for(self, passage: dict[str, Any]) -> list[str]:
            from . import coding as _coding
            by_id = {c["id"]: c["name"] for c in self._codes}
            iid = str(passage.get("interview_id") or "")
            nomes: list[str] = []
            for t_from, t_to in self._passage_ranges(passage):
                for code_id in _coding.codes_at(self._codings, iid, t_from, t_to):
                    nome = by_id.get(code_id)
                    if nome and nome not in nomes:
                        nomes.append(nome)
            return nomes

        def _on_theme_selected(self, row: int, marks: dict | None = None) -> None:
            self.passages.clear()
            if row < 0 or self._payload is None:
                self.theme_title.setText("")
                self.theme_desc.setText("")
                return
            item = self.themes_list.item(row)
            theme_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
            if theme_id == self.OUTROS_ID:
                lista = list(self._payload.get("outros") or [])
                self.theme_title.setText(f"Sem tema definido ({len(lista)} trechos)")
                self.theme_desc.setText(
                    "Trechos que não se pareceram o bastante com nenhum tema. Dá para codificá-los mesmo assim.")
            else:
                theme = self._current_theme() or {}
                lista = list(theme.get("passages") or [])
                self.theme_title.setText(str(theme.get("name") or theme_id))
                termos = ", ".join(theme.get("terms") or [])
                desc = str(theme.get("description") or "")
                self.theme_desc.setText(
                    desc + (f"\nTermos característicos: {termos}" if termos else ""))
            for passage in lista:
                self._add_passage(passage, marks)

        def _add_passage(self, passage: dict[str, Any], marks: dict | None = None) -> None:
            iid = str(passage.get("interview_id") or "")
            codigos = self._code_names_for(passage)
            prefixo = ("● " + "; ".join(codigos) + "\n") if codigos else ""
            texto = " ".join(str(passage.get("text") or "").split())
            item = QListWidgetItem(
                f"{prefixo}{self._friendly_title(iid)} · {format_clock(float(passage.get('start', 0) or 0))}\n{texto}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState((marks or {}).get(self._passage_key(passage), Qt.CheckState.Checked))
            item.setData(Qt.ItemDataRole.UserRole, (iid, float(passage.get("start", 0) or 0)))
            item.setData(Qt.ItemDataRole.UserRole + 1, passage)
            self.passages.addItem(item)

        def _refresh_passages(self) -> None:
            """Reescreve os rotulos de codigo preservando as marcacoes."""
            marks = self._current_marks()
            atual = self.passages.currentRow()
            self._on_theme_selected(self.themes_list.currentRow(), marks)
            if 0 <= atual < self.passages.count():
                self.passages.setCurrentRow(atual)

        def _checked_passages(self) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for row in range(self.passages.count()):
                item = self.passages.item(row)
                if item.checkState() == Qt.CheckState.Checked:
                    out.append(item.data(Qt.ItemDataRole.UserRole + 1))
            return out

        # ---- renomear / juntar ------------------------------------------------
        def _selection_is_outros(self) -> bool:
            item = self.themes_list.currentItem()
            return item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") == self.OUTROS_ID

        def _no_theme_reason(self, acao: str) -> str:
            """Motivo certo para renomear/juntar sem um TEMA escolhido — a
            linha «Sem tema definido» está na lista mas não é um tema, e
            mandar "escolha um tema" para quem acabou de escolher uma linha
            não explica nada."""
            if self._payload is None:
                return f"Descubra os temas primeiro — depois dá para {acao}."
            if self._selection_is_outros():
                return ("«Sem tema definido» não é um tema — são os trechos que sobraram. "
                        f"Escolha um tema da lista para {acao}.")
            return f"Escolha um tema na lista para {acao}."

        def _rename(self) -> None:
            from . import themes as _themes
            theme = self._current_theme()
            if theme is None or self._payload is None:
                self.status_label.setText(self._no_theme_reason("renomear"))
                return
            nome, ok = QInputDialog.getText(self, "Renomear tema", "Nome do tema:",
                                            text=str(theme.get("name") or ""))
            nome = " ".join(str(nome or "").split())
            if not ok or not nome:
                return
            _themes.rename_theme(self._payload, str(theme["id"]), nome)
            self._save_themes()
            self._render()
            self.status_label.setText(f"Tema renomeado para «{nome}».")

        def _merge(self) -> None:
            from . import themes as _themes
            theme = self._current_theme()
            if theme is None or self._payload is None:
                self.status_label.setText(self._no_theme_reason("juntar"))
                return
            outros = [t for t in self._themes() if t is not theme]
            if not outros:
                self.status_label.setText("Só há um tema.")
                return
            nomes = [str(t.get("name") or t.get("id")) for t in outros]
            escolha, ok = QInputDialog.getItem(
                self, "Juntar temas", f"Juntar «{theme.get('name')}» com:", nomes, 0, False)
            if not ok or escolha not in nomes:
                return
            absorvido = outros[nomes.index(escolha)]
            _themes.merge_themes(self._payload, str(theme["id"]), str(absorvido["id"]))
            self._save_themes()
            self._render()
            self.status_label.setText(
                f"«{escolha}» entrou em «{theme.get('name')}» ({int(theme.get('n_passages') or 0)} trechos).")

        # ---- codificacao -------------------------------------------------------
        def _code_names(self) -> list[str]:
            return [str(c.get("name") or "") for c in self._codes]

        def _apply_code_to_checked(self) -> None:
            from . import coding as _coding
            theme = self._current_theme()
            marcados = self._checked_passages()
            if not marcados:
                self.status_label.setText(
                    "Marque ao menos um trecho para aplicar um código." if self._payload is not None
                    else "Descubra os temas primeiro — depois marque os trechos e aplique um código.")
                return
            nomes = self._code_names()
            sugerido = str(theme.get("name") or "") if theme else ""
            atual = nomes.index(sugerido) if sugerido in nomes else (len(nomes) if sugerido else 0)
            escolha, ok = QInputDialog.getItem(
                self, "Aplicar código", "Código (escolha um ou escreva um novo):",
                nomes + ([sugerido] if sugerido and sugerido not in nomes else []), atual, True)
            escolha = " ".join(str(escolha or "").split())
            if not ok or not escolha:
                return
            novo = _coding.find_code(self._codes, escolha) is None
            code = _coding.add_code(self._codes, escolha,
                                    description=str(theme.get("description") or "") if (theme and novo) else "")
            # O código pinta SÓ as falas de quem está em "Quem entra": um
            # trecho pergunta-resposta-pergunta-resposta vira duas faixas do
            # mesmo código, e a pergunta fica no documento como contexto.
            criadas, ja_tinham = 0, 0
            for p in marcados:
                iid = str(p["interview_id"])
                for t_from, t_to in self._passage_ranges(p):
                    if _coding.add_coding(self._codings, iid, t_from, t_to, code["id"],
                                          quote=self._range_quote(iid, t_from, t_to),
                                          origem="tema") is not None:
                        criadas += 1
                    else:
                        ja_tinham += 1
            self._forget_bulk_undo()
            self._save_coding()
            self._refresh_passages()
            self.status_label.setText(
                f"Código «{code['name']}» aplicado a {criadas} trecho(s)"
                + (f" ({ja_tinham} já o tinham)." if ja_tinham else ".")
                + ("" if self._speakers is None
                   else f" Só a fala de {', '.join(self._speakers)}."))

        def _apply_all_themes_as_codes(self) -> None:
            """Um código por tema, aplicado a todos os trechos daquele tema."""
            from . import coding as _coding
            temas = self._themes()
            if not temas:
                self.status_label.setText(
                    "Descubra os temas primeiro — depois dá para aplicar todos como códigos."
                    if self._payload is None else "Nenhum tema para aplicar.")
                return
            trechos = sum(len(t.get("passages") or []) for t in temas)
            escopo = ("" if self._speakers is None
                      else "\nCada código pinta só a fala de "
                           f"{', '.join(self._speakers)}; o resto do trecho fica no "
                           "documento como contexto.")
            resposta = QMessageBox.question(
                self, "Aplicar todos os temas como códigos",
                f"Isto cria um código para cada um dos {len(temas)} temas (reaproveitando os que já "
                f"existem com o mesmo nome) e o aplica aos {trechos} trechos deles."
                f"{escopo}\n\n«Sem tema definido» não entra. O botão «Desfazer», ao lado, "
                "devolve tudo ao que era.\n\nAplicar agora?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if resposta != QMessageBox.StandardButton.Yes:
                return
            antes = (deepcopy(self._codes), deepcopy(self._codings))
            try:
                resumo = _coding.apply_themes_as_codes(
                    self._codes, self._codings, temas, self._passage_ranges, self._range_quote)
            except Exception as exc:  # noqa: BLE001 - nunca deixa a janela num meio-termo
                self._codes, self._codings = antes
                self.status_label.setText(f"Não foi possível aplicar: {sanitize_message(str(exc))}")
                return
            # Só guarda o retrato quando algo mudou de fato: aplicar duas vezes
            # seguidas não pode fazer o «Desfazer» apontar para uma aplicação
            # que não alterou nada — e deixar de desfazer a que alterou.
            if resumo["criadas"] or resumo["codigos_novos"]:
                self._bulk_undo = antes
                self.undo_bulk_button.setVisible(True)
            self._save_coding()
            self._refresh_passages()
            partes = [f"{resumo['codigos_novos']} código(s) novo(s) e "
                      f"{resumo['criadas']} trecho(s) codificado(s) em {resumo['temas']} temas."]
            if resumo["ja_tinham"]:
                partes.append(f"{resumo['ja_tinham']} já tinham o código do tema.")
            if resumo["sem_fala"]:
                partes.append(f"{resumo['sem_fala']} trecho(s) ficaram de fora: só têm fala de "
                              "quem não entra na análise.")
            self.status_label.setText(" ".join(partes))

        def _undo_bulk_coding(self) -> None:
            """Desfaz a última aplicação em massa. Só ela: o que veio depois,
            manualmente, seria perdido — por isso o botão some assim que a
            codificação muda por outro caminho."""
            antes = getattr(self, "_bulk_undo", None)
            if antes is None:
                self.undo_bulk_button.setVisible(False)
                return
            self._codes, self._codings = antes
            self._bulk_undo = None
            self.undo_bulk_button.setVisible(False)
            self._save_coding()
            self._refresh_passages()
            self.status_label.setText("Aplicação em massa desfeita: codebook e codificação "
                                      "voltaram ao que eram.")

        def _forget_bulk_undo(self) -> None:
            """Qualquer outra mudança na codificação torna o desfazer em massa
            perigoso (levaria junto o que foi feito depois)."""
            if getattr(self, "_bulk_undo", None) is not None:
                self._bulk_undo = None
                self.undo_bulk_button.setVisible(False)

        def _passage_codes(self) -> None:
            from . import coding as _coding
            item = self.passages.currentItem()
            if item is None:
                self.status_label.setText(
                    "Selecione um trecho para ver ou mudar os códigos dele." if self._payload is not None
                    else "Descubra os temas primeiro — os trechos de cada tema aparecem à direita.")
                return
            passage = item.data(Qt.ItemDataRole.UserRole + 1) or {}
            aplicados = self._code_names_for(passage)
            opcoes = [("✓ " + n) if n in aplicados else n for n in self._code_names()]
            escolha, ok = QInputDialog.getItem(
                self, "Códigos deste trecho",
                "Escolha um código para acrescentar, um marcado (✓) para tirar, ou escreva um novo:",
                opcoes, 0, True)
            escolha = " ".join(str(escolha or "").split())
            if not ok or not escolha:
                return
            iid = str(passage["interview_id"])
            faixas = self._passage_ranges(passage)
            if not faixas:
                self.status_label.setText(
                    "Este trecho só tem fala de quem ficou de fora em «Quem entra».")
                return
            if escolha.startswith("✓ "):
                nome = escolha[2:].strip()
                code = _coding.find_code(self._codes, nome)
                if code is not None:
                    alvo = [c for c in self._codings
                            if c.get("interview_id") == iid and c.get("code_id") == code["id"]
                            and (int(c.get("t_from", -1)), int(c.get("t_to", -1))) in faixas]
                    for c in alvo:
                        _coding.remove_coding(self._codings, str(c["id"]))
                    self._forget_bulk_undo()
                    self._save_coding()
                    self._refresh_passages()
                    self.status_label.setText(f"Código «{nome}» retirado do trecho.")
                return
            code = _coding.add_code(self._codes, escolha)
            criadas = sum(
                1 for t_from, t_to in faixas
                if _coding.add_coding(self._codings, iid, t_from, t_to, code["id"],
                                      quote=self._range_quote(iid, t_from, t_to),
                                      origem="manual") is not None)
            if not criadas:
                self.status_label.setText(f"O trecho já tem o código «{code['name']}».")
                return
            self._forget_bulk_undo()
            self._save_coding()
            self._refresh_passages()
            self.status_label.setText(f"Código «{code['name']}» acrescentado ao trecho.")

        def _export_ids(self) -> list[str]:
            """Entrevistas que entram na exportação: TODAS as que têm alguma
            codificação, mais as do escopo dos temas.

            Nunca o combo "Onde:" — ele descreve a descoberta, e usá-lo aqui
            fazia o arquivo sair sem parte da codificação, em silêncio."""
            from . import coding as _coding
            transcritas = self._project_ids()
            ids = list(_coding.coded_interview_ids(self._codings))
            for iid in (self._payload or {}).get("interview_ids") or []:
                if iid not in ids:
                    ids.append(str(iid))
            return [i for i in ids if i in transcritas]

        def _export(self) -> None:
            from . import coding as _coding
            from . import search as _search
            ctx = self._window.context
            if ctx is None:
                return
            if not self._codings:
                self.status_label.setText("Nenhum código aplicado ainda — codifique trechos antes de exportar.")
                return
            paths = ctx.paths
            ids = self._export_ids()
            if not ids:
                self.status_label.setText(
                    "As entrevistas codificadas não têm transcrição neste projeto — nada a exportar.")
                return
            nome = str(ctx.project.get("project_name") or paths.project_root.name)
            padrao = _coding.coding_dir(paths) / f"{nome}.qualilab"
            destino, _filtro = QFileDialog.getSaveFileName(
                self, f"Exportar a codificação de {len(ids)} entrevista(s)", str(padrao),
                "Projeto QualiLab (*.qualilab);;Planilha CSV (*.csv)")
            if not destino:
                return
            titulos = {iid: self._friendly_title(iid) for iid in ids}
            out = Path(destino)
            try:
                if out.suffix.lower() == ".csv":
                    contagem = _coding.export_csv(
                        paths, ids, out, lambda iid: _search.load_source_turns(paths, iid))
                    self.status_label.setText(
                        f"Planilha gravada em {out} ({contagem['linhas']} trecho(s) codificado(s))."
                        + self._desancoradas_text(contagem))
                else:
                    if out.suffix.lower() != ".qualilab":
                        out = out.with_suffix(".qualilab")
                    contagem = _coding.export_qualilab(
                        paths, ids, out, nome, lambda iid: _search.load_source_turns(paths, iid), titles=titulos)
                    self.status_label.setText(
                        f"Projeto do QualiLab gravado em {out} ({contagem['documents']} documento(s), "
                        f"{contagem['codes']} código(s), {contagem['codings']} trecho(s) codificado(s))."
                        + self._desancoradas_text(contagem))
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self.status_label.setText(f"Não foi possível exportar: {exc}")

        @staticmethod
        def _desancoradas_text(contagem: dict[str, int]) -> str:
            """Perda NUNCA silenciosa: se a transcrição mudou depois da
            codificação, os trechos que não puderam ser localizados ficam de
            fora — e o usuário precisa saber."""
            n = int(contagem.get("desancoradas") or 0)
            if not n:
                return ""
            return (f" {n} trecho(s) codificado(s) não entraram: a transcrição mudou depois "
                    "da codificação e eles não foram encontrados onde estavam.")


    class EngineSettingsDialog(QDialog):
        def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Configurações de transcrição local")
            layout = QVBoxLayout(self)
            description = QLabel("Motor local de transcrição. Com o TAGARELA, o processador basta (1 h de áudio em poucos minutos); a placa NVIDIA acelera o Whisper e a separação de falantes.")
            description.setWordWrap(True)
            layout.addWidget(description)
            # Nota inline (2026-09-02): TAGARELA + idioma que nao e portugues.
            self.engine_lang_note = QLabel("")
            self.engine_lang_note.setWordWrap(True)
            self.engine_lang_note.setStyleSheet(f"color: {ui_tokens.WARN}; font-weight: 600;")
            self.engine_lang_note.setVisible(False)
            layout.addWidget(self.engine_lang_note)

            grid = QGridLayout()
            from . import model_manager
            self.model_combo = QComboBox()
            installed = model_manager.installed_asr_variants()
            current = str(config.get("asr_model") or model_manager.DEFAULT_ASR_VARIANT)
            current_idx = 0
            for key in model_manager.ASR_VARIANTS:
                if key not in installed:
                    continue
                info = model_manager.ASR_VARIANTS[key]
                self.model_combo.addItem(info["label"], key)
                if key == current:
                    current_idx = self.model_combo.count() - 1
            if self.model_combo.count() == 0:
                self.model_combo.addItem(current, current)
            self.model_combo.setCurrentIndex(current_idx)
            model_row = QHBoxLayout()
            model_row.addWidget(self.model_combo, stretch=1)
            self.install_models_btn = QPushButton("Gerenciar modelos…")
            self.install_models_btn.setToolTip("Ver, baixar e remover os modelos deste computador.")
            self.install_models_btn.clicked.connect(self._open_model_setup)
            model_row.addWidget(self.install_models_btn)
            grid.addWidget(QLabel("Modelo:"), 0, 0)
            grid.addLayout(model_row, 0, 1)

            self.device_combo = QComboBox()
            # Build device list dynamically. On Apple Silicon we surface
            # "GPU Apple Silicon (MLX/Metal)" as a third option so the user
            # understands Metal acceleration is available; selecting it (or
            # the default "cuda") both route through mlx_whisper_runner at
            # runtime when MPS is detected.
            from . import runtime as _runtime_dev
            device_options: list[tuple[str, str]] = [
                ("auto", "Automático (recomendado)"),
                ("cuda", "GPU NVIDIA (CUDA)"),
            ]
            if _runtime_dev.detect_device() == "mps":
                device_options.append(("mps", "GPU Apple Silicon (MLX/Metal)"))
            device_options.append(("cpu", "CPU"))
            for value, label in device_options:
                self.device_combo.addItem(label, value)
            self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(str(config.get("asr_device") or "auto"))))
            # Honestidade do combo: sem placa NVIDIA, "cuda" e uma escolha
            # que so falharia depois — desabilitar COM motivo (nunca
            # esconder), e uma config "cuda" orfa cai para "auto".
            # NUNCA no macOS: la "cuda" e a rota documentada do MLX/Metal
            # (ver comentario das device_options) e ha a opcao "mps".
            from . import capabilities as _caps_dev
            if sys.platform != "darwin" and not _caps_dev.hardware_snapshot().has_gpu:
                idx_cuda = self.device_combo.findData("cuda")
                try:
                    item = self.device_combo.model().item(idx_cuda)
                    item.setEnabled(False)
                    item.setToolTip("Nenhuma placa NVIDIA foi encontrada neste computador.")
                except Exception:  # noqa: BLE001 - modelo nao-standard: segue sem flag
                    pass
                if str(config.get("asr_device") or "auto") == "cuda":
                    self.device_combo.setCurrentIndex(
                        max(0, self.device_combo.findData("auto")))
            grid.addWidget(QLabel("Dispositivo:"), 1, 0)
            grid.addWidget(self.device_combo, 1, 1)

            self.compute_combo = QComboBox()
            for value, label in [
                ("auto", "Automático (recomendado)"),
                ("float16", "float16 (GPU)"),
                ("int8", "int8 (menor memoria)"),
                ("float32", "float32 (CPU/GPU, mais pesado)"),
            ]:
                self.compute_combo.addItem(label, value)
            self.compute_combo.setCurrentIndex(max(0, self.compute_combo.findData(str(config.get("asr_compute_type") or "auto"))))
            grid.addWidget(QLabel("Precisao:"), 2, 0)
            grid.addWidget(self.compute_combo, 2, 1)

            # Etapa 4: combo gerado do REGISTRO de pacotes de idioma, com
            # marcacao honesta de tempos por palavra (instalado / a baixar).
            # "Automatico" declara a limitacao: deteccao nao permite
            # alinhador confiavel.
            from . import model_manager as _mm_lang
            self.language_combo = QComboBox()

            def _sufixo_idioma(codigo: str) -> str:
                try:
                    asset = _mm_lang.align_asset_for(codigo)
                    snap = _mm_lang.cached_snapshot_path(
                        asset.repo_id, None, revision=asset.revision)
                    if snap is not None and _mm_lang._snapshot_has_weights(snap):
                        return ""
                    return f"  (tempos por palavra: baixa ~{asset.estimated_gb:.1f} GB)"
                except Exception:  # noqa: BLE001
                    return ""

            ordenados = sorted(_mm_lang.ALIGN_LANGUAGES.items(),
                               key=lambda kv: (kv[0] != "pt", kv[1]["label"]))
            self.language_combo.addItem(
                f"{ordenados[0][1]['label']}{_sufixo_idioma('pt')}", "pt")
            self.language_combo.addItem(
                "Automático — detecta o idioma; sem tempos por palavra", "auto")
            for code, spec in ordenados[1:]:
                self.language_combo.addItem(
                    f"{spec['label']}{_sufixo_idioma(code)}", code)
            language = str(config.get("asr_language") or "auto")
            self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(language)))
            grid.addWidget(QLabel("Idioma padrão:"), 3, 0)
            grid.addWidget(self.language_combo, 3, 1)
            self.model_combo.currentIndexChanged.connect(self._refresh_engine_lang_note)
            self.language_combo.currentIndexChanged.connect(self._refresh_engine_lang_note)
            self._refresh_engine_lang_note()

            self.min_speakers_spin = QSpinBox()
            self.min_speakers_spin.setRange(1, 20)
            self.min_speakers_spin.setValue(int(config.get("min_speakers") or 2))
            self.max_speakers_spin = QSpinBox()
            self.max_speakers_spin.setRange(1, 20)
            self.max_speakers_spin.setValue(int(config.get("max_speakers") or 2))
            speakers_row = QHBoxLayout()
            speakers_row.addWidget(self.min_speakers_spin)
            speakers_row.addWidget(QLabel("a"))
            speakers_row.addWidget(self.max_speakers_spin)
            grid.addWidget(QLabel("Falantes (min a max):"), 4, 0)
            grid.addLayout(speakers_row, 4, 1)

            self.batch_spin = QSpinBox()
            # 0 = "Automático" (resolve_compute_settings decide: cuda 8, cpu 2)
            self.batch_spin.setRange(0, 32)
            self.batch_spin.setSpecialValueText("Automático")
            try:
                _batch_cfg = int(config.get("asr_batch_size") or 0)
            except (TypeError, ValueError):
                _batch_cfg = 0  # "auto" ou valor invalido
            self.batch_spin.setValue(max(0, _batch_cfg))

            layout.addLayout(grid)
            advanced_group = QGroupBox("Avancado")
            advanced_layout = QGridLayout(advanced_group)
            advanced_layout.addWidget(QLabel("Batch:"), 0, 0)
            advanced_layout.addWidget(self.batch_spin, 0, 1)

            self.min_pause_spin = QDoubleSpinBox()
            self.min_pause_spin.setRange(0.0, 5.0)
            self.min_pause_spin.setSingleStep(0.5)
            self.min_pause_spin.setDecimals(1)
            self.min_pause_spin.setSuffix(" s")
            val = config.get("diarization_min_duration_off")
            self.min_pause_spin.setValue(float(val) if val is not None else 0.0)
            self.min_pause_spin.setToolTip("Pausas menores que este valor sao fundidas no mesmo falante. Aumentar reduz fragmentacao.")
            advanced_layout.addWidget(QLabel("Pausa minima entre falantes:"), 1, 0)
            advanced_layout.addWidget(self.min_pause_spin, 1, 1)

            self.min_segment_spin = QDoubleSpinBox()
            self.min_segment_spin.setRange(0.0, 2.0)
            self.min_segment_spin.setSingleStep(0.1)
            self.min_segment_spin.setDecimals(2)
            self.min_segment_spin.setSuffix(" s")
            val = config.get("diarization_min_segment")
            self.min_segment_spin.setValue(float(val) if val is not None else 0.3)
            self.min_segment_spin.setToolTip("Segmentos de fala menores que este valor sao removidos. Reduz micro-segmentos espurios.")
            advanced_layout.addWidget(QLabel("Segmento mínimo:"), 2, 0)
            advanced_layout.addWidget(self.min_segment_spin, 2, 1)

            layout.addWidget(advanced_group)

            hint = QLabel("Batch controla quantos trechos o Whisper processa por vez. Aumentar pode acelerar em GPU com memoria sobrando; reduzir evita falta de memoria. Para computador sem GPU NVIDIA, use CPU com int8 ou float32. Você pode alternar entre CUDA e CPU aqui a qualquer momento — o selo \"Motor\" na barra de status (parte inferior da janela) mostra o que está em uso e abre esta tela.")
            hint.setStyleSheet(_style_muted())
            hint.setWordWrap(True)
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _refresh_engine_lang_note(self) -> None:
            nota = engine_language_note(
                str(self.model_combo.currentData() or ""), str(self.language_combo.currentData() or ""))
            self.engine_lang_note.setText(nota)
            self.engine_lang_note.setVisible(bool(nota))

        def _open_model_setup(self) -> None:
            # Abre o GERENCIADOR (escolha por item), nao o dialogo de escopo
            # obrigatorio — que numa instalacao completa ficava vazio, com um
            # "Baixar modelos" sem nada a baixar (teste real 2026-08-30).
            self.reject()
            parent = self.parent()
            if parent and hasattr(parent, "show_model_manager"):
                parent.show_model_manager()

        def updates(self) -> dict[str, Any]:
            device = str(self.device_combo.currentData())
            compute_type = str(self.compute_combo.currentData())
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"
            min_spk = int(self.min_speakers_spin.value())
            max_spk = int(self.max_speakers_spin.value())
            if max_spk < min_spk:
                max_spk = min_spk
            language = str(self.language_combo.currentData())
            min_pause = float(self.min_pause_spin.value())
            min_segment = float(self.min_segment_spin.value())
            return {
                "asr_model": str(self.model_combo.currentData() or self.model_combo.currentText() or __import__("transcribe_pipeline.model_manager", fromlist=["DEFAULT_ASR_VARIANT"]).DEFAULT_ASR_VARIANT),
                "asr_device": device,
                "asr_compute_type": compute_type,
                "asr_batch_size": int(self.batch_spin.value()) or "auto",
                "asr_language": None if language == "auto" else language,
                "diarization_num_speakers": min_spk if min_spk == max_spk else None,
                "min_speakers": min_spk,
                "max_speakers": max_spk,
                "diarization_min_duration_off": min_pause if min_pause > 0 else None,
                "diarization_min_segment": min_segment if min_segment > 0 else None,
            }


    class OtherLanguageEngineDialog(QDialog):
        """O lote tem idioma que o TAGARELA nao transcreve: explica, ensina
        onde o modelo e escolhido e oferece o Whisper (reserva) com a
        qualidade de cada opcao — so para este lote ou para o projeto."""

        def __init__(self, idiomas: list[str], opcoes: list[tuple[str, str, bool]],
                     parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Este lote tem outro idioma")
            layout = QVBoxLayout(self)
            from . import model_manager as _mm_ol
            rotulos = [str((_mm_ol.ALIGN_LANGUAGES.get(c) or {}).get("label") or c) for c in idiomas]
            texto = QLabel(
                "O TAGARELA só transcreve português, e este lote inclui: "
                f"{', '.join(rotulos)}.\n\n"
                "Para outros idiomas o Transcritório usa um modelo Whisper. A "
                "qualidade depende do tamanho: o small é rápido, mas erra mais "
                "palavras; o large-v3-turbo é quase tão preciso quanto o large-v3 "
                "e bem mais rápido, ideal com placa de vídeo (ou paciência, sem "
                "ela).\n\n"
                "Se a gravação for em português e o idioma estiver marcado errado, "
                "cancele e corrija-o na aba Propriedades (ou em Configurar "
                "transcrição…).\n\n"
                "Depois, o modelo do projeto fica em Ferramentas → Configurar "
                "transcrição… (campo Modelo), e novos modelos em Ferramentas → "
                "Gerenciar modelos…")
            texto.setWordWrap(True)
            layout.addWidget(texto)
            layout.addWidget(QLabel("Modelo Whisper para este lote:"))
            self.model_combo = QComboBox()
            for key, nota, instalado in opcoes:
                info = _mm_ol.ASR_VARIANTS.get(key) or {}
                rotulo = str(info.get("label") or key)
                sufixo = f" — {nota}" if nota else ""
                if not instalado:
                    sufixo += f"  (baixar, ~{float(info.get('estimated_gb') or 0):.1f} GB)"
                self.model_combo.addItem(f"{rotulo}{sufixo}", key)
            layout.addWidget(self.model_combo)
            self.project_check = QCheckBox(
                "Passar o projeto inteiro para este modelo (em vez de só este lote)")
            layout.addWidget(self.project_check)
            buttons = QDialogButtonBox()
            self.ok_button = buttons.addButton("Transcrever com o Whisper", QDialogButtonBox.ButtonRole.AcceptRole)
            self.settings_button = buttons.addButton("Configurar transcrição…", QDialogButtonBox.ButtonRole.ActionRole)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
            self._wants_settings = False
            self.settings_button.clicked.connect(self._on_settings)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            self.ok_button.setEnabled(self.model_combo.count() > 0)

        def _on_settings(self) -> None:
            self._wants_settings = True
            self.reject()

        @property
        def wants_settings(self) -> bool:
            return self._wants_settings

        def chosen_model(self) -> str:
            return str(self.model_combo.currentData() or "")

        def apply_to_project(self) -> bool:
            return self.project_check.isChecked()


    class RetranscribeDialog(QDialog):
        """Escolha de modelo para refazer a transcricao de UM arquivo.

        So modelos INSTALADOS entram (para baixar outros: Gerenciar
        modelos...). O aviso de recriacao vive AQUI: quem aceita ja
        consentiu, e o job nao pergunta de novo (confirmed_recreate)."""

        def __init__(self, interview_id: str, installed: list[str],
                     current: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            from . import model_manager as _mm
            self.setWindowTitle("Transcrever novamente")
            layout = QVBoxLayout(self)
            intro = QLabel(f"Refazer a transcrição de {interview_id} com o modelo:")
            intro.setWordWrap(True)
            layout.addWidget(intro)
            self.model_combo = QComboBox()
            for key in _mm.ASR_VARIANTS:
                if key not in installed:
                    continue
                rotulo = str(_mm.ASR_VARIANTS[key].get("label") or key)
                self.model_combo.addItem(rotulo, key)
                if key == current:
                    self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
            if self.model_combo.count() == 0 and current:
                self.model_combo.addItem(current, current)
            layout.addWidget(self.model_combo)
            nota = QLabel("Modelos instalados neste computador. Para baixar "
                          "outros, use Ferramentas → Gerenciar modelos…")
            nota.setWordWrap(True)
            nota.setStyleSheet(_style_muted())
            layout.addWidget(nota)
            aviso = QLabel(
                "A transcrição editável será recriada DO ZERO — edições manuais "
                "serão descartadas.\nCópia de segurança das versões com edições em "
                "Transcricoes\\05_transcripts_review\\edits\\backups.")
            aviso.setWordWrap(True)
            aviso.setStyleSheet(_style_warn())
            layout.addWidget(aviso)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                       | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Transcrever novamente")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def selected_model(self) -> str:
            return str(self.model_combo.currentData() or "")


    class JobsDialog(QDialog):
        def __init__(self, context: app_service.ProjectContext, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Fila de processamento")
            self.resize(900, 460)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("Fila de processamento do projeto atual."))
            self.table = QTableWidget(0, 8)
            self.table.setHorizontalHeaderLabels(["Arquivo", "Estado", "Etapa", "Progresso", "Inicio", "Estimativa", "Fim", "Erro"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            layout.addWidget(self.table, stretch=1)
            self._paths = context.paths
            self.populate(context)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            # Auto-refresh (plano U1.4): a fila era um snapshot estatico —
            # reler jobs.json (leitura pura, sem side effects) enquanto aberta.
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setInterval(2000)
            self._refresh_timer.timeout.connect(self._refresh)
            self._refresh_timer.start()

        def populate(self, context: app_service.ProjectContext) -> None:
            self._render(context.jobs)

        def _refresh(self) -> None:
            from .project_store import jobs_path
            from .utils import read_json

            if not self.isVisible():
                # Blindagem p/ uso nao-modal futuro: timer vivo com janela
                # oculta nao deve reler o disco a cada 2 s.
                return
            try:
                payload = read_json(jobs_path(self._paths))
            except Exception:
                return
            if isinstance(payload, dict):
                self._render(payload)

        def _render(self, jobs: dict) -> None:
            self.table.setRowCount(0)
            for file_id in sorted(jobs):
                job = jobs[file_id] if isinstance(jobs[file_id], dict) else {}
                row = self.table.rowCount()
                self.table.insertRow(row)
                values = [
                    file_id,
                    job.get("status", ""),
                    job.get("stage", ""),
                    f"{job.get('progress', 0)}%",
                    format_job_time(job.get("started_at", "")),
                    # U1.4: tempo RESTANTE ("cerca de 3min"), nao o horario
                    # absoluto de termino — que exigia aritmetica do usuario.
                    eta_text_for_job(job, datetime.now()),
                    format_job_time(job.get("finished_at", "")),
                    job.get("last_error", ""),
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    self.table.setItem(row, column, item)


    # -----------------------------------------------------------------------
    # First-Run Wizard (QWizard with 6 pages)
    # -----------------------------------------------------------------------

    class FirstRunWizard(QWizard):
        """Step-by-step setup wizard for first-time users."""

        PAGE_WELCOME = 0
        PAGE_ACCOUNT = 1
        PAGE_TERMS = 2
        PAGE_PROFILE = 8  # etapa 3: perfil de instalacao (recomendado, nao imposto)
        PAGE_MODEL_SELECT = 3
        PAGE_TOKEN = 4
        PAGE_DOWNLOAD = 5
        PAGE_DONE = 6
        PAGE_LANGS = 9  # etapa 4: idiomas das gravacoes (pacotes de alinhamento)
        # id 7 era a antiga pagina sim/nao de falantes (v0.2), substituida
        # pela PAGE_PROFILE na etapa 3; nao reusar o id.

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            from . import model_manager
            self.download_completed = False
            # Diarizacao e OPCIONAL (v0.2): quem so quer transcrever pula
            # conta/termos/token do pyannote gated. Desde a etapa 3, quem
            # decide isso e o PERFIL (essencial = so transcrever).
            self.wants_diarization = True
            self.selected_profile = "padrao"
            # O modelo default tambem acompanha a maquina (em CPU, o turbo
            # levaria horas por entrevista).
            from . import capabilities as _caps
            try:
                default_variants = list(_caps.recommended_asr_variants(_caps.hardware_snapshot()))
            except Exception:  # noqa: BLE001 - sonda nunca impede o wizard
                default_variants = [model_manager.DEFAULT_ASR_VARIANT]
            self.selected_asr_variants: list[str] = default_variants
            self.setWindowTitle(f"{APP_NAME} — Configuração inicial")
            self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
            self.setFixedWidth(680)
            self.setMinimumHeight(560)
            self.setOption(QWizard.WizardOption.NoCancelButton, False)
            self.setButtonText(QWizard.WizardButton.NextButton, "Próximo →")
            self.setButtonText(QWizard.WizardButton.BackButton, "← Voltar")
            self.setButtonText(QWizard.WizardButton.CancelButton, "Pular por agora")
            self.setButtonText(QWizard.WizardButton.FinishButton, "Começar a usar")

            self.setPage(self.PAGE_WELCOME, self._make_welcome_page())
            self.setPage(self.PAGE_PROFILE, self._make_profile_page())
            self.setPage(self.PAGE_ACCOUNT, self._make_account_page())
            self.setPage(self.PAGE_TERMS, self._make_terms_page())
            self.setPage(self.PAGE_MODEL_SELECT, _ModelSelectWizardPage(self))
            self.setPage(self.PAGE_LANGS, self._make_langs_page())
            self.setPage(self.PAGE_TOKEN, self._make_token_page())
            self.setPage(self.PAGE_DOWNLOAD, self._make_download_page())
            self.setPage(self.PAGE_DONE, self._make_done_page())
            self.setStartId(self.PAGE_WELCOME)

        def nextId(self) -> int:
            # Ordem explicita: o perfil decide o caminho. Essencial pula
            # conta/termos/token (modelos ASR sao publicos — nao exigem
            # token); padrao/completo incluem falantes (pyannote gated).
            if self.selected_profile != "essencial":
                # PAGE_LANGS (etapa 4): so faz sentido com alinhamento —
                # os pacotes de idioma sao exatamente os alinhadores.
                seq = [
                    self.PAGE_WELCOME, self.PAGE_PROFILE, self.PAGE_ACCOUNT,
                    self.PAGE_TERMS, self.PAGE_MODEL_SELECT, self.PAGE_LANGS,
                    self.PAGE_TOKEN, self.PAGE_DOWNLOAD, self.PAGE_DONE,
                ]
            else:
                seq = [
                    self.PAGE_WELCOME, self.PAGE_PROFILE,
                    self.PAGE_MODEL_SELECT, self.PAGE_DOWNLOAD, self.PAGE_DONE,
                ]
            cid = self.currentId()
            try:
                idx = seq.index(cid)
            except ValueError:
                return super().nextId()
            return seq[idx + 1] if idx + 1 < len(seq) else -1

        def done(self, result: int) -> None:
            # Fechar o wizard (X, Pular, Concluir) com download em andamento
            # NUNCA pode destruir o QThread vivo (crash na saida) nem deixar
            # o download orfao competindo com um proximo download.
            page = self.page(self.PAGE_DOWNLOAD)
            worker = getattr(page, "_worker", None)
            if worker is not None and worker.isRunning():
                worker.request_cancel()
                worker.wait()  # bloqueante: should_cancel corta entre blobs/chunks
            if result == QDialog.DialogCode.Accepted:
                # Persistir a escolha como default de projetos novos e o
                # perfil da MAQUINA (decide alinhamento e gates de inicio).
                try:
                    from . import app_settings
                    updates = {
                        # "auto" (2026-08-31): o perfil decide o que
                        # INSTALAR, nunca congela "sem falantes" como
                        # preferencia — projetos passam a separar sozinhos
                        # assim que o modelo for instalado.
                        "diarize_default": (True if self.wants_diarization
                                            else "auto"),
                        "install_profile": str(self.selected_profile),
                    }
                    # O modelo escolhido vira o default da MAQUINA: projetos
                    # novos e gates passam a pedir ele, nao o turbo de
                    # fabrica (bug do 1o teste real, 2026-08-30). Se mais de
                    # um foi baixado, o recomendado ganha.
                    escolhidos = list(self.selected_asr_variants or [])
                    if escolhidos:
                        select_page = self.page(self.PAGE_MODEL_SELECT)
                        recomendado = getattr(select_page, "_recommended_key", None)
                        updates["asr_model_default"] = (
                            recomendado if recomendado in escolhidos else escolhidos[0])
                    # Etapa 4: um UNICO idioma escolhido vira o default de
                    # projetos novos; varios (ou nenhum) = pt neutro.
                    idiomas = self.selected_languages
                    updates["language_default"] = (
                        idiomas[0] if len(idiomas) == 1 else "pt")
                    app_settings.save(updates)
                except Exception as exc:
                    _logger.warning("nao foi possivel salvar app_settings: %s", exc)
            super().done(result)

        def _make_profile_page(self) -> QWizardPage:
            """Perfil de instalacao (etapa 3): o app mostra o que detectou
            na maquina e MARCA o recomendado — a escolha e sempre do
            usuario, inclusive contra a recomendacao (com aviso claro).
            Substitui a antiga pergunta sim/nao de falantes: o perfil ja
            diz o que entra."""
            from . import capabilities as caps
            page = QWizardPage()
            page.setTitle("O que instalar neste computador?")
            layout = QVBoxLayout(page)
            hardware = caps.hardware_snapshot()
            recomendado = caps.recommended_profile(hardware)
            variantes = caps.recommended_asr_variants(hardware)
            # Cobrar so o que FALTA da dupla (revisao 2026-09-02): o tamanho
            # somava TAGARELA + reserva mesmo com um deles ja instalado.
            from . import model_manager as _mm_pf
            try:
                instaladas = set(_mm_pf.installed_asr_variants())
            except Exception:  # noqa: BLE001 - cache ilegivel = nada instalado
                instaladas = set()
            faltantes = tuple(v for v in variantes if v not in instaladas)
            tamanhos = caps.model_sizes_from_registry(faltantes or variantes)
            em_cache = caps.cached_model_keys(variantes)
            detectado = QLabel(f"Este computador tem: {caps.describe_hardware(hardware)}.")
            detectado.setWordWrap(True)
            layout.addWidget(detectado)
            descricoes = {
                "essencial": "Transcrever e exportar entrevistas. Sem conta e sem token.",
                "padrao": ("Tudo do Essencial + separar quem fala e tempos por palavra "
                           "(pede uma conta gratuita no Hugging Face)."),
                "completo": ("Tudo do Padrão + busca por sentido, glossário de nomes, "
                             "resumo e perguntas com AI local."),
            }
            self._profile_radios: dict[str, QRadioButton] = {}
            for chave, rotulo, caps_keys in caps.PROFILES:
                # Nao cobrar modelos DURO-bloqueados (ex.: Qwen sem GPU):
                # o total anunciava ~8,7 GB que nunca seriam baixados.
                chaves_ok = tuple(
                    k for k in caps_keys
                    if not caps.hardware_blocker(caps.capability(k), hardware))
                gb = caps.profile_size(chaves_ok, tamanhos, em_cache)
                marca = "   ← recomendado para esta máquina" if chave == recomendado else ""
                radio = QRadioButton(f"{rotulo} (~{gb:.1f} GB de componentes){marca}")
                radio.toggled.connect(
                    lambda checked, k=chave: checked and self._on_profile_chosen(k))
                layout.addWidget(radio)
                detalhe = QLabel("      " + descricoes[chave])
                detalhe.setWordWrap(True)
                detalhe.setStyleSheet(_style_muted())
                layout.addWidget(detalhe)
                self._profile_radios[chave] = radio
            self._profile_warning = QLabel("")
            self._profile_warning.setWordWrap(True)
            self._profile_warning.setStyleSheet(_style_err())
            layout.addWidget(self._profile_warning)
            # Decisao do usuario (2026-08-30): o Completo PERGUNTA — baixar
            # os modelos de IA agora ou deixar para a primeira utilizacao.
            # Recomendado marcado, escolha sempre do usuario.
            chaves_ia = _wizard_optional_keys("completo", hardware, em_cache)
            self._ai_gb = round(sum(tamanhos.get(k, 0.0) for k in chaves_ia), 1)
            self._ai_download_group = QGroupBox("Modelos de AI do perfil Completo")
            ai_layout = QVBoxLayout(self._ai_download_group)
            self._ai_now_radio = QRadioButton(
                f"Baixar agora, junto com os demais (~{self._ai_gb:.1f} GB "
                "incluídos no total acima)")
            self._ai_later_radio = QRadioButton(
                "Deixar para a primeira utilização de cada recurso "
                f"(o download inicial fica ~{self._ai_gb:.1f} GB menor)")
            ai_layout.addWidget(self._ai_now_radio)
            ai_layout.addWidget(self._ai_later_radio)
            bloqueio_qwen = caps.hardware_blocker(
                caps.capability("resumo_perguntar"), hardware)
            self._ai_blocked_note = QLabel(
                "O modelo de análise (8,7 GB) não entra no download: precisa de "
                "placa NVIDIA. As demais funções de AI valem."
                if bloqueio_qwen else "")
            self._ai_blocked_note.setWordWrap(True)
            self._ai_blocked_note.setStyleSheet(_style_muted())
            if bloqueio_qwen:
                ai_layout.addWidget(self._ai_blocked_note)
            if bloqueio_qwen:
                self._ai_later_radio.setText(
                    self._ai_later_radio.text() + "   ← recomendado")
                self._ai_later_radio.setChecked(True)
            else:
                self._ai_now_radio.setText(
                    self._ai_now_radio.text() + "   ← recomendado")
                self._ai_now_radio.setChecked(True)
            self._ai_download_group.setVisible(False)  # so com Completo marcado
            layout.addWidget(self._ai_download_group)
            from . import model_manager as _mm
            variante_label = " + ".join(
                str(_mm.ASR_VARIANTS.get(v, {}).get("label", v)) for v in variantes)
            rodape = QLabel(
                f"Os tamanhos acima são estimativas com o modelo de transcrição "
                f"recomendado para esta máquina ({variante_label}); na próxima etapa "
                "você escolhe o modelo exato e o total se ajusta.\n"
                "Tudo pode ser mudado depois em Ferramentas → Gerenciar modelos… — "
                "nada aqui é definitivo.")
            rodape.setWordWrap(True)
            rodape.setStyleSheet(_style_muted())
            layout.addWidget(rodape)
            layout.addStretch()
            self._profile_radios[recomendado].setChecked(True)
            return page

        def _on_profile_chosen(self, chave: str) -> None:
            from . import capabilities as caps
            self.selected_profile = chave
            self.wants_diarization = chave != "essencial"
            hardware = caps.hardware_snapshot()
            avisos: list[str] = []
            if chave == "completo":
                bloqueio = caps.hardware_blocker(
                    caps.capability("resumo_perguntar"), hardware)
                if bloqueio:
                    avisos.append(
                        f"Atenção: o resumo e as perguntas com AI {bloqueio}. "
                        "Você pode instalar mesmo assim — as demais funções valem.")
                else:
                    aviso_vram = caps.hardware_warning(
                        caps.capability("resumo_perguntar"), hardware)
                    if aviso_vram:
                        avisos.append(
                            f"Atenção: o resumo e as perguntas com AI {aviso_vram} "
                            "— por sua conta e risco.")
            aviso_cpu = caps.cpu_speed_warning(hardware)
            if aviso_cpu:
                avisos.append(aviso_cpu)
            if hasattr(self, "_profile_warning"):
                self._profile_warning.setText("\n".join(avisos))
            if hasattr(self, "_ai_download_group"):
                self._ai_download_group.setVisible(
                    chave == "completo" and getattr(self, "_ai_gb", 0) > 0)

        @property
        def wants_ai_models_now(self) -> bool:
            """Completo + escolha "baixar agora" na pagina de perfis."""
            return (getattr(self, "selected_profile", "") == "completo"
                    and getattr(self, "_ai_gb", 0) > 0
                    and hasattr(self, "_ai_now_radio")
                    and self._ai_now_radio.isChecked())

        def _make_langs_page(self) -> QWizardPage:
            """Idiomas das gravacoes (etapa 4): decide QUAIS pacotes de
            alinhamento baixar. pt pre-marcado; a escolha e por conforto
            (tempos por palavra), nunca requisito — qualquer idioma
            transcreve."""
            from . import model_manager as _mm
            page = QWizardPage()
            page.setTitle("Em que idiomas são as suas gravações?")
            layout = QVBoxLayout(page)
            intro = QLabel(
                "Cada idioma marcado baixa um pacote de tempos por palavra "
                "(~1,2 GB) — é o que faz o duplo clique numa palavra levar o "
                "áudio até ela.\n"
                "Gravações em outros idiomas transcrevem normalmente, apenas "
                "sem os tempos por palavra. Você pode baixar mais idiomas "
                "depois em Ferramentas → Gerenciar modelos…\n"
                "O motor recomendado (TAGARELA) só transcreve português: para "
                "outros idiomas o Transcritório usa o Whisper de reserva e avisa "
                "na hora de transcrever.")
            intro.setWordWrap(True)
            layout.addWidget(intro)
            grade = QGridLayout()
            self._lang_checkboxes: dict[str, QCheckBox] = {}
            ordenados = sorted(_mm.ALIGN_LANGUAGES.items(),
                               key=lambda kv: (kv[0] != "pt", kv[1]["label"]))
            for indice, (code, spec) in enumerate(ordenados):
                cb = QCheckBox(str(spec["label"]))
                cb.setChecked(code == "pt")
                grade.addWidget(cb, indice // 4, indice % 4)
                self._lang_checkboxes[code] = cb
            layout.addLayout(grade)
            layout.addStretch()
            return page

        @property
        def selected_languages(self) -> tuple[str, ...]:
            caixas = getattr(self, "_lang_checkboxes", {})
            return tuple(sorted(c for c, cb in caixas.items() if cb.isChecked()))

        # -- Page factories --

        def _make_welcome_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle(f"Bem-vindo ao {APP_NAME}!")
            page.setSubTitle("")
            layout = QVBoxLayout(page)
            intro = QLabel(
                "Este programa transcreve gravações de entrevistas automaticamente, "
                "usando inteligência artificial que funciona no seu próprio computador.\n\n"
                "Nenhum áudio será enviado para a internet. "
                "Suas gravações ficam sempre no seu computador.\n\n"
                "Para funcionar, o programa precisa baixar alguns componentes de "
                "inteligência artificial. O tamanho depende do que você escolher "
                "instalar — de ~1,5 GB no perfil Essencial a alguns GB no completo; "
                "os números aparecem na próxima etapa. Isso é feito uma única vez.\n\n"
                "Vamos guiá-lo passo a passo. O processo leva uns 10 minutos "
                "e você só precisa fazer isso na primeira vez."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            faq = QGroupBox("O que são \"componentes de AI\"?")
            faq.setCheckable(False)
            faq_layout = QVBoxLayout(faq)
            faq_text = QLabel(
                "São arquivos que ensinam o computador a reconhecer fala em português. "
                "Funcionam como um dicionário muito sofisticado. "
                "Depois de baixados, tudo funciona sem internet."
            )
            faq_text.setWordWrap(True)
            faq_layout.addWidget(faq_text)
            layout.addWidget(faq)
            layout.addStretch()
            return page

        def _make_account_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle("Criar uma conta gratuita")
            layout = QVBoxLayout(page)
            account_intro = QLabel(
                "Os componentes de transcrição ficam em um site chamado Hugging Face — "
                "uma biblioteca pública de inteligência artificial. É gratuito e seguro, "
                "como se fosse um \"Google Acadêmico\" de modelos de AI.\n\n"
                "Você precisa criar uma conta lá para poder baixar os componentes. "
                "Use qualquer e-mail (pode ser o institucional)."
            )
            account_intro.setWordWrap(True)
            layout.addWidget(account_intro)
            btn = QPushButton("Abrir site para criar minha conta →")
            btn.setStyleSheet(f"{_style_ok()} padding: 8px;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/join")))
            layout.addWidget(btn)
            account_next = QLabel(
                "\nDepois de criar sua conta no site (no navegador), "
                "volte aqui e clique em \"Próximo\".\n\n"
                "Já tem conta? Pode pular direto para o próximo passo."
            )
            account_next.setWordWrap(True)
            layout.addWidget(account_next)
            faq = QGroupBox("Dúvidas frequentes")
            faq_l = QVBoxLayout(faq)
            faq_text = QLabel(
                "\"É seguro criar conta?\" — Sim. Hugging Face é reconhecido pela comunidade científica.\n\n"
                "\"Vou pagar alguma coisa?\" — Não. A conta gratuita é suficiente.\n\n"
                "\"Posso usar conta do Google?\" — Sim, o site permite login com Google."
            )
            faq_text.setWordWrap(True)
            faq_l.addWidget(faq_text)
            layout.addWidget(faq)
            layout.addStretch()
            return page

        def _make_terms_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle("Autorizar o modelo de identificação de falantes")
            layout = QVBoxLayout(page)
            terms_intro = QLabel(
                "Além do modelo de transcrição (que é livre), usamos um segundo modelo "
                "que identifica quem está falando em cada trecho — ou seja, separa a fala "
                "do entrevistador da fala do entrevistado.\n\n"
                "Esse modelo exige que você aceite os termos de uso no site. "
                "É só fazer login e clicar em \"Agree and access repository\" (Concordar).\n\n"
                "Se o site estiver em inglês, procure o botão azul \"Agree\"."
            )
            terms_intro.setWordWrap(True)
            layout.addWidget(terms_intro)
            btn = QPushButton("Abrir página do modelo para aceitar os termos →")
            btn.setStyleSheet(f"{_style_ok()} padding: 8px;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/pyannote/speaker-diarization-community-1")))
            layout.addWidget(btn)
            faq = QGroupBox("O que estou aceitando?")
            faq_l = QVBoxLayout(faq)
            terms_faq = QLabel(
                "Você está aceitando os termos de uso do modelo \"pyannote\", criado por "
                "pesquisadores franceses. Os termos dizem basicamente que você usará o modelo "
                "para fins legítimos. Não há custo e não há coleta de dados."
            )
            terms_faq.setWordWrap(True)
            faq_l.addWidget(terms_faq)
            layout.addWidget(faq)
            layout.addStretch()
            return page

        def _make_token_page(self) -> QWizardPage:
            page = _TokenWizardPage()
            return page

        def _make_download_page(self) -> QWizardPage:
            page = _DownloadWizardPage(self)
            return page

        def _make_done_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle("Tudo pronto!")
            page.setFinalPage(True)
            layout = QVBoxLayout(page)
            done_label = QLabel(
                "Os componentes de inteligência artificial foram instalados com sucesso.\n\n"
                "O Transcritório está pronto para usar!\n\n"
                "A partir de agora, toda a transcrição acontece no seu computador, "
                "sem enviar nada para a internet.\n\n"
                "Para começar:\n"
                "  1. Crie um projeto — uma pasta nova onde o Transcritório guarda todo o trabalho\n"
                "  2. Adicione suas gravações — elas continuam onde estão, sem cópia nem alteração\n"
                "  3. Selecione quais deseja transcrever"
            )
            done_label.setWordWrap(True)
            layout.addWidget(done_label)
            layout.addStretch()
            return page

    class _ModelSelectWizardPage(QWizardPage):
        """Page 3: choose which ASR model(s) to download."""

        RECOMMENDED = ["large-v3-turbo", "large-v3"]
        # tiny/base NAO entram (demo_only, 2026-08-30): o primeiro contato
        # nunca deve produzir uma transcricao pessima. Continuam baixaveis
        # pelo Gerenciar modelos, rotulados como demonstracao.
        OTHERS = ["medium", "small"]
        @property
        def FIXED_GB(self) -> float:
            """Componentes alem do ASR, conforme o PERFIL escolhido — lido
            do registro (antes era um literal que divergia) e sensivel ao
            perfil (essencial: nenhum extra)."""
            perfil = getattr(self._wizard, "selected_profile", "padrao")
            if perfil == "essencial":
                return 0.0
            pular = set() if getattr(self._wizard, "wants_diarization", True) else {"diarization"}
            return sum(a.estimated_gb for a in self._model_manager._FIXED_MODELS
                       if a.key not in pular)

        def __init__(self, wizard: "FirstRunWizard") -> None:
            super().__init__()
            from . import capabilities as caps, model_manager
            self._model_manager = model_manager
            self._wizard = wizard
            self.setTitle("Escolha o modelo de transcrição")
            # A recomendacao acompanha a MAQUINA: TAGARELA primario em todas
            # (2026-09-02) + um Whisper de reserva para outros idiomas (turbo
            # com GPU util, small no resto).
            _recs = caps.recommended_asr_variants(caps.hardware_snapshot())
            self._recommended_key = _recs[0]
            self._recommended_keys = tuple(_recs)
            layout = QVBoxLayout(self)
            intro = QLabel(
                "O modelo recomendado para esta máquina já vem marcado.\n\n"
                "Cada caixa marcada é um modelo que SERÁ BAIXADO — marque mais "
                "de um só se quiser comparar qualidades; para instalar um único "
                "modelo, deixe apenas uma caixa marcada. Modelos maiores acertam "
                "mais, porém demoram mais e ocupam mais espaço.\n\n"
                "Você pode instalar ou remover modelos depois, em Ferramentas → "
                "Gerenciar modelos…"
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            self._checkboxes: dict[str, QCheckBox] = {}
            # Grupo de cima: o recomendado da maquina PRIMEIRO (TAGARELA), a
            # reserva em seguida e os grandes do Whisper depois.
            top_keys = list(self._recommended_keys)
            for _rec in self.RECOMMENDED:
                if _rec not in top_keys:
                    top_keys.append(_rec)
            other_keys = [k for k in self.OTHERS if k not in self._recommended_keys]

            def _make_checkbox(key: str) -> QCheckBox:
                info = self._model_manager.ASR_VARIANTS[key]
                if key == self._recommended_key:
                    suffix = "  ★ Recomendado para esta máquina"
                elif key in self._recommended_keys:
                    suffix = "  ★ Recomendado como reserva (outros idiomas)"
                else:
                    suffix = ""
                cb = QCheckBox(f"{info['label']}  ({self._fmt(info['estimated_gb'])}){suffix}")
                cb.setChecked(key in self._recommended_keys)
                cb.setToolTip(info["desc"])
                cb.stateChanged.connect(self._on_changed)
                self._checkboxes[key] = cb
                return cb

            for key in top_keys:
                layout.addWidget(_make_checkbox(key))

            others_group = QGroupBox("Outros modelos")
            others_group.setCheckable(False)
            others_layout = QVBoxLayout(others_group)
            for key in other_keys:
                others_layout.addWidget(_make_checkbox(key))
            layout.addWidget(others_group)

            layout.addStretch()
            self.total_label = QLabel("")
            self.total_label.setStyleSheet(f"{_style_muted()} font-size: 11px;")
            self.total_label.setWordWrap(True)
            layout.addWidget(self.total_label)
            self._update_total()

        @staticmethod
        def _fmt(gb: float) -> str:
            if gb >= 1.0:
                return f"{gb:.1f} GB"
            return f"{int(gb * 1024)} MB"

        def _on_changed(self) -> None:
            self._update_total()
            self.completeChanged.emit()

        def total_gb(self) -> float:
            """Espaço em disco do que está marcado + componentes fixos."""
            asr_gb = sum(
                self._model_manager.ASR_VARIANTS[k]["estimated_gb"]
                for k, cb in self._checkboxes.items()
                if cb.isChecked()
            )
            return asr_gb + self.FIXED_GB

        def _update_total(self) -> None:
            total = self.total_gb()
            # estimated_gb e tamanho EM DISCO (download real e ~metade, o cache
            # HF duplica) — rotular como "espaco em disco", nao "download".
            # Enumerar o que sera baixado tira a duvida "marquei dois, vem
            # os dois?" — sim, e o rotulo diz com todas as letras.
            nomes = [
                f"{self._model_manager.ASR_VARIANTS[k]['label']} ({self._fmt(self._model_manager.ASR_VARIANTS[k]['estimated_gb'])})"
                for k, cb in self._checkboxes.items() if cb.isChecked()
            ]
            extras = self.FIXED_GB
            if not nomes:
                self.total_label.setText("Marque ao menos um modelo para continuar.")
                return
            linha = "Será baixado: " if len(nomes) == 1 else f"Serão baixados {len(nomes)} modelos: "
            sufixo = (" + componentes do perfil escolhido"
                      if extras else " (perfil Essencial: só o modelo de transcrição)")
            self.total_label.setText(
                f"{linha}{', '.join(nomes)}{sufixo}.\n"
                f"Espaço em disco necessário: ~{self._fmt(total)}.")

        def selected_asr_variants(self) -> list[str]:
            return [k for k, cb in self._checkboxes.items() if cb.isChecked()]

        def isComplete(self) -> bool:
            return len(self.selected_asr_variants()) > 0

        def validatePage(self) -> bool:
            variants = self.selected_asr_variants()
            if not variants:
                return False
            self._wizard.selected_asr_variants = variants
            return True

    class _TokenWizardPage(QWizardPage):
        """Page 4: token entry with pre-validation."""

        def __init__(self) -> None:
            super().__init__()
            self.setTitle("Criar e colar a chave de acesso")
            layout = QVBoxLayout(self)
            token_intro = QLabel(
                "Agora você precisa criar uma \"chave de acesso\" no Hugging Face. "
                "É como uma senha temporária que permite ao Transcritório baixar os componentes.\n\n"
                "Como criar (3 cliques):\n"
                "  1. Clique no botão abaixo para abrir a página de chaves.\n"
                "  2. Clique em \"Create new token\".\n"
                "     • Em \"Token name\", escreva: Transcritorio\n"
                "     • Em \"Type\", selecione: Read\n"
                "     • Clique em \"Create token\"\n"
                "  3. Copie a chave gerada e cole no campo abaixo."
            )
            token_intro.setWordWrap(True)
            layout.addWidget(token_intro)
            btn = QPushButton("Abrir página de chaves no Hugging Face →")
            btn.setStyleSheet(f"{_style_ok()} padding: 8px;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/settings/tokens")))
            layout.addWidget(btn)
            layout.addSpacing(12)
            layout.addWidget(QLabel("Cole sua chave aqui:"))
            self.token_edit = QLineEdit()
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_edit.setPlaceholderText("Cole aqui a chave (começa com hf_…)")
            # Pre-fill from secure vault if available
            from . import token_vault
            saved = token_vault.retrieve()
            if saved:
                self.token_edit.setText(saved)
            self.token_edit.textChanged.connect(self._on_token_changed)
            layout.addWidget(self.token_edit)
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            layout.addWidget(self.status_label)
            layout.addStretch()
            privacy = QLabel(
                "A chave é usada apenas para baixar os componentes e depois é descartada.\n"
                "Ela nunca é enviada para nenhum outro servidor."
            )
            privacy.setStyleSheet(f"{_style_muted()} font-size: 11px;")
            privacy.setWordWrap(True)
            layout.addWidget(privacy)

        def _on_token_changed(self) -> None:
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.completeChanged.emit()

        def isComplete(self) -> bool:
            token = self.token_edit.text().strip()
            return token.startswith("hf_") and len(token) >= 10

        def validatePage(self) -> bool:
            from . import model_manager
            from . import token_vault
            token = self.token_edit.text().strip()
            if not token:
                self.status_label.setText("Cole a chave de acesso no campo acima.")
                self.status_label.setStyleSheet(_style_err())
                return False
            self.status_label.setText("Verificando sua chave…")
            self.status_label.setStyleSheet(_style_muted())
            # Force UI repaint before blocking call
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            # Validate token
            result = model_manager.validate_token(token)
            if not result["valid"]:
                self.status_label.setText(result["message"])
                self.status_label.setStyleSheet(_style_err())
                return False
            # Check gated model access
            gated = model_manager.check_gated_access(token)
            if not gated["access"]:
                self.status_label.setText(gated["message"])
                self.status_label.setStyleSheet(_style_warn())
                return False
            self.status_label.setText(f"✓ {result['message']} {gated['message']}")
            self.status_label.setStyleSheet(_style_ok())
            # Persist validated token in secure vault
            try:
                token_vault.store(token)
            except Exception as exc:
                _logger.warning("token_vault.store falhou: %s", exc)
            return True

        def token(self) -> str:
            return self.token_edit.text().strip()

    def _wizard_optional_keys(profile: str, hw, cached: set[str]) -> tuple[str, ...]:
        """Modelos de IA que o assistente baixa no perfil Completo quando o
        usuario escolhe "baixar agora" (pura, testavel).

        Exclui o que ja esta em cache e o Qwen quando o hardware nao da
        conta — nao baixar 8,7 GB inuteis; o aviso da pagina de perfil
        ja explicou o porque."""
        if profile != "completo":
            return ()
        from . import capabilities as _caps
        chaves: list[str] = []
        for key in ("search_encoder", "ner_gliner", "llm_qwen"):
            if key in cached:
                continue
            cap = _caps.capability_for_model(key)
            if cap is not None and _caps.hardware_blocker(cap, hw):
                continue
            chaves.append(key)
        return tuple(chaves)

    class _DownloadWizardPage(QWizardPage):
        """Page 4: model download with progress."""

        def __init__(self, wizard: "FirstRunWizard") -> None:
            super().__init__()
            self._wizard = wizard
            self._worker: "_SetupDownloadThread | None" = None
            self._download_started = False
            self._download_done = False
            self.setTitle("Baixar os componentes")
            self.setFinalPage(False)
            layout = QVBoxLayout(self)
            download_intro = QLabel(
                "Tudo pronto! Agora vamos baixar os componentes de inteligência artificial.\n\n"
                "Isso pode levar de 5 a 30 minutos, dependendo da velocidade da sua internet. "
                "Você pode continuar usando o computador normalmente."
            )
            download_intro.setWordWrap(True)
            layout.addWidget(download_intro)
            self.progress_label = QLabel("")
            self.progress_label.setWordWrap(True)
            layout.addWidget(self.progress_label)
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 0)
            layout.addWidget(self.progress_bar)
            # Feedback do 1o teste real (2026-08-30): "não tem como cancelar
            # o download". O botao interrompe SEM perder o progresso — o
            # cache parcial e retomado no proximo download.
            self.cancel_download_button = QPushButton("Cancelar download")
            self.cancel_download_button.setVisible(False)
            self.cancel_download_button.clicked.connect(self._on_cancel_clicked)
            layout.addWidget(self.cancel_download_button)
            layout.addStretch()
            from .progress_bar_fallback import ProgressBarController
            self._bar_ctrl = ProgressBarController()
            self._cancelled_by_user = False

        def initializePage(self) -> None:
            if self._download_started:
                return
            # Check disk space before starting download
            from . import model_manager
            # O assistente sabe o que vai baixar: passar o tamanho real em
            # vez de deixar o limiar generico decidir.
            select_page = self._wizard.page(FirstRunWizard.PAGE_MODEL_SELECT)
            required_gb = getattr(select_page, "total_gb", lambda: None)()
            # Etapa 4: pacotes de idioma escolhidos (padrao/completo) entram
            # no download e na conta de disco. Essencial: sem alinhamento.
            align_langs: tuple[str, ...] | None = None
            if getattr(self._wizard, "selected_profile", "padrao") != "essencial":
                align_langs = getattr(self._wizard, "selected_languages", ("pt",))
                for code in align_langs or ():
                    # pt ja esta no FIXED_GB da pagina de modelos — somar
                    # so os idiomas EXTRAS para nao contar em dobro.
                    if code != "pt" and model_manager.align_language_supported(code):
                        required_gb = (required_gb or 0.0) + float(
                            model_manager.align_asset_for(code).estimated_gb)
            # Completo com "baixar IA agora": os opcionais entram no download
            # do assistente (e na conta de disco).
            optional_keys: tuple[str, ...] = ()
            self._qwen_excluido_por_hardware = False
            if getattr(self._wizard, "wants_ai_models_now", False):
                from . import capabilities as _caps_dl
                hw = _caps_dl.hardware_snapshot()
                cached = _caps_dl.cached_model_keys()
                optional_keys = _wizard_optional_keys(
                    getattr(self._wizard, "selected_profile", ""), hw, cached)
                self._qwen_excluido_por_hardware = (
                    "llm_qwen" not in optional_keys and "llm_qwen" not in cached)
                if optional_keys:
                    required_gb = (required_gb or 0.0) + sum(
                        float(model_manager.optional_model(k).estimated_gb)
                        for k in optional_keys)
            disk = model_manager.check_disk_space(required_gb)
            if not disk["ok"]:
                self.progress_label.setText(disk["message"])
                self.progress_label.setStyleSheet(_style_err())
                return
            self._download_started = True
            token_page = self._wizard.page(FirstRunWizard.PAGE_TOKEN)
            token = token_page.token() if hasattr(token_page, "token") else ""
            asr_variants = getattr(self._wizard, "selected_asr_variants", None)
            self.progress_label.setText("Conectando ao Hugging Face…")
            self._bar_ctrl.start(self.progress_bar)
            self._worker = _SetupDownloadThread(
                token,
                asr_variants=asr_variants,
                include_diarization=bool(getattr(self._wizard, "wants_diarization", True)),
                include_alignment=getattr(self._wizard, "selected_profile", "padrao") != "essencial",
                optional_keys=optional_keys,
                align_languages=align_langs,
            )
            self._worker.progress.connect(self._on_progress)
            self._worker.finished_ok.connect(self._on_done)
            self._worker.failed.connect(self._on_failed)
            self._cancelled_by_user = False
            self.cancel_download_button.setVisible(True)
            self.cancel_download_button.setEnabled(True)
            self._worker.start()

        def _on_cancel_clicked(self) -> None:
            if self._worker is None or not self._worker.isRunning():
                return
            self._cancelled_by_user = True
            self.cancel_download_button.setEnabled(False)
            self.progress_label.setText(
                "Cancelando… o que já foi baixado fica guardado e será "
                "aproveitado se você retomar.")
            self.progress_label.setStyleSheet(_style_muted())
            self._worker.request_cancel()

        def isComplete(self) -> bool:
            return self._download_done

        def _on_progress(self, message: str, percent: int) -> None:
            self.progress_label.setText(message)
            self._bar_ctrl.update(self.progress_bar, percent, message)

        def _on_done(self) -> None:
            self._download_done = True
            self._wizard.download_completed = True
            self.cancel_download_button.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            mensagem = "Componentes baixados e verificados com sucesso!"
            if getattr(self, "_qwen_excluido_por_hardware", False):
                mensagem += ("\nO modelo de análise (8,7 GB) não foi baixado: "
                             "precisa de placa NVIDIA. As demais funções do "
                             "perfil Completo estão prontas.")
            self.progress_label.setText(mensagem)
            self.progress_label.setStyleSheet(_style_ok())
            self.completeChanged.emit()

        def _on_failed(self, message: str) -> None:
            self.cancel_download_button.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            if self._cancelled_by_user:
                # Cancelar nao e erro: dizer o que aconteceu e como seguir.
                self.progress_label.setText(
                    "Download cancelado. O que já foi baixado fica guardado.\n"
                    "Use ← Voltar para mudar a escolha e avance para retomar — "
                    "ou feche com \"Pular por agora\" e retome depois em "
                    "Ferramentas → Gerenciar modelos…")
                self.progress_label.setStyleSheet(_style_muted())
            else:
                self.progress_label.setText(f"Erro: {message}\n\nVerifique sua conexão e tente novamente.")
                self.progress_label.setStyleSheet(_style_err())
            self._download_started = False  # allow retry via Back + Next

    class _SetupDownloadThread(QThread):
        progress = Signal(str, int)
        finished_ok = Signal()
        failed = Signal(str)

        def __init__(self, token: str, asr_variants: list[str] | None = None,
                     include_diarization: bool = True, include_alignment: bool = True,
                     optional_keys: tuple[str, ...] = (),
                     align_languages: tuple[str, ...] | None = None) -> None:
            super().__init__()
            self.token = token
            self.asr_variants = asr_variants
            self.include_diarization = include_diarization
            self.include_alignment = include_alignment
            self.optional_keys = tuple(optional_keys)
            self.align_languages = align_languages
            self._cancel_requested = False

        def request_cancel(self) -> None:
            self._cancel_requested = True

        def run(self) -> None:
            from .model_manager import _download_diag_log
            try:
                def on_progress(detail: dict) -> None:
                    msg = detail.get("message", "")
                    pct = int(detail.get("progress", 0))
                    self.progress.emit(msg, pct)
                result = app_service.download_models(
                    token=self.token,
                    progress_callback=on_progress,
                    should_cancel=lambda: self._cancel_requested,
                    asr_variants=self.asr_variants,
                    include_diarization=self.include_diarization,
                    include_alignment=self.include_alignment,
                    align_languages=self.align_languages,
                )
                result_failures = getattr(result, "failures", 0)
                result_message = getattr(result, "message", "")
                _download_diag_log(
                    f"[wizard] download_models returned: failures={result_failures} "
                    f"message={result_message!r}"
                )
                if result_failures:
                    _download_diag_log("[wizard] emitting failed signal")
                    self.failed.emit(str(result_message or "Falha ao baixar um ou mais componentes."))
                    return
                # Completo com "baixar IA agora": os opcionais em sequencia,
                # no mesmo canal de progresso (cada um nomeia a si mesmo).
                from .model_manager import download_optional_model, optional_model
                for key in self.optional_keys:
                    if self._cancel_requested:
                        self.failed.emit("Download cancelado.")
                        return
                    falhas = download_optional_model(
                        key, progress_callback=on_progress,
                        should_cancel=lambda: self._cancel_requested)
                    _download_diag_log(f"[wizard] optional {key}: failures={falhas}")
                    if falhas:
                        rotulo = str(optional_model(key).label)
                        self.failed.emit(
                            f"Falha ao baixar {rotulo} — você pode tentar de "
                            "novo depois em Ferramentas → Gerenciar modelos…")
                        return
                _download_diag_log("[wizard] emitting finished_ok signal")
                self.finished_ok.emit()
            except Exception as exc:
                from .utils import sanitize_message
                import traceback
                _download_diag_log(f"[wizard] UNCAUGHT: {type(exc).__name__}: {exc}")
                for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
                    for subline in line.rstrip().splitlines():
                        _download_diag_log(f"  {subline}")
                self.failed.emit(sanitize_message(str(exc)))

    class ProjectChooserDialog(QDialog):
        """Shown when AI components are ready — lets user pick or create a project."""

        def __init__(self, context: app_service.ProjectContext | None, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.choice = "continue"
            self.selected_recent: Path | None = None
            self.setWindowTitle(APP_NAME)
            self.resize(520, 400)
            layout = QVBoxLayout(self)
            title = QLabel(APP_NAME)
            title.setStyleSheet("font-size: 20px; font-weight: 700;")
            layout.addWidget(title)

            from . import recent_projects
            recent = recent_projects.load_recent()

            if context is not None:
                project_name = str(context.project.get("project_name") or context.paths.project_root.name)
                current_label = QLabel(f"Projeto atual: {project_name}")
                current_label.setStyleSheet(_style_muted())
                layout.addWidget(current_label)
                btn_continue = QPushButton("Continuar projeto atual")
                btn_continue.setToolTip("Abrir a lista de arquivos deste projeto.")
                btn_continue.clicked.connect(lambda: self.select_choice("continue"))
                layout.addWidget(btn_continue)

            if recent:
                recent_label = QLabel("Projetos recentes")
                recent_label.setStyleSheet("font-weight: 700; margin-top: 8px;")
                layout.addWidget(recent_label)
                for project_path in recent[:5]:
                    name = project_path.name
                    btn = QPushButton(f"{name}  ({project_path})")
                    btn.setToolTip(str(project_path))
                    btn.clicked.connect(lambda _c=False, p=project_path: self.select_recent(p))
                    layout.addWidget(btn)

            layout.addSpacing(12)
            for choice, label, help_text in [
                ("new", "Novo projeto", "Escolher uma pasta e criar um novo projeto de transcrição."),
                ("open", "Abrir projeto existente", "Selecionar um arquivo .transcritorio de um projeto existente."),
            ]:
                button = QPushButton(label)
                button.setToolTip(help_text)
                button.clicked.connect(lambda _checked=False, selected=choice: self.select_choice(selected))
                layout.addWidget(button)

            layout.addStretch()
            status_label = QLabel("✓ Componentes de AI instalados")
            status_label.setStyleSheet(f"{_style_ok()} font-size: 11px;")
            status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(status_label)

        def select_choice(self, choice: str) -> None:
            self.choice = choice
            self.accept()

        def select_recent(self, path: Path) -> None:
            self.choice = "recent"
            self.selected_recent = path
            self.accept()


    class NewProjectDialog(QDialog):
        """Novo projeto com o modelo mental a vista (1o teste real,
        2026-08-30): o usuario nao sabia se criaria uma pasta ou arquivos
        soltos, nem o que aconteceria com os audios. O preview mostra
        EXATAMENTE a pasta que sera criada, antes de criar."""

        def __init__(self, parent: QWidget | None = None, initial_dir: str = "") -> None:
            super().__init__(parent)
            self.setWindowTitle("Novo projeto")
            self.setMinimumWidth(560)
            layout = QVBoxLayout(self)
            intro = QLabel(
                "Um projeto é uma pasta única com todo o trabalho do Transcritório:\n"
                "  •  suas gravações NÃO são copiadas nem alteradas — o projeto "
                "apenas as referencia onde estão;\n"
                "  •  tudo o que o programa produz fica dentro dessa pasta;\n"
                "  •  as versões finais para leitura ficam na subpasta Resultados.")
            intro.setWordWrap(True)
            intro.setStyleSheet(_style_muted())
            layout.addWidget(intro)
            grid = QGridLayout()
            grid.addWidget(QLabel("Nome do projeto:"), 0, 0)
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("ex.: Entrevistas Bairro Sul 2026")
            self.name_edit.textChanged.connect(self._update_preview)
            grid.addWidget(self.name_edit, 0, 1, 1, 2)
            grid.addWidget(QLabel("Criar em:"), 1, 0)
            self.dir_edit = QLineEdit(initial_dir or str(Path.home()))
            self.dir_edit.textChanged.connect(self._update_preview)
            grid.addWidget(self.dir_edit, 1, 1)
            browse = QPushButton("Procurar…")
            browse.clicked.connect(self._browse)
            grid.addWidget(browse, 1, 2)
            layout.addLayout(grid)
            self.preview_label = QLabel("")
            self.preview_label.setWordWrap(True)
            layout.addWidget(self.preview_label)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
            self._ok_button.setText("Criar projeto")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            self._update_preview()

        def _browse(self) -> None:
            folder = QFileDialog.getExistingDirectory(
                self, "Escolha onde criar o projeto", self.dir_edit.text())
            if folder:
                self.dir_edit.setText(folder)

        def project_name(self) -> str:
            return self.name_edit.text().strip()

        def project_root(self) -> Path:
            return Path(self.dir_edit.text().strip()) / safe_project_folder_name(
                self.project_name())

        def _update_preview(self) -> None:
            name = self.project_name()
            base = self.dir_edit.text().strip()
            valido = bool(name) and bool(base) and Path(base).is_dir()
            self._ok_button.setEnabled(valido)
            if not name:
                self.preview_label.setText("Digite um nome para ver o que será criado.")
                self.preview_label.setStyleSheet(_style_muted())
                return
            destino = self.project_root()
            if destino.exists():
                self.preview_label.setText(f"⚠ Já existe uma pasta neste local:\n{destino}")
                self.preview_label.setStyleSheet(_style_err())
                self._ok_button.setEnabled(False)
                return
            self.preview_label.setText(
                f"Será criada a pasta:\n{destino}\n"
                "— e todo o trabalho deste projeto ficará dentro dela.")
            self.preview_label.setStyleSheet(_style_ok() if valido else _style_err())

        def accept(self) -> None:
            if self.project_root().exists():
                QMessageBox.warning(
                    self, "Projeto já existe",
                    f"Já existe uma pasta com este nome:\n{self.project_root()}")
                return
            super().accept()


    class ModelSetupDialog(QDialog):
        def __init__(
            self,
            parent: QWidget | None = None,
            asr_variants: list[str] | None = None,
            include_diarization: bool = True,
            include_alignment: bool = True,
            align_languages: tuple[str, ...] | None = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("Preparar modelos locais")
            self.resize(720, 640)
            layout = QVBoxLayout(self)
            # O dialogo mostra e baixa o que ESTA instalacao precisa (perfil
            # + modelo configurado), nao o conjunto de fabrica. E o token so
            # e exigido quando ha modelo RESTRITO pendente: a instalacao
            # Essencial existe para dispensar conta/token, e o 1o teste real
            # (2026-08-30) travou o usuario numa exigencia que nao valia.
            from . import model_manager as _mm
            self._scope = {
                "asr_variants": asr_variants,
                "include_diarization": include_diarization,
                "include_alignment": include_alignment,
                "align_languages": align_languages,
            }
            try:
                pendentes = [item for item in _mm.status(
                    asr_variants=asr_variants,
                    include_diarization=include_diarization,
                    include_alignment=include_alignment,
                    align_languages=align_languages) if not item.cached]
                self._nada_pendente = not pendentes
            except Exception:  # noqa: BLE001 - config invalida nao trava o dialogo
                pendentes = []
                self._nada_pendente = False
            self._needs_token = any(item.asset.gated for item in pendentes)

            title = QLabel("Preparar modelos locais")
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            layout.addWidget(title)

            intro = QTextEdit()
            intro.setReadOnly(True)
            if self._nada_pendente:
                # Escopo completo em cache: um "Baixar modelos" sem nada a
                # baixar confundia (teste real 2026-08-30).
                intro.setPlainText(
                    "Todos os modelos desta instalação já estão baixados e "
                    "prontos — não há nada para baixar agora.\n\n"
                    "Para ver, baixar ou remover modelos (inclusive os de AI), "
                    "use Ferramentas → Gerenciar modelos…")
                intro.setMinimumHeight(90)
            elif self._needs_token:
                intro.setPlainText(
                    "O token Hugging Face é usado apenas para baixar modelos. "
                    "Áudios, vídeos e transcrições continuam neste computador.\n\n"
                    "Passo a passo:\n"
                    "1. Crie ou entre na sua conta do Hugging Face.\n"
                    "2. Abra o modelo pyannote/speaker-diarization-community-1 e aceite os termos.\n"
                    "3. Crie um token de leitura no Hugging Face.\n"
                    "4. Cole o token abaixo e baixe os modelos.\n"
                    "5. Depois do download, o Transcritório verifica o carregamento local/offline.\n\n"
                    "Para preparar outro computador, repita estes mesmos passos com o token do usuário daquele computador. "
                    "Nunca use nem compartilhe o token de outra pessoa."
                )
                intro.setMinimumHeight(180)
            else:
                intro.setPlainText(
                    "Os componentes da sua instalação são todos públicos: "
                    "nenhuma conta e nenhum token são necessários.\n\n"
                    "Clique em Baixar modelos para completar o que falta. "
                    "Áudios, vídeos e transcrições continuam neste computador."
                )
                intro.setMinimumHeight(90)
            layout.addWidget(intro)

            links = QHBoxLayout()
            for label, url in [
                ("Criar conta", "https://huggingface.co/join"),
                ("Aceitar pyannote", "https://huggingface.co/pyannote/speaker-diarization-community-1"),
                ("Criar token", "https://huggingface.co/settings/tokens"),
            ]:
                button = QPushButton(label)
                button.setVisible(self._needs_token)
                button.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
                links.addWidget(button)
            links.addStretch()
            layout.addLayout(links)

            self._token_label = QLabel("Token Hugging Face deste usuário:")
            self._token_label.setVisible(self._needs_token)
            layout.addWidget(self._token_label)
            self.token_edit = QLineEdit()
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_edit.setPlaceholderText("hf_…")
            self.token_edit.setVisible(self._needs_token)
            # Pre-fill from secure vault if available
            from . import token_vault
            saved = token_vault.retrieve() if self._needs_token else ""
            if saved:
                self.token_edit.setText(saved)
            layout.addWidget(self.token_edit)

            self.remember_checkbox = QCheckBox("Lembrar neste computador usando cofre seguro")
            self.remember_checkbox.setVisible(self._needs_token)
            # Opt-out by default: the expected behavior for 95% of users is
            # to paste the token once and never see this dialog again. Users
            # who share a machine can explicitly uncheck.
            self.remember_checkbox.setChecked(True)
            self.remember_checkbox.setToolTip("Armazena o token criptografado no cofre de credenciais do sistema (Gerenciador de Credenciais no Windows, Keychain no macOS, SecretService no Linux). Só você neste computador pode acessar.")
            layout.addWidget(self.remember_checkbox)

            status = QTextEdit()
            status.setReadOnly(True)
            try:
                status.setPlainText(app_service.models_status_text(**self._scope))
            except Exception as exc:  # noqa: BLE001
                status.setPlainText(f"Não foi possível listar os modelos: {exc}")
            status.setMinimumHeight(120)
            layout.addWidget(status)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Baixar modelos")
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not self._nada_pendente)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def token(self) -> str:
            return self.token_edit.text().strip()

        def accept(self) -> None:
            if self._needs_token and not self.token():
                QMessageBox.warning(
                    self,
                    "Token necessário",
                    "Cole o token de leitura do Hugging Face deste usuário para baixar o modelo de separação de falantes.",
                )
                return
            if not self._needs_token:
                # Nada restrito pendente: nao ha token a validar/guardar.
                QDialog.accept(self)
                return
            # Persist token if "remember" is checked. Backend errors (keyring
            # unavailable, DPAPI access denied, etc.) must NEVER crash the app
            # — log and warn, then proceed.
            from . import token_vault
            from . import model_manager as _mm
            try:
                if self.remember_checkbox.isChecked():
                    token_vault.store(self.token())
                else:
                    token_vault.clear()
            except Exception as exc:
                _mm._download_diag_log(
                    f"[ModelSetupDialog.accept] token_vault falhou: "
                    f"{type(exc).__name__}: {exc}"
                )
                _logger.warning("token_vault falhou em accept(): %s", exc)
                QMessageBox.warning(
                    self,
                    "Cofre de token indisponível",
                    f"Não foi possível salvar o token no cofre seguro.\n\n"
                    f"{type(exc).__name__}: {exc}\n\n"
                    "O download vai prosseguir usando o token desta sessão. "
                    "Você precisará colar o token de novo da próxima vez.",
                )
            super().accept()


    class ReviewStudioWindow(QMainWindow):
        def __init__(self, project_root: Path | None = None) -> None:
            super().__init__()
            # Canal de teste visivel no titulo: a beta e a estavel podem ficar
            # abertas ao mesmo tempo, e confundir as duas seria pior do que
            # nao ter beta nenhuma.
            self.setWindowTitle(APP_NAME + channel_suffix())
            icon_path = app_asset_path(APP_ICON_FILE)
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
            self.resize(1440, 900)
            self.context: app_service.ProjectContext | None = None
            try:
                self.context = app_service.load_project(project_root=project_root)
                from . import recent_projects
                recent_projects.save_recent(self.context.paths.project_root)
            except FileNotFoundError:
                pass
            except Exception as exc:
                print(f"Aviso: não foi possível carregar o projeto: {exc}", file=sys.stderr)
            self.statuses = []
            self._status_map: dict[str, Any] = {}
            self._checked_ids: set[str] = set()
            self._trash_undo: list[str] = []  # trash_ids da sessao atual, LIFO
            self._trash_redo: list[str] = []
            self._trash_worker: TrashMoveWorker | None = None
            self._trash_session_ids: list[str] = []  # trash_ids criados nesta sessao (para purge no close)
            self._trash_busy: bool = False
            self.review: dict[str, Any] | None = None
            self.current_interview_id: str | None = None
            self.turns: list[dict[str, Any]] = []
            self.word_index: list[dict[str, Any]] = []
            self._word_uncertain_cutoff: float | None = None
            # Retrato (hardware + modelos em cache) que decide o que fica
            # disponivel; None = recalcular na proxima consulta.
            self._caps_cache: tuple[Any, set[str], dict[str, float]] | None = None
            # Acao a reexecutar quando o worker "Preparar modelos" concluir
            # (a retomada real do gate ensure_models_ready — F2).
            self._retry_after_models: Callable[[], None] | None = None
            # Linha sob o cursor enquanto o menu de contexto esta aberto
            # (alvo das acoes destrutivas disparadas por ele — F7).
            self._context_cursor_row: int | None = None
            self.current_turn_id: str | None = None
            self.current_play_row: int | None = None
            self.media_candidates: list[Path] = []
            self.media_candidate_index = 0
            # Painel de video (2026-08-31): preferencia vale SO na sessao
            # (decisao do usuario); _video_panel_visible e o ultimo estado
            # APLICADO — detector de transicao do review_splitter.
            self._video_user_hidden = False
            self._video_panel_visible = False
            # Aviso de trocas de falante dispensado (por entrevista, sessao).
            self._boundary_dismissed: set[str] = set()
            self.worker: PipelineWorker | None = None
            self.current_job_label = ""
            self._loading_editor = False
            self._editor_dirty = False
            self._save_failed = False
            self._slider_dragging = False
            self._changing_selection = False
            self._fallback_media_attempted = False
            self._voice_naming_declined: set[str] = set()  # "De quem e esta voz?" recusado nesta sessao
            self._confirm_migrated: set[str] = set()  # migracao implicita ja gravada nesta sessao
            self._close_after_worker = False
            self.undo_stack = QUndoStack(self)

            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            # Seguir o dispositivo PADRAO do sistema: o QAudioOutput fica
            # preso ao dispositivo do momento em que foi criado, entao
            # conectar um fone (bluetooth OU com fio) depois de abrir o app
            # deixava o som na caixa de som (teste real, 2026-08-30).
            self._media_devices = QMediaDevices(self)
            self._media_devices.audioOutputsChanged.connect(self._follow_default_audio_output)
            self.autosave_timer = QTimer(self)
            self.autosave_timer.setInterval(1200)
            self.autosave_timer.setSingleShot(True)
            self.autosave_timer.timeout.connect(self.save_current_turn)

            self._build_actions()
            self._build_ui()
            self.set_editor_enabled(False)
            self._connect_player()
            self.refresh_interviews()
            # Global drag-and-drop: users can drop audio/video files from
            # Explorer/Finder/Nautilus anywhere on the window.
            self.setAcceptDrops(True)

        def _build_actions(self) -> None:
            self.add_folder_action = QAction("Adicionar pasta…", self)
            self.add_folder_action.setToolTip("Escolher uma pasta com áudios ou vídeos.")
            self.add_folder_action.triggered.connect(self.add_audio_folder)

            self.new_project_action = QAction("Novo projeto…", self)
            self.new_project_action.setShortcut(QKeySequence("Ctrl+N"))
            self.new_project_action.setToolTip("Criar uma nova pasta de projeto de transcrições. (Ctrl+N)")
            self.new_project_action.triggered.connect(self.new_project)

            self.open_project_action = QAction("Abrir projeto…", self)
            self.open_project_action.setShortcut(QKeySequence("Ctrl+O"))
            self.open_project_action.setToolTip(
                "Abrir um projeto existente: escolha o arquivo .transcritorio "
                "dentro da pasta do projeto. (Ctrl+O)")
            self.open_project_action.triggered.connect(self.open_project)

            self.add_files_action = QAction("Adicionar arquivos…", self)
            self.add_files_action.setShortcut(QKeySequence("Ctrl+I"))
            self.add_files_action.setToolTip(
                "Adicionar arquivos de áudio ou vídeo ao projeto. (Ctrl+I)\n"
                "Também é possível arrastar arquivos do Explorer/Finder direto para a janela."
            )
            self.add_files_action.triggered.connect(self.add_audio_files)

            # R3: as acoes orfas "Salvar projeto", "Comecar", "Configurar
            # modelos..." e "Status dos modelos" foram removidas — nunca
            # tiveram casa em menu/botao (salvamento de projeto e
            # automatico; o empty-state e o comecar; o gerenciador de
            # modelos absorveu configuracao e status).

            self.open_project_folder_action = QAction("Abrir pasta do projeto", self)
            self.open_project_folder_action.setToolTip("Abrir a pasta do projeto no Explorador de Arquivos.\nDesativado sem projeto aberto.")
            self.open_project_folder_action.triggered.connect(self.open_project_folder)


            self.exit_action = QAction("Sair", self)
            self.exit_action.setToolTip("Fechar o Transcritório.")
            self.exit_action.triggered.connect(self.close)

            self.apply_metadata_action = QAction("Editar propriedades…", self)
            self.apply_metadata_action.setToolTip("Aplicar língua, falantes, rótulos ou contexto aos arquivos selecionados.")
            self.apply_metadata_action.triggered.connect(self.apply_metadata_to_selected)

            self.queue_action = QAction("Ver fila de processamento", self)
            self.queue_action.setToolTip("Ver o estado das transcrições em lote.")
            self.queue_action.triggered.connect(self.show_queue)

            self.engine_settings_action = QAction("Configurar transcrição…", self)
            self.engine_settings_action.setToolTip("Escolher GPU/CPU, modelo, precisao e batch.")
            self.engine_settings_action.triggered.connect(self.configure_engine)



            self.model_manager_action = QAction("Gerenciar modelos…", self)
            self.model_manager_action.setToolTip("Ver tamanho em disco, remover modelos, trocar token HF, baixar outros.")
            self.model_manager_action.triggered.connect(self.show_model_manager)

            # R3: "Atualizar biblioteca" fundiu aqui — F5 tambem procura
            # gravações novas nas pastas do projeto (run_manifest_job
            # termina em refresh), em vez de so reler o que ja se conhece.
            self.reload_list_action = QAction("Recarregar lista", self)
            self.reload_list_action.setShortcut(QKeySequence("F5"))
            self.reload_list_action.setToolTip("Procurar gravações novas nas pastas do projeto e recarregar a lista. (F5)")
            self.reload_list_action.triggered.connect(self.run_manifest_job)

            self.open_transcript_action = QAction("Abrir transcrição", self)
            self.open_transcript_action.setToolTip("Abrir a transcrição do arquivo selecionado (duplo-clique ou Enter na linha). Selecione um arquivo na lista.")
            self.open_transcript_action.triggered.connect(self.open_selected_review)

            self.transcribe_action = QAction("Transcrever selecionados", self)
            self.transcribe_action.setToolTip("Transcrever os arquivos selecionados na lista do projeto.")
            self.transcribe_action.triggered.connect(self.run_full_transcription_job)

            self.transcribe_pending_action = QAction("Transcrever todos não transcritos", self)
            self.transcribe_pending_action.setToolTip("Transcrever todos os arquivos do projeto que ainda não têm transcrição.")
            self.transcribe_pending_action.triggered.connect(self.run_pending_transcription_job)

            # R3: a chave de separacao saiu da toolbar (checkbox solto) e
            # virou item checkavel no dropdown do Transcrever + catalogo.
            self.diarize_toggle_action = QAction("Separar falantes", self)
            self.diarize_toggle_action.setCheckable(True)
            self.diarize_toggle_action.setToolTip(
                "Identifica automaticamente quem está falando (Entrevistador/Entrevistado).\n"
                "Ligado sozinho sempre que o recurso está instalado neste computador.\n"
                "Desative para áudios com um único falante ou para transcrever mais rápido.")
            self.diarize_toggle_action.toggled.connect(self._on_diarize_toggled)

            self.transcribe_current_action = QAction("Transcrever este arquivo", self)
            self.transcribe_current_action.setToolTip("Transcrever a mídia aberta agora.")
            self.transcribe_current_action.triggered.connect(self.run_current_file_transcription_job)

            self.retranscribe_current_action = QAction("Transcrever novamente…", self)
            self.retranscribe_current_action.setToolTip(
                "Refazer a transcrição do arquivo aberto, podendo escolher outro modelo.\n"
                "A transcrição editável é recriada (cópia de segurança das edições em edits/backups).")
            self.retranscribe_current_action.triggered.connect(self.retranscribe_current_file)

            self.save_action = QAction("Salvar transcrição", self)
            self.save_action.setShortcut(QKeySequence.StandardKey.Save)
            self.save_action.setToolTip("Salvar a transcrição editável desta entrevista.")
            self.save_action.triggered.connect(lambda _checked=False: self.save_current_turn(force=True))

            self.generate_files_action = QAction("Exportar…", self)
            self.generate_files_action.setShortcut(QKeySequence("Ctrl+E"))
            self.generate_files_action.setToolTip("Exportar a transcrição aberta, os arquivos selecionados ou todas as transcrições. (Ctrl+E)")
            self.generate_files_action.triggered.connect(self.export_reviews)
            # R3: as variantes "Exportar este arquivo..."/"Exportar
            # selecionados..." morreram — eram tres rotulos para a MESMA
            # funcao (o escopo sempre foi escolhido dentro do dialogo).

            self.delete_transcription_action = QAction("Apagar transcrição… (a gravação fica)", self)
            self.delete_transcription_action.setToolTip("Apaga apenas os arquivos de transcrição gerados. A gravação original fica no projeto.")
            self.delete_transcription_action.triggered.connect(self.delete_selected_transcriptions)

            self.rename_interview_action = QAction("Renomear rótulo…", self)
            self.rename_interview_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
            self.rename_interview_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.rename_interview_action.triggered.connect(self.rename_selected_interview)

            self.move_up_action = QAction("Mover arquivo para cima", self)
            self.move_up_action.setShortcut(QKeySequence("Ctrl+Alt+Up"))
            self.move_up_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.move_up_action.triggered.connect(self.move_selected_up)

            self.move_down_action = QAction("Mover arquivo para baixo", self)
            self.move_down_action.setShortcut(QKeySequence("Ctrl+Alt+Down"))
            self.move_down_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.move_down_action.triggered.connect(self.move_selected_down)

            for _reorder_action in (self.rename_interview_action, self.move_up_action, self.move_down_action):
                _reorder_action.setShortcutVisibleInContextMenu(True)

            self.trash_selected_action = QAction("Enviar para a Lixeira…", self)
            self.trash_selected_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
            # ApplicationShortcut: Del dispara de qualquer lugar; effective_target_ids trata selecao.
            self.trash_selected_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_selected_action.setToolTip(
                "Enviar os arquivos SELECIONADOS na lista (destaque) para a Lixeira do projeto.\n"
                "As caixas de marcação não contam — elas escolhem o que transcrever.\n"
                "Reversível com Ctrl+Z nesta sessão. (Del)")
            self.trash_selected_action.triggered.connect(self.trash_selected_interviews)

            self.trash_undo_action = QAction("Desfazer exclusão", self)
            self.trash_undo_action.setShortcut(QKeySequence("Ctrl+Z"))
            # ApplicationShortcut + guard em undo_last_trash delega ao editor quando foco e QTextEdit
            self.trash_undo_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_undo_action.setToolTip("Desfaz a última exclusão desta sessão (Ctrl+Z).")
            self.trash_undo_action.triggered.connect(self.undo_last_trash)

            self.trash_redo_action = QAction("Refazer exclusão", self)
            self.trash_redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
            self.trash_redo_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_redo_action.setToolTip("Refaz a última exclusão desfeita (Ctrl+Shift+Z).")
            self.trash_redo_action.triggered.connect(self.redo_last_trash)

            for _trash_action in (self.trash_selected_action, self.trash_undo_action, self.trash_redo_action):
                _trash_action.setShortcutVisibleInContextMenu(True)


            self.close_open_file_action = QAction("Fechar arquivo aberto", self)
            self.close_open_file_action.setToolTip("Fechar o arquivo aberto e voltar à lista de entrevistas.")
            self.close_open_file_action.triggered.connect(self.close_open_file)

            self.open_export_folder_action = QAction("Abrir pasta Resultados", self)
            self.open_export_folder_action.setToolTip("Abrir a pasta Resultados do projeto (DOCX, Markdown, legendas) no Explorador.")
            self.open_export_folder_action.triggered.connect(self.open_export_folder)

            # R3: "Reprocessar falantes" saiu do menu — virou a oferta
            # contextual na lista ("N entrevistas sem separação de vozes —
            # Separar agora"), que aparece exatamente quando ha o que
            # preencher (instalado => aplicado). O fluxo run_diarization_job
            # continua sendo o executor.

            self.improve_speakers_action = QAction("Refazer separação de falantes…", self)
            self.improve_speakers_action.setToolTip(
                "Refaz a separação de vozes e recria a transcrição do zero.\n"
                "Suas edições serão descartadas — guardamos uma cópia em "
                "Documentos › Versões anteriores."
            )
            self.improve_speakers_action.triggered.connect(self.improve_speakers_current_file)

            self.name_voices_action = QAction("Dar nome às vozes…", self)
            self.name_voices_action.setToolTip("Ouvir uma amostra de cada voz da transcrição aberta e dar nome aos falantes.\nAbra uma transcrição primeiro.")
            self.name_voices_action.triggered.connect(self.open_voice_naming_dialog)

            self.voice_prompt_action = QAction("Perguntar de quem é cada voz ao abrir transcrições", self)
            self.voice_prompt_action.setCheckable(True)
            self.voice_prompt_action.setChecked(True)
            self.voice_prompt_action.setToolTip(
                "Quando ligado, transcrições ainda não confirmadas abrem com a pergunta \"De quem é esta voz?\".\n"
                "Desligue para revisar em lote sem interrupções (vale para este projeto)."
            )
            self.voice_prompt_action.toggled.connect(self._on_voice_prompt_toggled)

            self.find_action = QAction("Buscar neste arquivo", self)
            self.find_action.setShortcut(QKeySequence("Ctrl+F"))
            self.find_action.setToolTip("Filtra os blocos da transcrição aberta pelo termo digitado.")
            self.find_action.triggered.connect(self.show_find_bar)

            self.project_search_action = QAction("Buscar palavras…", self)
            self.project_search_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
            self.project_search_action.setToolTip(
                "Busca palavras e expressões exatas nas transcrições do projeto.\n"
                "Lê o texto transcrito (revisado, quando houver), nunca o áudio;\n"
                "na janela, dá para restringir a entrevista aberta ou escolher quais entram.")
            self.project_search_action.triggered.connect(lambda: self.open_word_search())

            # O nome diz primeiro o que a função entrega SEMPRE (os trechos, em
            # qualquer computador) e só depois o que depende da máquina (a
            # resposta escrita). "Perguntar às entrevistas com AI" prometia a
            # resposta a todo mundo — e a maioria dos computadores não tem
            # placa NVIDIA. Relato de campo 2026-09-04: "continua não
            # entregando o que promete; temos que mudar como nomeamos,
            # explicamos e propagandeamos".
            self.explore_action = QAction("✨ Buscar por sentido e perguntar…", self)
            self.explore_action.setToolTip(
                "Escreva uma pergunta ou um tema e receba os trechos das entrevistas que\n"
                "tratam disso — pelo sentido, mesmo sem as palavras exatas. Isso funciona\n"
                "em qualquer computador.\n"
                "A resposta escrita, citando os trechos, precisa de placa NVIDIA; sem ela,\n"
                "ficam os trechos.\n"
                "Lê as transcrições (não o áudio); o escopo é escolhível na janela:\n"
                "todas, somente a entrevista aberta ou um conjunto escolhido.\n"
                "AI local — nada sai do seu computador.")
            self.explore_action.triggered.connect(self.open_explore)

            self.themes_action = QAction("✨ Temas das entrevistas…", self)
            self.themes_action.setToolTip(
                "Agrupa os trechos de todas as entrevistas por semelhança de sentido e lista\n"
                "os temas mais tratados, com todos os trechos de cada um. Dá para renomear,\n"
                "juntar, aplicar códigos (um ou mais por trecho) e exportar para o QualiLab\n"
                "(.qualilab) ou CSV. Lê as transcrições (não o áudio); o escopo é escolhível\n"
                "na janela. AI local — nada sai do seu computador.")
            self.themes_action.triggered.connect(self.open_themes)

            self.summarize_action = QAction("✨ Resumir a entrevista com AI", self)
            self.summarize_action.setToolTip(
                "Gera um resumo com indice tematico da entrevista aberta — ou de\n"
                "cada entrevista marcada ☑ na lista (requer placa NVIDIA).\n"
                "Sai em 05_transcripts_review/final/md/ e em Resultados/.\n"
                "AI local — nada sai do seu computador.")
            self.summarize_action.triggered.connect(self.run_summarize_job)

            self.glossario_action = QAction("✨ Glossário de nomes com AI", self)
            self.glossario_action.setToolTip(
                "Lê as transcrições do projeto e monta um glossário de pessoas, lugares\n"
                "e instituições citados, juntando as variações de grafia do mesmo nome\n"
                "(por exemplo IBGE escrito como BGA). Nada é alterado nas transcrições.\n"
                "AI local — nada sai do seu computador.")
            self.glossario_action.triggered.connect(self.run_glossario_job)

            self.spelling_action = QAction("✨ Revisar grafias de nomes…", self)
            self.spelling_action.setToolTip(
                "Mostra os nomes que aparecem escritos de formas diferentes e deixa\n"
                "você corrigir ocorrência por ocorrência, com o trecho à vista.\n"
                "Exige o glossário gerado antes; o áudio e a transcrição original\n"
                "não são alterados, e Ctrl+Z desfaz na entrevista aberta.")
            self.spelling_action.triggered.connect(self.open_spelling_review)

            # R3: "Atualizar transcricao editavel" (nome que nao ajudava)
            # saiu da UI — a remontagem ja roda automaticamente ao fim de
            # todos os fluxos que mexem nos dados brutos; o reparo
            # excepcional ganhara casa em Documentos > Versões anteriores.

            self.qc_action = QAction("Verificar exportações", self)
            self.qc_action.setToolTip("Verificar a qualidade das transcrições geradas (integridade e consistência).")
            self.qc_action.triggered.connect(self.run_qc_job)

            # R3: "Creditos" fundiu aqui — os dois abriam o mesmo dialogo.
            self.about_action = QAction("Sobre o Transcritório", self)
            self.about_action.setToolTip("Informações sobre o Transcritório: versão e créditos.")
            self.about_action.triggered.connect(self.show_about)

            self.documentation_action = QAction("Documentação", self)
            self.documentation_action.setToolTip("Abrir a documentação do projeto, se disponível.")
            self.documentation_action.triggered.connect(self.show_documentation)

            self.workflow_help_action = QAction("Fluxo de trabalho", self)
            self.workflow_help_action.setToolTip("Ver o passo a passo básico do Transcritório.")
            self.workflow_help_action.triggered.connect(self.show_workflow_help)

            self.cancel_job_action = QAction("Cancelar", self)
            self.cancel_job_action.setToolTip("Cancela o processamento atual. O motor é interrompido; outras etapas param no próximo ponto seguro.")
            self.cancel_job_action.triggered.connect(self.cancel_current_job)

            self.undo_action = self.undo_stack.createUndoAction(self, "Desfazer")
            self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
            # WidgetWithChildrenShortcut + addAction no text_edit (feito apos criacao do editor)
            # evita conflito com trash_undo_action (ApplicationShortcut): Qt prefere o contexto mais especifico.
            self.undo_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.redo_action = self.undo_stack.createRedoAction(self, "Refazer")
            self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
            self.redo_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

            # Playback keyboard shortcuts
            self.play_action = QAction("Reproduzir/Pausar", self)
            self.play_action.setShortcut(Qt.Key.Key_Space)
            self.play_action.triggered.connect(self.toggle_playback)
            self.addAction(self.play_action)

            self.seek_back_action = QAction("Voltar 5 segundos", self)
            self.seek_back_action.setShortcut(QKeySequence("Ctrl+Left"))
            self.seek_back_action.triggered.connect(lambda: self.seek_relative(-5))
            self.addAction(self.seek_back_action)

            self.seek_forward_action = QAction("Avançar 5 segundos", self)
            self.seek_forward_action.setShortcut(QKeySequence("Ctrl+Right"))
            self.seek_forward_action.triggered.connect(lambda: self.seek_relative(5))
            self.addAction(self.seek_forward_action)

            # --- Atalhos do Estúdio (2026-09-04) ---------------------------
            # Uma hora de entrevista tem ~219 blocos, e cada um custava idas ao
            # mouse: ouvir, voltar, corrigir, juntar, seguir. O problema é que
            # com o cursor DENTRO do texto o QTextEdit consome Espaço, Ctrl+←
            # e Ctrl+→ (sonda empírica) — os três atalhos de player que já
            # existiam nunca chegavam a quem estava digitando. A família Alt e
            # as teclas F sobrevivem: daí a segunda tecla nas ações do player e
            # o conjunto abaixo, todo ele utilizável sem tirar a mão do texto.
            self.play_action.setText("Reproduzir/Pausar")
            self.play_action.setShortcuts([QKeySequence(Qt.Key.Key_Space), QKeySequence("F4")])
            self.play_action.setToolTip("Reproduz ou pausa. (Espaço; F4 também funciona com o cursor no texto)")
            self.seek_back_action.setShortcuts([QKeySequence("Ctrl+Left"), QKeySequence("Alt+Left")])
            self.seek_back_action.setToolTip("Volta 5 segundos. (Ctrl+← ; Alt+← com o cursor no texto)")
            self.seek_forward_action.setShortcuts([QKeySequence("Ctrl+Right"), QKeySequence("Alt+Right")])
            self.seek_forward_action.setToolTip("Avança 5 segundos. (Ctrl+→ ; Alt+→ com o cursor no texto)")

            self.seek_back_short_action = QAction("Voltar 2 segundos", self)
            self.seek_back_short_action.setShortcut(QKeySequence("Alt+Shift+Left"))
            self.seek_back_short_action.setToolTip("Volta 2 segundos — o passo curto de quem está conferindo uma palavra. (Alt+Shift+←)")
            self.seek_back_short_action.triggered.connect(lambda: self.seek_relative(-2))
            self.addAction(self.seek_back_short_action)

            self.seek_forward_short_action = QAction("Avançar 2 segundos", self)
            self.seek_forward_short_action.setShortcut(QKeySequence("Alt+Shift+Right"))
            self.seek_forward_short_action.setToolTip("Avança 2 segundos. (Alt+Shift+→)")
            self.seek_forward_short_action.triggered.connect(lambda: self.seek_relative(2))
            self.addAction(self.seek_forward_short_action)

            self.repeat_turn_action = QAction("Repetir o bloco", self)
            self.repeat_turn_action.setShortcut(QKeySequence("F3"))
            self.repeat_turn_action.setToolTip("Volta ao início do bloco atual e reproduz de novo. (F3)")
            self.repeat_turn_action.triggered.connect(self.repeat_current_turn)

            self.speed_down_action = QAction("Reproduzir mais devagar", self)
            self.speed_down_action.setShortcut(QKeySequence("F7"))
            self.speed_down_action.setToolTip("Um passo mais devagar na velocidade de reprodução. (F7)")
            self.speed_down_action.triggered.connect(lambda: self._step_playback_speed(-1))
            self.addAction(self.speed_down_action)

            self.speed_up_action = QAction("Reproduzir mais rápido", self)
            self.speed_up_action.setShortcut(QKeySequence("F8"))
            self.speed_up_action.setToolTip("Um passo mais rápido na velocidade de reprodução. (F8)")
            self.speed_up_action.triggered.connect(lambda: self._step_playback_speed(1))
            self.addAction(self.speed_up_action)

            self.next_block_action = QAction("Bloco seguinte", self)
            self.next_block_action.setShortcut(QKeySequence("Alt+Down"))
            self.next_block_action.setToolTip("Vai para o próximo bloco, leva o áudio junto e deixa o cursor no texto. (Alt+↓)")
            self.next_block_action.triggered.connect(lambda: self._step_block(1))

            self.prev_block_action = QAction("Bloco anterior", self)
            self.prev_block_action.setShortcut(QKeySequence("Alt+Up"))
            self.prev_block_action.setToolTip("Volta ao bloco anterior, leva o áudio junto e deixa o cursor no texto. (Alt+↑)")
            self.prev_block_action.triggered.connect(lambda: self._step_block(-1))

            self.next_flagged_action = QAction("Próximo bloco marcado", self)
            self.next_flagged_action.setShortcut(QKeySequence("Alt+Shift+Down"))
            self.next_flagged_action.setToolTip("Pula para o próximo bloco marcado para conferir. (Alt+Shift+↓)")
            self.next_flagged_action.triggered.connect(lambda: self._step_flagged(1))

            self.prev_flagged_action = QAction("Bloco marcado anterior", self)
            self.prev_flagged_action.setShortcut(QKeySequence("Alt+Shift+Up"))
            self.prev_flagged_action.setToolTip("Volta ao bloco marcado anterior. (Alt+Shift+↑)")
            self.prev_flagged_action.triggered.connect(lambda: self._step_flagged(-1))

            self.focus_toggle_action = QAction("Alternar foco: lista de blocos / texto", self)
            self.focus_toggle_action.setShortcut(QKeySequence("F6"))
            self.focus_toggle_action.setToolTip("Passa o foco do texto para a lista de blocos e de volta. (F6)")
            self.focus_toggle_action.triggered.connect(self._toggle_studio_focus)

            self.merge_prev_block_action = QAction("Juntar com anterior", self)
            self.merge_prev_block_action.setShortcut(QKeySequence("Alt+Shift+J"))
            self.merge_prev_block_action.setToolTip(
                "Junta este bloco ao bloco de cima quando os falantes forem iguais. (Alt+Shift+J)")
            self.merge_prev_block_action.triggered.connect(self.merge_current_turn_with_previous)

            self.merge_block_action = QAction("Juntar com próximo", self)
            self.merge_block_action.setShortcut(QKeySequence("Alt+J"))
            self.merge_block_action.setToolTip(
                "Junta este bloco ao bloco seguinte quando os falantes forem iguais. (Alt+J)")
            self.merge_block_action.triggered.connect(self.merge_current_turn)

            self.split_block_action = QAction("Dividir bloco", self)
            self.split_block_action.setShortcut(QKeySequence("Alt+D"))
            self.split_block_action.setToolTip(
                "Divide o bloco pelo cursor de edição na onda, pela posição do player\n"
                "ou no tempo exato da palavra sob o cursor do texto. (Alt+D)")
            self.split_block_action.triggered.connect(self.split_current_turn)

        def action_button(self, action: QAction, primary: bool = False) -> QPushButton:
            button = QPushButton(action.text())
            button.setToolTip(action.toolTip())
            button.setEnabled(action.isEnabled())
            # Ancora a acao no botao (nao exibe nada): o botao vira a
            # "casa" declarada da acao — e o smoke_nav_ui detecta acoes
            # sem casa varrendo widget.actions() (Programa R, R0).
            button.addAction(action)
            button.clicked.connect(lambda _checked=False, item=action: item.trigger())
            # O botao precisa SEGUIR a acao: QAction.trigger() dispara mesmo
            # com a acao desabilitada, entao um botao que nao espelha o
            # enabled contorna os gates do update_action_states. Os botoes
            # criados aqui vivem tanto quanto a janela (lambda segura o
            # botao sem risco de wrapper morto).
            action.changed.connect(
                lambda item=action, b=button: (
                    b.setEnabled(item.isEnabled()),
                    b.setToolTip(item.toolTip()),
                ))
            if primary:
                button.setDefault(True)
                button.setStyleSheet("font-weight: 700;")
            return button

        _MEDIA_BUTTON_PRIMARY_QSS = (
            f"QPushButton {{ background: {ui_tokens.ACCENT}; color: {ui_tokens.ON_ACCENT}; "
            "font-weight: 700; font-size: 14px; padding: 9px 18px; "
            f"border-radius: 6px; border: 1px solid {ui_tokens.ACCENT}; }} "
            f"QPushButton:hover {{ background: {ui_tokens.ACCENT_HOVER}; "
            f"border-color: {ui_tokens.ACCENT_HOVER}; }} "
            "QPushButton::menu-indicator { subcontrol-position: right center; "
            "subcontrol-origin: padding; right: 6px; }"
        )
        _MEDIA_BUTTON_GHOST_QSS = ""
        _TRANSCREVER_PRIMARY_QSS = (
            f"QToolButton {{ background: {ui_tokens.ACCENT}; color: {ui_tokens.ON_ACCENT}; "
            "font-weight: 700; font-size: 14px; padding: 6px 14px; "
            f"border-radius: 6px; border: 1px solid {ui_tokens.ACCENT}; }} "
            f"QToolButton:hover {{ background: {ui_tokens.ACCENT_HOVER}; "
            f"border-color: {ui_tokens.ACCENT_HOVER}; }}"
        )
        _TRANSCREVER_GHOST_QSS = "font-weight: 700;"

        def media_button(self) -> QPushButton:
            button = QPushButton("+ Adicionar mídia…")
            button.setToolTip(
                "Adicionar arquivos de áudio/vídeo ao projeto.\n"
                "Também pode arrastar arquivos do Explorer/Finder para a janela."
            )
            menu = QMenu(button)
            menu.addAction(self.add_files_action)
            menu.addAction(self.add_folder_action)
            button.setMenu(menu)
            self._media_button_ref = button
            return button

        def _update_add_media_emphasis(self, has_rows: bool) -> None:
            """Enfase caminha com a jornada (dossie RD): sem midia, Adicionar
            e primary; midia TODA pendente (R4), Transcrever e primary —
            fecha o beco pos-adicao em que a unica pista era o bold; com o
            projeto andando, toolbar neutra (o proximo passo mora na lista).
            """
            button = getattr(self, "_media_button_ref", None)
            if button is not None:
                button.setStyleSheet(
                    self._MEDIA_BUTTON_PRIMARY_QSS if not has_rows
                    else self._MEDIA_BUTTON_GHOST_QSS
                )
            transcrever = getattr(self, "transcribe_button", None)
            if transcrever is not None:
                tudo_pendente = has_rows and all(
                    not (s.review_exists or s.canonical_exists)
                    for s in self.statuses)
                transcrever.setStyleSheet(
                    self._TRANSCREVER_PRIMARY_QSS if tudo_pendente
                    else self._TRANSCREVER_GHOST_QSS)

        def transcribe_menu_button(self) -> QToolButton:
            # R3: clique direto AGE (☑ marcadas; sem nenhuma ☑, todas as
            # pendentes); a setinha abre as variantes e a chave de
            # separacao de falantes que morava solta na toolbar.
            button = QToolButton()
            button.setText("Transcrever")
            button.setToolTip(
                "Transcrever as entrevistas marcadas com ☑.\n"
                "Sem nenhuma marcada, transcreve todas as pendentes.")
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setStyleSheet("font-weight: 700;")
            button.clicked.connect(self._on_transcribe_button_clicked)
            menu = QMenu(button)
            menu.addAction(self.transcribe_action)
            menu.addAction(self.transcribe_pending_action)
            menu.addSeparator()
            menu.addAction(self.diarize_toggle_action)
            button.setMenu(menu)
            self.transcribe_button = button
            return button

        def _on_transcribe_button_clicked(self) -> None:
            """Clique direto no botao Transcrever (R3): ☑ marcadas; sem
            nenhuma marcada, todas as pendentes."""
            if not self._require_project("Transcrever"):
                return
            marcadas = [s.interview_id for s in self.statuses
                        if s.interview_id in self._checked_ids]
            if marcadas:
                self.run_full_transcription_job(ids=marcadas)
                return
            self.run_pending_transcription_job()

        def _build_menus(self) -> None:
            # Estrutura da reforma (Programa R, dossie RD aprovado
            # 2026-08-31): 6 menus — Projeto / Editar / Entrevista /
            # Analisar / Ferramentas / Ajuda. O menu e o CATALOGO
            # completo; o lugar primario de cada comando e contextual.
            # Rotulos seguem os atuais ate a R3 consolidar as familias
            # (exportar x3, trio de falantes, orfas).

            # --- Projeto: ciclo de vida e resultados do projeto ---
            projeto_menu = self.menuBar().addMenu("Projeto")
            projeto_menu.addAction(self.new_project_action)
            projeto_menu.addAction(self.open_project_action)
            recent_menu = projeto_menu.addMenu("Projetos recentes")
            from . import recent_projects
            for rp in recent_projects.load_recent()[:5]:
                recent_menu.addAction(str(rp), lambda p=rp: self._open_project_path(p))
            if self.context is not None:
                recent_menu.addSeparator()
                recent_menu.addAction(str(self.context.paths.project_root), self.refresh_interviews)
            projeto_menu.addSeparator()
            add_media_menu = projeto_menu.addMenu("Adicionar mídia")
            add_media_menu.addAction(self.add_files_action)
            add_media_menu.addAction(self.add_folder_action)
            projeto_menu.addAction(self.reload_list_action)
            projeto_menu.addSeparator()
            projeto_menu.addAction(self.generate_files_action)
            projeto_menu.addSeparator()
            projeto_menu.addAction(self.open_project_folder_action)
            projeto_menu.addAction(self.open_export_folder_action)
            projeto_menu.addSeparator()
            projeto_menu.addAction(self.exit_action)

            # --- Editar: desfazer/refazer e busca no aberto ---
            editar_menu = self.menuBar().addMenu("Editar")
            editar_menu.addAction(self.undo_action)
            editar_menu.addAction(self.redo_action)
            editar_menu.addSeparator()
            editar_menu.addAction(self.find_action)
            editar_menu.addSeparator()
            # Submenu "Bloco e reprodução": é aqui que os atalhos do Estúdio
            # ficam VISÍVEIS. Atalho que ninguém descobre não economiza clique.
            bloco_menu = editar_menu.addMenu("Bloco e reprodução")
            bloco_menu.addAction(self.play_action)
            bloco_menu.addAction(self.repeat_turn_action)
            bloco_menu.addAction(self.seek_back_action)
            bloco_menu.addAction(self.seek_forward_action)
            bloco_menu.addAction(self.seek_back_short_action)
            bloco_menu.addAction(self.seek_forward_short_action)
            bloco_menu.addAction(self.speed_down_action)
            bloco_menu.addAction(self.speed_up_action)
            bloco_menu.addSeparator()
            bloco_menu.addAction(self.prev_block_action)
            bloco_menu.addAction(self.next_block_action)
            bloco_menu.addAction(self.prev_flagged_action)
            bloco_menu.addAction(self.next_flagged_action)
            bloco_menu.addAction(self.focus_toggle_action)
            bloco_menu.addSeparator()
            bloco_menu.addAction(self.merge_prev_block_action)
            bloco_menu.addAction(self.merge_block_action)
            bloco_menu.addAction(self.split_block_action)

            # --- Entrevista: tudo sobre a entrevista (abrir, transcrever,
            # falantes, propriedades, lista, destrutivas) ---
            entrevista_menu = self.menuBar().addMenu("Entrevista")
            entrevista_menu.addAction(self.open_transcript_action)
            entrevista_menu.addAction(self.save_action)
            entrevista_menu.addAction(self.close_open_file_action)
            entrevista_menu.addSeparator()
            entrevista_menu.addAction(self.transcribe_action)
            entrevista_menu.addAction(self.transcribe_pending_action)
            entrevista_menu.addAction(self.transcribe_current_action)
            entrevista_menu.addAction(self.retranscribe_current_action)
            entrevista_menu.addAction(self.diarize_toggle_action)
            entrevista_menu.addSeparator()
            entrevista_menu.addAction(self.name_voices_action)
            entrevista_menu.addAction(self.improve_speakers_action)
            entrevista_menu.addSeparator()
            entrevista_menu.addAction(self.apply_metadata_action)
            entrevista_menu.addAction(self.rename_interview_action)
            entrevista_menu.addAction(self.move_up_action)
            entrevista_menu.addAction(self.move_down_action)
            entrevista_menu.addSeparator()
            entrevista_menu.addAction(self.delete_transcription_action)
            entrevista_menu.addAction(self.trash_selected_action)
            entrevista_menu.addAction(self.trash_undo_action)
            entrevista_menu.addAction(self.trash_redo_action)

            # --- Analisar: busca no projeto e ✨ AI assistiva ---
            analisar_menu = self.menuBar().addMenu("Analisar")
            # Linha explicativa durante um lote (2026-09-02): item cinza nao
            # mostra tooltip, e o usuario concluia que a AI "nao veio instalada".
            self.busy_menu_hint_action = QAction("", self)
            self.busy_menu_hint_action.setEnabled(False)
            self.busy_menu_hint_action.setVisible(False)
            analisar_menu.addAction(self.busy_menu_hint_action)
            # Separador como QAction com pai (o de addSeparator() pertence ao
            # menu e o wrapper Python pode morrer antes — smoke_nav_ui).
            self.busy_menu_hint_separator = QAction(self)
            self.busy_menu_hint_separator.setSeparator(True)
            self.busy_menu_hint_separator.setVisible(False)
            analisar_menu.addAction(self.busy_menu_hint_separator)
            analisar_menu.addAction(self.project_search_action)
            analisar_menu.addAction(self.explore_action)
            analisar_menu.addAction(self.themes_action)
            analisar_menu.addSeparator()
            analisar_menu.addAction(self.summarize_action)
            analisar_menu.addAction(self.glossario_action)
            analisar_menu.addAction(self.spelling_action)

            # --- Ferramentas: fila, verificacao, motor e modelos ---
            ferramentas_menu = self.menuBar().addMenu("Ferramentas")
            ferramentas_menu.addAction(self.queue_action)
            ferramentas_menu.addAction(self.qc_action)
            ferramentas_menu.addSeparator()
            ferramentas_menu.addAction(self.engine_settings_action)
            ferramentas_menu.addAction(self.model_manager_action)
            from . import install_tools as _install_tools
            if not _install_tools.is_frozen():
                # Canal uv/PyPI (v0.2): aceleracao NVIDIA e um extra opcional
                # instalado por fora (o app fechado); o bundle legado usa o
                # fluxo antigo do cuda_pack.
                ferramentas_menu.addAction(
                    "Instalar aceleração NVIDIA (CUDA)…", self.show_cuda_uv_install_dialog
                )
            ferramentas_menu.addSeparator()
            ferramentas_menu.addAction(self.voice_prompt_action)
            # Cancelar saiu do menu: mora na statusbar, junto do progresso.

            # --- Ajuda ---
            ajuda_menu = self.menuBar().addMenu("Ajuda")
            ajuda_menu.addAction(self.documentation_action)
            ajuda_menu.addAction(self.workflow_help_action)
            ajuda_menu.addSeparator()
            if not _install_tools.is_frozen():
                ajuda_menu.addAction("Verificar atualizações…", self.show_upgrade_dialog)
                ajuda_menu.addAction("Reparar instalação…", self.show_repair_dialog)
                ajuda_menu.addSeparator()
            ajuda_menu.addAction(self.about_action)

        def _show_uv_command_dialog(self, title: str, intro: str, command: str) -> None:
            """Dialog padrao do canal uv/PyPI: mostra o comando a rodar com o
            app FECHADO (o uv nao consegue reinstalar um ambiente em uso)."""
            from . import install_tools as _it
            body = (
                f"{intro}\n\n"
                "1. Feche o Transcritório.\n"
                "2. Abra o Prompt de Comando (ou PowerShell).\n"
                "3. Cole e execute:\n\n"
                f"    {command}\n\n"
                "4. Abra o Transcritório novamente."
            )
            if _it.find_uv() is None:
                body += (
                    "\n\nAtenção: o programa 'uv' não foi encontrado neste computador. "
                    "Instale antes com:\n\n    winget install astral-sh.uv"
                )
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle(title)
            msg.setText(body)
            copy_btn = msg.addButton("Copiar comando", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == copy_btn:
                QApplication.clipboard().setText(command)
                self.progress_label.setText("Comando copiado. Feche o Transcritório e execute no terminal.")

        def show_upgrade_dialog(self, *_args: Any) -> None:
            from . import install_tools as _it
            self._show_uv_command_dialog(
                "Verificar atualizações",
                "Para atualizar o Transcritório para a versão mais recente:",
                _it.upgrade_command(),
            )

        def show_repair_dialog(self, *_args: Any) -> None:
            from . import install_tools as _it
            from . import runtime as _rt
            # Reparo preserva a aceleracao NVIDIA se ela esta instalada
            # (flag persistido OU torch atual e build CUDA).
            with_cuda = _it.cuda_extra_installed() or _rt.cuda_libs_present()
            self._show_uv_command_dialog(
                "Reparar instalação",
                "Isto reconstrói o ambiente técnico do Transcritório.\n"
                "Seus projetos, áudios, transcrições e modelos NÃO são afetados.",
                _it.repair_command(cuda=with_cuda),
            )

        def show_cuda_uv_install_dialog(self, *_args: Any) -> None:
            from . import install_tools as _it
            from . import runtime as _rt
            if _rt.cuda_libs_present():
                QMessageBox.information(
                    self,
                    "Aceleração NVIDIA",
                    "A aceleração NVIDIA já está instalada neste computador.\n"
                    "Escolha o dispositivo em Ferramentas → Configurar transcrição…",
                )
                _it.mark_cuda_extra_installed(True)
                return
            from . import capabilities as _caps
            hw = _caps.hardware_snapshot()
            if not hw.has_gpu:
                QMessageBox.information(
                    self,
                    "Aceleração NVIDIA",
                    "Nenhuma placa NVIDIA compatível foi encontrada neste computador.\n"
                    "O Transcritório continua funcionando normalmente em CPU.",
                )
                return
            detalhe = (f"Placa NVIDIA com {hw.vram_gb:.0f} GB de memória de vídeo detectada."
                       if hw.vram_gb else "Placa NVIDIA detectada.")
            aviso = ""
            if hw.vram_gb is not None and hw.vram_gb < 4:
                aviso = ("\nAtenção: com essa memória de vídeo, a aceleração pode não "
                         "valer a pena com os modelos maiores — o modo CPU continua "
                         "disponível.")
            self._show_uv_command_dialog(
                "Instalar aceleração NVIDIA",
                f"{detalhe} A aceleração torna a transcrição 3-9x mais rápida.\n"
                "O download é grande (~2,5 GB) e o Transcritório continua funcionando em CPU\n"
                "caso algo dê errado — a instalação não altera seus projetos." + aviso,
                _it.cuda_install_command(),
            )
            # O flag cuda_extra_installed so e marcado quando a instalacao e
            # CONFIRMADA (cuda_libs_present no proximo start) — mostrar o
            # dialog nao significa que o usuario rodou o comando.

        def show_workflow_help(self) -> None:
            QMessageBox.information(
                self,
                "Fluxo de trabalho",
                "O caminho básico: + Adicionar mídia… → Transcrever → abrir a entrevista (duplo clique) → revisar o texto → Salvar transcrição → Exportar…",
            )

        def show_about(self) -> None:
            from . import __build__, __version__
            # No canal oficial (uv/PyPI) o wheel publica __build__ == "dev";
            # rotular a release como "versão de desenvolvimento" era
            # factualmente errado (auditoria 2026-09-01). Instalado em
            # site-packages = release; fora dele = rodando da fonte.
            versao = __version__
            if __build__ != "dev":
                build_info = f"Build: {__build__}"
            elif "site-packages" in str(Path(__file__).resolve()):
                try:
                    from importlib.metadata import version as _pkg_version
                    versao = _pkg_version("transcritorio")
                except Exception:  # noqa: BLE001
                    pass
                build_info = "Instalação oficial (uv/PyPI)"
            else:
                build_info = "Versão de desenvolvimento (fonte)"
            QMessageBox.information(
                self,
                f"Sobre {APP_NAME}",
                f"{APP_NAME} v{versao}\n\n{build_info}\n\nCréditos: {APP_CREDITS}\n\nTranscrição local com WhisperX e pyannote.",
            )

        def show_documentation(self) -> None:
            if self.context is not None:
                docs = [self.context.paths.project_root / "README_transcricoes.md"]
                existing = [str(path) for path in docs if path.exists()]
            else:
                existing = []
            QMessageBox.information(
                self,
                "Documentacao",
                "\n".join(existing) if existing else "A documentacao do projeto nao foi encontrada nesta pasta.",
            )

        def show_queue(self) -> None:
            if not self._require_project("Fila de processamento"):
                return
            self.context = app_service.load_project(self.context.config_path)
            dialog = JobsDialog(self.context, self)
            dialog.exec()

        def configure_engine(self) -> None:
            if not self._require_project("Configuracao do motor"):
                return
            dialog = EngineSettingsDialog(self.context.config, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                self.context = app_service.update_engine_config(self.context, dialog.updates())
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível salvar a configuração", sanitize_message(str(exc)))
                return
            self.refresh_interviews()
            self._sync_diarize_toggle()
            self.progress_label.setText("Configuração de transcrição atualizada.")

        def _on_diarize_toggled(self, checked: bool) -> None:
            if self.context is None:
                return
            try:
                self.context = app_service.update_engine_config(self.context, {"diarize": checked})
            except Exception as exc:
                _logger.warning("update_engine_config(diarize) falhou: %s", exc)
                return
            if not checked:
                return
            # Honestidade ao MARCAR: numa instalacao sem o modelo de
            # falantes, a exigencia de conta/token do Hugging Face
            # aparecia so no clique em Transcrever — surpresa que o
            # perfil Essencial prometia dispensar.
            try:
                estado, _motivo, _gb = self._capability_state("separar_falantes")
            except Exception:  # noqa: BLE001 - sonda nunca bloqueia o toggle
                return
            if estado == "pronta":
                return
            box = QMessageBox(self)
            box.setWindowTitle("Separar quem fala")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                "A separação de falantes usa um modelo adicional (~0,1 GB) que "
                "ainda não está neste computador. Ele é gratuito, mas o download "
                "exige uma conta no Hugging Face e o aceite dos termos do modelo.\n\n"
                "Preparar agora? (Se deixar para depois, o aplicativo pedirá isso "
                "na hora de transcrever.)")
            preparar = box.addButton("Preparar agora", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Deixar para depois", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is preparar:
                self.show_model_setup()

        def _sync_diarize_toggle(self) -> None:
            if not hasattr(self, "diarize_toggle_action"):
                return
            from . import app_settings as _settings
            # A chave mostra o estado EFETIVO: com "auto", segue a
            # instalacao do modelo no momento (tri-state 2026-08-31).
            if self.context:
                efetivo = app_service.diarize_effective(self.context.config or {})[0]
            else:
                efetivo = app_service.diarize_effective(
                    {"diarize": _settings.diarize_default()})[0]
            self.diarize_toggle_action.blockSignals(True)
            self.diarize_toggle_action.setChecked(efetivo)
            self.diarize_toggle_action.blockSignals(False)

        def _maybe_offer_parakeet_gpu(self) -> None:
            """Oferta unica da aceleracao GPU do Parakeet, na primeira
            transcricao com o motor em maquina apta sem o pacote.

            Nao bloqueia o job: qualquer resposta segue transcrevendo
            (a diferenca e so CPU vs GPU). VRAM < 6 GB nao recebe a
            oferta proativa (a linha do gerenciador continua la, com o
            aviso por conta e risco)."""
            if sys.platform != "win32" or getattr(sys, "frozen", False):
                return
            if getattr(self, "_parakeet_gpu_prompted", False):
                return
            self._parakeet_gpu_prompted = True
            from . import onnx_env as _onnx_env, runtime as _runtime
            flag = _runtime.app_data_dir() / "parakeet_gpu_prompt_dismissed.flag"
            if _onnx_env.onnx_env_ready() or not _runtime.cuda_libs_present():
                return
            # Pacote instalado para OUTRO Python (2026-09-02): o worker falhava
            # e o TAGARELA caia para a CPU calado. Reinstalar vale a pergunta
            # mesmo para quem recusou a oferta original.
            motivo = _onnx_env.onnx_env_stale_reason() if _onnx_env.onnx_env_dir().exists() else ""
            if flag.exists() and not motivo:
                return
            from . import capabilities as _caps
            hw = _caps.hardware_snapshot()
            if not hw.has_gpu:
                return
            if hw.vram_gb is not None and hw.vram_gb < 6.0 and not motivo:
                return
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            if motivo:
                box.setWindowTitle("Reinstalar a aceleração do Parakeet na GPU?")
                box.setText(
                    f"A aceleração GPU do motor Parakeet parou de funcionar: {motivo}. "
                    "Sem ela, a transcrição está saindo no processador — cerca de 4x "
                    "mais devagar.\n\n"
                    "Reinstalar a aceleração agora (uma vez, ~0,3 GB)?")
                instalar = box.addButton("Reinstalar agora", QMessageBox.ButtonRole.AcceptRole)
            else:
                box.setWindowTitle("Acelerar o Parakeet na GPU?")
                box.setText(
                    "O motor Parakeet pode usar a sua placa NVIDIA e ficar "
                    "cerca de 4x mais rápido (uma hora de gravação em ~1 minuto).\n\n"
                    "Instalar a aceleração agora (uma vez, ~0,3 GB)? Sem ela, a "
                    "transcrição segue normalmente no processador.")
                instalar = box.addButton("Instalar agora", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Agora não", QMessageBox.ButtonRole.RejectRole)
            nunca = box.addButton("Não perguntar de novo", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is nunca:
                try:
                    flag.parent.mkdir(parents=True, exist_ok=True)
                    flag.write_text("dismissed", encoding="utf-8")
                except OSError:
                    pass
                return
            if box.clickedButton() is instalar:
                dlg = ModelManagerDialog(lambda: self.context, self)
                dlg._install_onnx_gpu_env()

        def _maybe_offer_parakeet_cpu(self, motor_spec: dict, langs: set[str]) -> bool:
            """Oferta única do TAGARELA quando o Whisper vai rodar em CPU.

            Retorna True se o usuário trocou o motor do projeto (config já
            gravada; o chamador recalcula motor_key). Não bloqueia: qualquer
            resposta transcreve. Espelha o padrão da oferta de GPU acima.
            É o caminho de migração com consentimento para quem instalou
            antes de o TAGARELA virar o padrão de máquinas sem GPU."""
            if getattr(self, "_parakeet_cpu_prompted", False):
                return False
            if not self.context:
                return False
            from . import runtime as _runtime
            flag = _runtime.app_data_dir() / "parakeet_cpu_offer_dismissed.flag"
            device = _runtime.resolve_device(
                str(self.context.config.get("asr_device") or "auto"))[0]
            if not parakeet_cpu_offer_due(
                    device, motor_spec.get("engine"), langs, self._engine_offer_declined(flag)):
                return False
            self._parakeet_cpu_prompted = True
            box = QMessageBox(self)
            box.setWindowTitle("Usar o motor recomendado?")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                "O motor Parakeet pt-BR (TAGARELA) é o recomendado: feito para "
                "o português falado — segundo os autores do modelo, em fala "
                "espontânea erra menos palavras que o Whisper large-v3 —, com "
                "pontuação e tempos por palavra nativos, e transcreve 1 hora de "
                "gravação em poucos minutos mesmo sem placa de vídeo. Só "
                "transcreve português.\n\n"
                "Usar o TAGARELA nesta e nas próximas transcrições deste "
                "projeto?")
            usar = box.addButton("Usar o TAGARELA", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Agora não", QMessageBox.ButtonRole.RejectRole)
            nunca = box.addButton("Não perguntar de novo", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is nunca:
                try:
                    flag.parent.mkdir(parents=True, exist_ok=True)
                    flag.write_text("dismissed", encoding="utf-8")
                except OSError:
                    pass
                return False
            if box.clickedButton() is not usar:
                return False
            return self._switch_engine_to_parakeet(machine_default=True)

        def _switch_engine_to_parakeet(self, machine_default: bool) -> bool:
            """Troca o motor do projeto para o TAGARELA (e, se pedido, o
            padrao da maquina para projetos novos). Sem idioma declarado no
            projeto, grava "pt" — o motor so transcreve portugues."""
            if self.context is None:
                return False
            updates: dict[str, Any] = {"asr_model": "parakeet-pt"}
            if not str(self.context.config.get("asr_language") or "").strip():
                updates["asr_language"] = "pt"
            try:
                self.context = app_service.update_engine_config(self.context, updates)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Não foi possível trocar o motor",
                                    sanitize_message(str(exc)))
                return False
            if machine_default:
                try:
                    from . import app_settings as _settings_sw
                    _settings_sw.save({"asr_model_default": "parakeet-pt"})
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("nao foi possivel gravar asr_model_default: %s", exc)
            return True

        def _switch_engine_to_whisper(self, model_key: str) -> bool:
            """Inverso de _switch_engine_to_parakeet (2026-09-02): passa o
            projeto para um Whisper (outros idiomas). Nao mexe no idioma nem
            no padrao da maquina."""
            if self.context is None or not model_key:
                return False
            try:
                self.context = app_service.update_engine_config(self.context, {"asr_model": model_key})
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Não foi possível trocar o motor",
                                    sanitize_message(str(exc)))
                return False
            # Escolha explicita do usuario: a oferta do TAGARELA nao volta
            # neste projeto durante a sessao (faixa e QMessageBox).
            self._engine_user_choice.add(str(self.context.paths.project_root))
            self._parakeet_cpu_prompted = True
            return True

        @property
        def _engine_user_choice(self) -> set[str]:
            if not hasattr(self, "_engine_user_choice_set"):
                self._engine_user_choice_set: set[str] = set()
            return self._engine_user_choice_set

        def _engine_offer_declined(self, flag: Path) -> bool:
            """Recusa definitiva (flag da maquina) ou escolha explicita de um
            Whisper neste projeto nesta sessao."""
            if flag.exists():
                return True
            if self.context is None:
                return False
            return str(self.context.paths.project_root) in self._engine_user_choice

        def _maybe_offer_cuda_install(self) -> None:
            """Se Windows + NVIDIA detectada + bundle sem torch_cuda + flag
            nao setada, oferece instalar CUDA pack. Chamado uma vez no
            startup, nao-bloqueante."""
            if sys.platform != "win32":
                return
            from . import runtime as _runtime
            flag = _runtime.app_data_dir() / "cuda_prompt_dismissed.flag"
            if flag.exists():
                return
            # Ja tem CUDA no bundle? Entao nada a oferecer.
            if _runtime.cuda_libs_present():
                return
            # Sem placa NVIDIA? Nada a oferecer.
            from . import capabilities as _caps
            hw = _caps.hardware_snapshot()
            if not hw.has_gpu:
                return
            # Todas as condicoes atendidas: oferece install
            detectada = (
                f"Detectamos uma placa gráfica NVIDIA com {hw.vram_gb:.0f} GB "
                "de memória de vídeo no seu computador."
                if hw.vram_gb else
                "Detectamos uma placa gráfica NVIDIA no seu computador.")
            aviso_vram = ""
            if hw.vram_gb is not None and hw.vram_gb < 4:
                aviso_vram = (
                    "\n\nAtenção: com essa memória de vídeo, a aceleração pode "
                    "não valer a pena com os modelos maiores — o modo CPU "
                    "continua disponível.")
            msg = (
                f"{detectada}\n\n"
                "O Transcritório está instalado sem a aceleração por placa "
                "gráfica. Ativando a aceleração, a transcrição fica de 3 a 9 "
                "vezes mais rápida, mas exige um download adicional de cerca "
                "de 2,5 GB.\n\n"
                "Clique em 'Baixar e instalar agora' para ativar; o "
                "Transcritório cuida do resto e avisa quando concluir."
                + aviso_vram
            )
            box = QMessageBox(self)
            box.setWindowTitle("Aceleração disponível (NVIDIA detectada)")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(msg)
            box.setTextFormat(Qt.TextFormat.PlainText)
            btn_install = box.addButton("Baixar e instalar agora", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Agora não", QMessageBox.ButtonRole.RejectRole)
            btn_never = box.addButton("Nunca perguntar", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_install:
                from . import __version__
                self._perform_cuda_install(__version__)
            elif clicked is btn_never:
                try:
                    flag.parent.mkdir(parents=True, exist_ok=True)
                    flag.write_text("dismissed\n", encoding="utf-8")
                except Exception as exc:
                    _logger.warning("nao foi possivel persistir flag CUDA dismiss: %s", exc)
            # Caso "Agora não": nao seta flag; pergunta de novo no proximo start

        def _perform_cuda_install(self, version: str) -> None:
            from . import cuda_installer
            if not cuda_installer.install_dir_writable():
                QMessageBox.warning(
                    self,
                    "Permissão insuficiente",
                    "A pasta de instalação do Transcritório não permite escrita "
                    "sem privilégios de administrador.\n\n"
                    "Feche o Transcritório, clique com o botão direito no "
                    "atalho, escolha 'Executar como administrador' e tente "
                    "de novo; OU reinstale o Transcritório escolhendo "
                    "'Instalar so pra mim (recomendado)'."
                )
                return
            dlg = QProgressDialog("Conectando ao GitHub…", "Cancelar", 0, 100, self)
            dlg.setWindowTitle("Instalando aceleração NVIDIA")
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            dlg.show()
            QApplication.processEvents()
            cancelled = {"flag": False}
            dlg.canceled.connect(lambda: cancelled.__setitem__("flag", True))

            def _progress(message: str, pct: int) -> None:
                dlg.setLabelText(message)
                dlg.setValue(pct)
                QApplication.processEvents()

            try:
                cuda_installer.download_and_extract(
                    version=version,
                    progress_callback=_progress,
                    should_cancel=lambda: cancelled["flag"],
                )
                dlg.close()
                QMessageBox.information(
                    self,
                    "Aceleração instalada",
                    "Pronto! Reinicie o Transcritório para ativar a aceleração NVIDIA.",
                )
            except Exception as exc:
                dlg.close()
                if not cancelled["flag"]:
                    QMessageBox.critical(
                        self,
                        "Falha ao instalar aceleração",
                        f"Não foi possível instalar a aceleração NVIDIA:\n\n{exc}\n\n"
                        "Você pode tentar de novo mais tarde (a pergunta vai "
                        "aparecer de novo no próximo início do Transcritório).",
                    )

        def show_startup_dialog(self) -> None:
            # Tela A: Setup wizard when AI components are missing.
            # Gate respeita a escolha persistida (diarizacao opcional, v0.2):
            # quem pulou o token nao ve o wizard reaparecer a cada inicio.
            def _models_ready() -> bool:
                # get_required_models levanta ValueError quando o
                # run_config.yaml traz um modelo ASR desconhecido (editado
                # a mao, vindo da CLI). Sem este guard a excecao subia no
                # startup e o app nao abria.
                try:
                    from . import app_settings as _settings
                    return app_service.required_models_ready(
                        self._configured_asr_variants(),
                        include_diarization=self._configured_diarize(),
                        include_alignment=_settings.alignment_default(),
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("Verificacao de modelos falhou: %s", exc)
                    self.progress_label.setText(
                        "Não foi possível verificar os modelos instalados "
                        "(confira em Ferramentas → Configurar transcrição…).")
                    return True  # nao bloquear a abertura do app
            if not _models_ready():
                wizard = FirstRunWizard(self)
                result = wizard.exec()
                self._caps_cache = None  # o assistente pode ter mudado o que existe
                if result == QDialog.DialogCode.Accepted and wizard.download_completed:
                    # Components installed — show project chooser
                    self.progress_label.setText("Componentes de AI instalados.")
                else:
                    # Skipped or cancelled — show warning
                    if not _models_ready():
                        self.progress_label.setText(
                            "⚠ Componentes de AI não instalados. "
                            "Use Ferramentas → Gerenciar modelos…"
                        )
                        self.progress_label.setStyleSheet(_style_err())
                        self.refresh_interviews()
                        return
                # Fall through to project chooser if models are now ready
                if not _models_ready():
                    self.refresh_interviews()
                    return

            # Offer CUDA install dialog if NVIDIA detectada e bundle nao tem
            # (chamado antes do project chooser; o dialogo e nao-bloqueante
            # no sentido de que o usuario so tem 3 opcoes e a resposta fecha).
            self._maybe_offer_cuda_install()

            # Tela B: Project chooser when everything is ready
            dialog = ProjectChooserDialog(self.context, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                # Fechar no X nao pode ser queda livre: o refresh liga o
                # empty-state "Comece criando um projeto".
                self.refresh_interviews()
                if self.context is None:
                    return
                return
            if dialog.choice == "new":
                self.new_project()
            elif dialog.choice == "open":
                self.open_project()
            elif dialog.choice == "recent" and dialog.selected_recent is not None:
                self._open_project_path(dialog.selected_recent)
            else:
                self.refresh_interviews()

        def show_model_manager(self) -> None:
            dialog = ModelManagerDialog(lambda: self.context, self)
            dialog.exec()

        def show_model_setup(self, asr_variants: list[str] | None = None,
                             include_diarization: bool | None = None,
                             include_alignment: bool | None = None,
                             align_languages: tuple[str, ...] | None = None) -> None:
            # Escopo parametrizavel (F2): ensure_models_ready passa o SEU —
            # recalcular aqui do zero fazia "Melhorar falantes" (que exige
            # diarizacao) cair num dialogo "nao ha nada para baixar".
            if self.worker and self.worker.isRunning():
                QMessageBox.information(self, "Tarefa em andamento", "Aguarde a tarefa atual terminar antes de preparar modelos.")
                return
            from . import app_settings as _settings
            scope_variants = asr_variants or self._configured_asr_variants()
            scope_dia = (self._configured_diarize()
                         if include_diarization is None else bool(include_diarization))
            scope_align = (_settings.alignment_default()
                           if include_alignment is None else bool(include_alignment))
            # Idiomas do lote (etapa 4): default = o do projeto.
            scope_langs = align_languages
            if scope_langs is None and self.context is not None:
                from . import model_manager as _mm_lang
                code = _mm_lang.normalize_language(
                    (self.context.config or {}).get("asr_language"))
                scope_langs = (code,) if code else None
            dialog = ModelSetupDialog(
                self, asr_variants=scope_variants,
                include_diarization=scope_dia, include_alignment=scope_align,
                align_languages=scope_langs)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            token = dialog.token()
            self.start_worker(
                "Preparar modelos",
                [
                    (
                        "Baixando e verificando modelos locais...",
                        lambda progress, should_cancel, hf_token=token, variants=scope_variants, include_dia=scope_dia, include_align=scope_align, langs=scope_langs: app_service.download_models(
                            token=hf_token,
                            progress_callback=progress,
                            should_cancel=should_cancel,
                            asr_variants=variants,
                            include_diarization=include_dia,
                            include_alignment=include_align,
                            align_languages=langs,
                        ),
                        True,
                    )
                ],
            )

        def _configured_asr_variants(self) -> list[str] | None:
            """Variants ASR para os gates de modelos: o configurado no projeto.

            Sem projeto aberto, vale a escolha do assistente (por maquina) —
            cair no default de fabrica fazia o gate exigir o turbo de quem
            instalou o tiny."""
            from . import app_settings as _settings
            if self.context is None:
                return [_settings.asr_model_default()]
            model = (self.context.config or {}).get("asr_model")
            return [model] if model else [_settings.asr_model_default()]

        def _configured_diarize(self) -> bool:
            """Se a diarizacao entra nos gates de modelos.

            Com projeto aberto: o 'diarize' EFETIVO do projeto (tri-state
            "auto" resolvido pela instalacao). Sem projeto (startup): o
            default do wizard, resolvido do mesmo jeito — senao quem
            pulou o token veria o wizard reaparecer a cada inicio."""
            if self.context is not None:
                return app_service.diarize_effective(self.context.config or {})[0]
            from . import app_settings
            return app_service.diarize_effective(
                {"diarize": app_settings.diarize_default()})[0]

        def ensure_ffmpeg(self) -> bool:
            """O ffmpeg e pre-requisito externo (prepara o audio e le a
            duracao). Sem esta checagem, quem esquece de instala-lo so
            descobria por um 'a tarefa terminou com erro' generico."""
            from .runtime import resolve_executable
            caminho = resolve_executable("ffmpeg")
            if Path(caminho).exists() or shutil.which(caminho):
                return True
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("FFmpeg não encontrado")
            box.setText(
                "O Transcritório precisa do FFmpeg para preparar o áudio, e ele não "
                "foi encontrado neste computador.\n\n"
                "Instale com este comando no Prompt de Comando e reabra o aplicativo:\n\n"
                "    winget install Gyan.FFmpeg"
            )
            copiar = box.addButton("Copiar comando", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is copiar:
                QApplication.clipboard().setText("winget install Gyan.FFmpeg")
            return False

        def ensure_models_ready(self, require_diarization: bool | None = None,
                                asr_variants: list[str] | None = None,
                                retry: Callable[[], None] | None = None,
                                align_languages: tuple[str, ...] | None = None) -> bool:
            """Gate de modelos obrigatorios da acao que esta comecando.

            retry: a PROPRIA acao, para ser reexecutada quando o download
            (assincrono) terminar — o re-teste imediato antigo nunca via o
            download pronto e a acao morria em silencio.
            align_languages (etapa 4): pacotes de idioma exigidos pelo
            LOTE (default: idioma do projeto, "pt" na duvida)."""
            if not self.ensure_ffmpeg():
                return False
            # asr_variants: quem vai transcrever com um modelo DIFERENTE do
            # configurado (Transcrever novamente…) valida esse modelo — o
            # default continua sendo o do projeto.
            variants = asr_variants or self._configured_asr_variants()
            # require_diarization=True: acoes explicitas de falantes (Identificar
            # falantes / Melhorar falantes) exigem pyannote mesmo com o projeto
            # configurado sem diarizacao.
            include_dia = self._configured_diarize() if require_diarization is None else require_diarization
            from . import app_settings as _settings
            include_align = _settings.alignment_default()
            if app_service.required_models_ready(variants, include_diarization=include_dia,
                                                 include_alignment=include_align,
                                                 align_languages=align_languages):
                return True
            from . import model_manager as _mm
            partial = False
            try:
                partial = _mm.has_partial_cache(asr_variants=variants, include_diarization=include_dia,
                                                include_alignment=include_align,
                                                align_languages=align_languages)
            except Exception:
                partial = False
            if partial:
                title = "Download anterior inconcluso"
                msg = (
                    "Parece que um download anterior dos modelos de AI foi "
                    "interrompido antes de completar. Alguns arquivos estão "
                    "no cache mas não são suficientes para transcrever.\n\n"
                    "Deseja retomar o download agora? O progresso já baixado "
                    "será aproveitado."
                )
            else:
                title = "Modelos locais pendentes"
                # Mensagem honesta: NOMEAR o que falta (a frase generica
                # dizia que faltava "transcricao e separacao de falantes"
                # quando so faltava o pyannote de ~0,1 GB).
                try:
                    faltando = [item for item in _mm.status(
                        asr_variants=variants,
                        include_diarization=include_dia,
                        include_alignment=include_align,
                        align_languages=align_languages) if not item.cached]
                except Exception:  # noqa: BLE001
                    faltando = []
                if faltando:
                    linhas = "\n".join(
                        f"• {item.asset.label} (~{item.asset.estimated_gb:.1f} GB)"
                        for item in faltando)
                    msg = ("Para esta ação, falta baixar neste computador:\n"
                           f"{linhas}\n\nDeseja preparar agora?")
                else:
                    msg = ("Os modelos necessários para esta ação ainda não "
                           "foram baixados neste computador. Deseja preparar "
                           "agora?")
            answer = QMessageBox.question(self, title, msg)
            if answer == QMessageBox.StandardButton.Yes:
                # A retomada REAL acontece quando o worker "Preparar
                # modelos" terminar (on_worker_done) — o download e
                # assincrono e o re-teste imediato antigo nunca o via.
                self._retry_after_models = retry
                self.show_model_setup(asr_variants=variants,
                                      include_diarization=include_dia,
                                      include_alignment=include_align,
                                      align_languages=align_languages)
                if app_service.required_models_ready(variants, include_diarization=include_dia,
                                                     include_alignment=include_align,
                                                     align_languages=align_languages):
                    # Caso raro (cache ficou completo sem download novo).
                    self._retry_after_models = None
                    self._invalidate_capability_cache()
                    return True
                if not (self.worker and self.worker.isRunning()):
                    # Dialogo cancelado: nenhum download comecou.
                    self._retry_after_models = None
                    self.progress_label.setText(
                        "Preparação cancelada — a ação não foi executada.")
            return False

        def _open_project_path(self, project_path: Path) -> None:
            # Mesmo protocolo de open_project(): salvar edicoes pendentes e
            # resetar o estado do editor via switch_project_context — sem isso,
            # autosave/undo gravariam a review do projeto antigo dentro do novo.
            if not self.save_current_turn():
                return
            if not project_path.exists():
                # Item de "Projetos recentes" apontando para pasta movida/apagada:
                # abrir criaria silenciosamente um projeto novo vazio no caminho.
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    f"O projeto não foi encontrado em:\n{project_path}\n\n"
                    "Ele pode ter sido movido ou apagado.",
                )
                return
            try:
                context = app_service.open_project(project_path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Erro ao abrir projeto:\n{exc}")
                return
            self.switch_project_context(context)
            self._update_project_label()

        def _build_ui(self) -> None:
            self._build_menus()
            root = QWidget()
            root_layout = QVBoxLayout(root)
            # R1: o header customizado morreu. O nome do projeto vive na
            # barra de titulo da janela; o selo Modelo/Motor (clicavel)
            # vive na statusbar (criado aqui, posicionado adiante).
            self.project_label = QLabel(self.project_header_text())
            self.project_label.setStyleSheet(_style_muted())
            self.project_label.setTextFormat(Qt.TextFormat.RichText)
            self.project_label.linkActivated.connect(lambda _link: self.configure_engine())
            self._update_project_label()

            # Toolbar real (R1): ordem = jornada — producao | revisao |
            # analise. Botoes de acao via addAction espelham a QAction
            # nativamente. A chave Separar falantes vive no dropdown do
            # Transcrever (R3) — a toolbar nao carrega mais estado solto.
            from . import ui_shell
            toolbar = ui_shell.build_tool_bar(self)
            toolbar.addWidget(self.media_button())
            toolbar.addWidget(self.transcribe_menu_button())
            toolbar.addSeparator()
            toolbar.addAction(self.save_action)
            toolbar.addAction(self.generate_files_action)
            toolbar.addSeparator()
            # Identidade propria da exploracao por sentido (feedback
            # 2026-08-26: nunca misturar com a busca de palavras).
            toolbar.addAction(self.explore_action)
            self.addToolBar(toolbar)
            # Estado inicial da chave (sem projeto = default EFETIVO da
            # maquina; tri-state "auto" resolvido pela instalacao).
            self._sync_diarize_toggle()

            # Statusbar real (R1): estado EMBAIXO. Nomes de atributo
            # preservados (aliasing) — PipelineWorker/update_action_states
            # escrevem por nome e seguem intocados.
            self.progress_label = QLabel("Pronto.")
            self.save_status_label = QLabel("Sem transcrição aberta.")
            self.save_status_label.setStyleSheet(_style_muted())
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setVisible(False)
            self.cancel_job_button = self.action_button(self.cancel_job_action)
            self.cancel_job_button.setVisible(False)
            self.setStatusBar(ui_shell.build_status_bar(
                self,
                activity_label=self.progress_label,
                progress_bar=self.progress_bar,
                cancel_button=self.cancel_job_button,
                save_label=self.save_status_label,
                engine_badge=self.project_label,
            ))

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(self._build_interview_panel())
            splitter.addWidget(self._build_review_panel())
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 4)
            root_layout.addWidget(splitter, stretch=1)
            self.setCentralWidget(root)

        def _build_interview_panel(self) -> QWidget:
            panel = QWidget()
            # Minimo EXPLICITO destrava o splitter horizontal: sem ele o Qt
            # honra o minimumSizeHint da fileira de filtros (~600px) e o
            # divisor fica sem curso (teste real 2026-08-31). A tabela tem
            # scrollbar propria; estreitar e escolha do usuario.
            panel.setMinimumWidth(220)
            layout = QVBoxLayout(panel)
            layout.addWidget(QLabel("Arquivos do projeto"))
            # Filter toolbar
            filter_row = QHBoxLayout()
            filter_row.addWidget(QLabel("Status:"))
            self.filter_status_combo = QComboBox()
            self.filter_status_combo.addItems(["Todas", "Transcritas", "Pendentes", "Processando"])
            self.filter_status_combo.setToolTip("Filtrar por status de transcrição.")
            self.filter_status_combo.currentIndexChanged.connect(self._apply_interview_filter)
            filter_row.addWidget(self.filter_status_combo)
            filter_row.addSpacing(12)
            filter_row.addWidget(QLabel("Buscar:"))
            self.filter_text_edit = QLineEdit()
            self.filter_text_edit.setPlaceholderText("ID da entrevista")
            self.filter_text_edit.setClearButtonEnabled(True)
            self.filter_text_edit.setToolTip("Filtrar por ID (busca parcial).")
            self.filter_text_edit.textChanged.connect(self._apply_interview_filter)
            filter_row.addWidget(self.filter_text_edit)
            # Presenca visivel da busca de palavras (feedback 2026-08-26:
            # atalho+menu nao bastam) — ao lado do filtro por ID.
            project_search_button = QPushButton("Buscar palavras…")
            project_search_button.setToolTip(self.project_search_action.toolTip() + "\n(Ctrl+Shift+F)")
            project_search_button.clicked.connect(lambda: self.open_word_search())
            filter_row.addWidget(project_search_button)
            layout.addLayout(filter_row)
            # 2026-09-02: migracao VISIVEL para o TAGARELA em maquinas sem
            # GPU — quem instalou antes da 0.2.2 ficou no Whisper, e a
            # QMessageBox ao Transcrever era facil de nao ver. Decisao pura:
            # engine_offer_due; recusa grava a mesma flag da QMessageBox.
            self.engine_offer_banner = QFrame()
            self.engine_offer_banner.setVisible(False)
            self.engine_offer_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}"
            )
            engine_offer_layout = QHBoxLayout(self.engine_offer_banner)
            engine_offer_layout.setContentsMargins(10, 6, 10, 6)
            engine_offer_label = QLabel(
                "⚡ O TAGARELA é o motor recomendado: feito para o português "
                "falado (segundo os autores do modelo, erra menos palavras que o "
                "Whisper em fala espontânea), com pontuação e tempos por palavra "
                "nativos, e transcreve 1 hora em poucos minutos mesmo sem placa "
                "de vídeo. Só transcreve português.")
            engine_offer_label.setWordWrap(True)
            engine_offer_layout.addWidget(engine_offer_label, 1)
            engine_accept_button = QPushButton("Usar o TAGARELA")
            engine_accept_button.setToolTip(
                "Troca o motor deste projeto e o padrão deste computador; "
                "baixa o modelo se ele ainda não estiver instalado.")
            engine_accept_button.clicked.connect(self._on_engine_offer_accept)
            engine_offer_layout.addWidget(engine_accept_button)
            engine_decline_button = QPushButton("Continuar com o Whisper")
            engine_decline_button.setToolTip("Não perguntar de novo neste computador.")
            engine_decline_button.clicked.connect(self._on_engine_offer_decline)
            engine_offer_layout.addWidget(engine_decline_button)
            layout.addWidget(self.engine_offer_banner)
            # R3: oferta contextual que substitui o item de menu
            # "Reprocessar falantes" — aparece quando ha entrevistas
            # transcritas sem separacao de vozes e o recurso esta ativo
            # (instalado => aplicado); some quando nao ha o que completar.
            self.diar_offer_banner = QFrame()
            self.diar_offer_banner.setVisible(False)
            self.diar_offer_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}"
            )
            diar_offer_layout = QHBoxLayout(self.diar_offer_banner)
            diar_offer_layout.setContentsMargins(10, 6, 10, 6)
            self.diar_offer_label = QLabel("")
            self.diar_offer_label.setWordWrap(True)
            diar_offer_layout.addWidget(self.diar_offer_label, 1)
            diar_offer_button = QPushButton("Separar falantes agora")
            diar_offer_button.setToolTip(
                "Separa quem fala nas entrevistas listadas, sem transcrever "
                "de novo.\nSó toca quem ainda não tem separação — suas "
                "edições ficam como estão.")
            diar_offer_button.clicked.connect(self._on_diar_offer_clicked)
            diar_offer_layout.addWidget(diar_offer_button)
            layout.addWidget(self.diar_offer_banner)
            # Faixa "lote em andamento" (2026-09-02): explica por que as acoes
            # (AI inclusive) estao cinza e responde ao clique nelas.
            self.busy_hint_banner = QFrame()
            self.busy_hint_banner.setVisible(False)
            self.busy_hint_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}"
            )
            busy_hint_layout = QHBoxLayout(self.busy_hint_banner)
            busy_hint_layout.setContentsMargins(10, 6, 10, 6)
            self.busy_hint_label = QLabel("")
            self.busy_hint_label.setWordWrap(True)
            busy_hint_layout.addWidget(self.busy_hint_label, 1)
            layout.addWidget(self.busy_hint_banner)
            # Faixa "vozes por identificar" (2026-09-02): ao fim de um lote,
            # as entrevistas cujas vozes ainda nao foram nomeadas — o dialogo
            # automatico so cobre a transcricao aberta; as demais ficam aqui,
            # com identificacao em sequencia.
            self.voice_batch_banner = QFrame()
            self.voice_batch_banner.setVisible(False)
            self.voice_batch_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}"
            )
            voice_batch_layout = QHBoxLayout(self.voice_batch_banner)
            voice_batch_layout.setContentsMargins(10, 6, 10, 6)
            self.voice_batch_label = QLabel("")
            self.voice_batch_label.setWordWrap(True)
            voice_batch_layout.addWidget(self.voice_batch_label, 1)
            self.voice_batch_button = QPushButton("Identificar agora…")
            self.voice_batch_button.setToolTip(
                "Abre cada entrevista pendente, uma por vez, e pergunta de quem "
                "é cada voz.\nCancelar numa delas interrompe a sequência — o "
                "restante continua nesta faixa.")
            self.voice_batch_button.clicked.connect(self._on_voice_batch_identify)
            voice_batch_layout.addWidget(self.voice_batch_button)
            self.voice_batch_later_button = QPushButton("Depois")
            self.voice_batch_later_button.setToolTip(
                "Esconde esta faixa. Cada entrevista continua perguntando "
                "pelas vozes quando você a abrir.")
            self.voice_batch_later_button.clicked.connect(self._on_voice_batch_later)
            voice_batch_layout.addWidget(self.voice_batch_later_button)
            layout.addWidget(self.voice_batch_banner)
            # Interview table (10 columns: checkbox + 9 data columns)
            self.interview_table = QTableWidget(0, 10)
            self.interview_table.setAccessibleName("Arquivos do projeto")
            self.interview_table.setHorizontalHeaderLabels([
                "", "Arquivo", "Formato", "Transcrição", "Duração",
                "Língua", "Falantes", "Rótulos", "Contexto", "Avisos",
            ])
            self.interview_table.horizontalHeader().setSectionResizeMode(COL_CHECK, QHeaderView.ResizeMode.Fixed)
            self.interview_table.setColumnWidth(COL_CHECK, 30)
            for col in range(COL_ARQUIVO, COL_AVISOS):
                self.interview_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            self.interview_table.horizontalHeader().setSectionResizeMode(COL_AVISOS, QHeaderView.ResizeMode.Stretch)
            self.interview_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
            self.interview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.interview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.interview_table.setSortingEnabled(True)
            self.interview_table.cellClicked.connect(self._on_interview_cell_clicked)
            self.interview_table.itemSelectionChanged.connect(self.update_action_states)
            # Enter ou duplo-clique abre a transcricao selecionada
            self.interview_table.itemActivated.connect(lambda _item: self.open_selected_review())
            self.interview_table.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
            self.interview_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.interview_table.customContextMenuRequested.connect(self._show_library_context_menu)
            # Ancorar actions com shortcut WidgetWithChildrenShortcut na tabela
            self.interview_table.addAction(self.rename_interview_action)
            self.interview_table.addAction(self.move_up_action)
            self.interview_table.addAction(self.move_down_action)
            self.interview_table.addAction(self.trash_selected_action)
            self.interview_table.addAction(self.trash_undo_action)
            self.interview_table.addAction(self.trash_redo_action)
            layout.addWidget(self.interview_table, stretch=1)
            # Rich empty-state drop zone shown when no media is in the project.
            # Purpose: make the "add media" affordance obvious for non-technical
            # users (explicit feedback from Lucas: UI should be more evident).
            self._empty_state_widget = QWidget()
            self._empty_state_widget.setStyleSheet(
                f"QWidget#emptyDropZone {{ border: 2px dashed {ui_tokens.ACCENT}; "
                "border-radius: 10px; background: transparent; }"
            )
            self._empty_state_widget.setObjectName("emptyDropZone")
            _es_layout = QVBoxLayout(self._empty_state_widget)
            _es_layout.setContentsMargins(24, 36, 24, 36)
            _es_layout.setSpacing(12)
            _es_layout.addStretch(1)
            self._empty_icon = QLabel("📁")
            self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_icon.setStyleSheet("font-size: 48px;")
            _es_layout.addWidget(self._empty_icon)
            self._empty_title = QLabel("Arraste áudios e vídeos aqui")
            self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_title.setStyleSheet("font-size: 16px; font-weight: 600;")
            _es_layout.addWidget(self._empty_title)
            self._empty_sub = QLabel("ou clique no botão abaixo")
            self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_sub.setStyleSheet(f"{_style_muted()} font-size: 13px;")
            self._empty_sub.setWordWrap(True)
            _es_layout.addWidget(self._empty_sub)
            _cta_row = QHBoxLayout()
            _cta_row.addStretch(1)
            self._empty_cta_btn = QPushButton("+ Adicionar mídia…")
            self._empty_cta_btn.setStyleSheet(self._MEDIA_BUTTON_PRIMARY_QSS)
            _cta_menu = QMenu(self._empty_cta_btn)
            _cta_menu.addAction(self.add_files_action)
            _cta_menu.addAction(self.add_folder_action)
            self._empty_cta_btn.setMenu(_cta_menu)
            _cta_row.addWidget(self._empty_cta_btn)
            # Modo "sem projeto" (1o teste real, 2026-08-30): fechar a tela
            # de escolha derrubava o usuario numa tabela vazia sem caminho.
            self._empty_new_project_btn = QPushButton("Criar projeto…")
            self._empty_new_project_btn.setStyleSheet(self._MEDIA_BUTTON_PRIMARY_QSS)
            self._empty_new_project_btn.clicked.connect(self.new_project)
            _cta_row.addWidget(self._empty_new_project_btn)
            self._empty_open_project_btn = QPushButton("Abrir projeto…")
            self._empty_open_project_btn.clicked.connect(self.open_project)
            _cta_row.addWidget(self._empty_open_project_btn)
            _cta_row.addStretch(1)
            _es_layout.addLayout(_cta_row)
            # Somente os formatos que o manifest REALMENTE aceita
            # (config.media_extensions) — prometer OGG/OPUS/WMA e rejeitar
            # o arquivo na linha seguinte era promessa falsa.
            self._empty_hint = QLabel("Formatos aceitos: MP3, M4A, WAV, FLAC, MP4, MOV")
            self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_hint.setStyleSheet(f"{_style_muted()} font-size: 11px;")
            self._empty_hint.setWordWrap(True)
            _es_layout.addWidget(self._empty_hint)
            _es_layout.addStretch(2)
            self._empty_state_widget.setVisible(False)
            layout.addWidget(self._empty_state_widget, stretch=1)
            metadata_button = self.action_button(self.apply_metadata_action)
            layout.addWidget(metadata_button)
            open_button = self.action_button(self.open_transcript_action)
            layout.addWidget(open_button)
            return panel

        def _has_project(self) -> bool:
            return self.context is not None

        def _update_empty_state_mode(self) -> None:
            """Alterna a drop zone entre 'sem projeto' e 'projeto sem mídia'.

            Sem projeto, ela vira o convite com o modelo mental e os botões
            que resolvem — em vez de uma tabela vazia sem caminho."""
            if not hasattr(self, "_empty_state_widget"):
                return
            sem_projeto = self.context is None
            self._empty_icon.setText("🗂️" if sem_projeto else "📁")
            self._empty_title.setText(
                "Comece criando um projeto" if sem_projeto
                else "Arraste áudios e vídeos aqui")
            self._empty_sub.setText(
                "O projeto é uma pasta única onde o Transcritório guarda todo o "
                "trabalho.\nSuas gravações não são copiadas nem alteradas — o "
                "projeto apenas as referencia onde estão."
                if sem_projeto else "ou clique no botão abaixo")
            self._empty_cta_btn.setVisible(not sem_projeto)
            self._empty_hint.setVisible(not sem_projeto)
            self._empty_new_project_btn.setVisible(sem_projeto)
            self._empty_open_project_btn.setVisible(sem_projeto)

        def _require_project(self, action_label: str = "Esta acao") -> bool:
            """Sem projeto: explica E oferece os botões que resolvem.

            (Antes mandava o usuário ao menu — beco sem saída clássico.)"""
            if self.context is not None:
                return True
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Nenhum projeto aberto")
            box.setText(f"{action_label} requer um projeto aberto.")
            criar = box.addButton("Criar projeto…", QMessageBox.ButtonRole.ActionRole)
            abrir = box.addButton("Abrir projeto…", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Agora não", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(criar)
            box.exec()
            if box.clickedButton() is criar:
                self.new_project()
            elif box.clickedButton() is abrir:
                self.open_project()
            return False

        def _browse_dir(self) -> str:
            if self.context is not None:
                return str(self.context.paths.project_root)
            return str(Path.home())

        def _update_project_label(self) -> None:
            """Selo Modelo/Motor da statusbar + titulo da janela (R1).

            O nome do projeto mora na barra de titulo ("Transcritório —
            <nome>"); o caminho completo mora no tooltip do selo. Sem
            projeto, o tooltip nao promete links que nao existem."""
            if not hasattr(self, "project_label"):
                return
            self.project_label.setText(self.project_header_text())
            if self.context is not None:
                nome = str(self.context.project.get("project_name")
                           or self.context.paths.project_root.name)
                self.setWindowTitle(f"{APP_NAME} — {nome}{channel_suffix()}")
                self.project_label.setToolTip(
                    f"Projeto: {self.context.paths.project_root}\n"
                    "Clique em \"Modelo\" ou \"Motor\" para configurar a "
                    "transcrição (modelo, dispositivo CUDA/CPU, idioma).")
            else:
                self.setWindowTitle(APP_NAME + channel_suffix())
                self.project_label.setToolTip("Nenhum projeto aberto.")

        def project_header_text(self) -> str:
            if self.context is None:
                return "Nenhum projeto aberto"
            from . import model_manager
            from . import runtime as _runtime_local
            # Mostrar o modelo CONFIGURADO; se o que vai rodar de fato for
            # outro (configurado ainda nao baixado), dizer — o selo calado
            # fazia o usuario concluir que "continuou o small" (2026-09-02).
            configurado = str(self.context.config.get("asr_model") or "?")
            model = configurado
            if model_manager.resolve_asr_model(configurado) != configurado:
                model = f"{configurado} (não instalado)"
            asr_device = self.context.config.get("asr_device") if self.context else None
            backend = _runtime_local.describe_backend(asr_device)
            # TAGARELA numa maquina CUDA roda no processador ate o pacote
            # onnx-gpu existir: o selo nao pode dizer "CUDA" em verde.
            engine_efetivo = (model_manager.ASR_VARIANTS.get(
                model_manager.resolve_asr_model(configurado)) or {}).get("engine")
            if engine_efetivo == "parakeet_onnx" and "CUDA" in backend:
                from . import parakeet_runner as _pk_badge
                if _pk_badge.planned_device("cuda") == "cpu":
                    backend = "CPU (TAGARELA — aceleração NVIDIA não ativa)"
            # Color the backend badge: green for GPU acceleration, amber when
            # only CPU is available so the user sees it at a glance.
            # E um LINK (mesmo destino do "Modelo"): o seletor CUDA/CPU
            # sempre existiu no dialogo do Motor, mas o selo nao-clicavel
            # o tornava indescobrivel (teste real 2026-08-30).
            is_accel = "CUDA" in backend or "MLX" in backend
            badge_color = ui_tokens.SUCCESS if is_accel else ui_tokens.WARN
            badge = (
                f'<a href="engine-settings" style="color:{badge_color};'
                f'font-weight:600;text-decoration:underline;">'
                f'Motor: {backend}'
                f'</a>'
            )
            return (f'<a href="engine-settings" style="color:{ui_tokens.TEXT_MUTED};text-decoration:underline;">Modelo: {model}</a>'
                    f"  ·  {badge}")

        def _build_review_panel(self) -> QWidget:
            panel = QWidget()
            # Sem minimo explicito, o minimumSizeHint deste painel chega a
            # ~1850px (titulo longo sem quebra + fileira de controles do
            # player) e o splitter horizontal fica sem curso NENHUM.
            panel.setMinimumWidth(480)
            layout = QVBoxLayout(panel)
            self.review_title = QLabel("Abra uma entrevista para editar a transcrição.")
            self.review_title.setStyleSheet("font-size: 16px; font-weight: 700;")
            layout.addWidget(self.review_title)

            self.open_file_action_row = QHBoxLayout()
            self.transcribe_current_button = self.action_button(self.transcribe_current_action, primary=True)
            self.transcribe_current_button.setVisible(False)
            self.improve_speakers_button = self.action_button(self.improve_speakers_action)
            self.improve_speakers_button.setVisible(False)
            # Presenca contextual do resumo (feedback 2026-08-26): o botao so
            # existe quando ESTE arquivo tem resumo salvo — um clique, abre.
            self.open_resumo_button = QPushButton("✨ Abrir resumo com temas")
            self.open_resumo_button.setToolTip(
                "Abre o resumo com indice tematico ja gerado para este arquivo\n"
                "(05_transcripts_review/final/md/). Gerado com AI local.")
            self.open_resumo_button.setVisible(False)
            self.open_resumo_button.clicked.connect(self._open_current_resumo)
            # Par gerar/abrir (exigencia 2026-08-26: "ficar so no menu nao
            # da"): sem resumo -> Gerar; com resumo -> Abrir. A faixa toda
            # sera repensada na revisao de UI (Fase 5).
            self.generate_resumo_button = QPushButton("✨ Resumir a entrevista com AI")
            self.generate_resumo_button.setToolTip(self.summarize_action.toolTip())
            self.generate_resumo_button.setVisible(False)
            self.generate_resumo_button.clicked.connect(self.run_summarize_job)
            self.open_file_action_row.addWidget(self.transcribe_current_button)
            # Presenca contextual do refazer: para arquivo TRANSCRITO o botao
            # "Transcrever este arquivo" some — este assume o lugar (menu
            # nunca e o unico caminho).
            self.retranscribe_current_button = self.action_button(self.retranscribe_current_action)
            self.retranscribe_current_button.setVisible(False)
            self.open_file_action_row.addWidget(self.retranscribe_current_button)
            self.open_file_action_row.addWidget(self.improve_speakers_button)
            self.open_file_action_row.addWidget(self.generate_resumo_button)
            self.open_file_action_row.addWidget(self.open_resumo_button)
            self.open_file_action_row.addStretch()
            layout.addLayout(self.open_file_action_row)

            self.review_splitter = QSplitter(Qt.Orientation.Vertical)
            self.review_splitter.setHandleWidth(8)
            # Abas do painel direito (R2, dossie RD): Transcrição
            # (trabalho diario) | Documentos (casa dos resultados).
            # Propriedades chega no proximo passo da R2. O splitter e
            # REPARENTADO para dentro da aba — nunca recriado (as
            # ancoras de atalho vivem nos widgets).
            self.review_tabs = QTabWidget()
            transcricao_tab = QWidget()
            transcricao_layout = QVBoxLayout(transcricao_tab)
            transcricao_layout.setContentsMargins(0, ui_tokens.SP_2, 0, 0)
            transcricao_layout.addWidget(self.review_splitter)
            self.review_tabs.addTab(transcricao_tab, "Transcrição")
            from .ui_docs_panel import DocsPanel
            self.docs_panel = DocsPanel()
            self.docs_panel.open_requested.connect(
                lambda p: open_folder_in_explorer(Path(p)))
            self.docs_panel.show_in_folder_requested.connect(
                self._show_path_in_folder)
            self.docs_panel.action_requested.connect(self._docs_action)
            # Abrir do banner de sucesso: os.startfile/xdg-open abrem o
            # ARQUIVO no app padrao (o nome do helper engana) — o mesmo
            # gesto do Abrir das linhas do painel.
            self.docs_panel.open_document_requested.connect(
                lambda p: open_folder_in_explorer(Path(p)))
            self.review_tabs.addTab(self.docs_panel, "Documentos")
            self.review_tabs.addTab(self._build_props_panel(), "Propriedades")
            self.review_tabs.currentChanged.connect(self._on_review_tab_changed)
            layout.addWidget(self.review_tabs, stretch=1)

            # O video e um PANE PROPRIO do splitter (2026-08-31): assim ele
            # colapsa/estica independente do audio. media_panel vira so
            # onda + controles.
            self.video_widget = QVideoWidget()
            # 120 vale para tamanhos intermediarios; o colapso a zero via
            # arrasto e permitido pelo setCollapsible do splitter.
            self.video_widget.setMinimumHeight(120)
            self.video_widget.setStyleSheet(f"background: {ui_tokens.VIDEO_BG};")
            self.video_widget.setVisible(False)
            self.player.setVideoOutput(self.video_widget)

            media_panel = QWidget()
            media_layout = QVBoxLayout(media_panel)
            media_layout.setContentsMargins(0, 0, 0, 0)

            self.waveform_widget = WaveformWidget()
            self.waveform_widget.seek_requested.connect(self.seek_waveform)
            media_layout.addWidget(self.waveform_widget)

            waveform_controls = QHBoxLayout()
            for label, tooltip, callback in [
                ("Zoom +", "Aproximar a onda sonora", self.zoom_waveform_in),
                ("Zoom -", "Afastar a onda sonora", self.zoom_waveform_out),
                ("Ver onda inteira", "Mostrar a onda sonora inteira", self.zoom_waveform_fit),
                ("Ver bloco", "Aproximar a onda sonora no bloco selecionado", self.zoom_waveform_to_current_turn),
                ("Centralizar no áudio", "Centralizar a onda no ponto atual do player", self.center_waveform_on_player),
            ]:
                button = QPushButton(label)
                button.setToolTip(tooltip)
                button.clicked.connect(callback)
                waveform_controls.addWidget(button)
            waveform_controls.addStretch()
            media_layout.addLayout(waveform_controls)

            media_controls = QHBoxLayout()
            self.play_button = QPushButton("Reproduzir")
            self.play_button.setAccessibleName("Reproduzir ou pausar")
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.play_button.setToolTip("Reproduzir ou pausar o áudio da entrevista. (Espaço; F4 com o cursor no texto)")
            self.play_button.clicked.connect(self.toggle_playback)
            media_controls.addWidget(self.play_button)
            stop_button = QPushButton("Parar")
            stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
            stop_button.setToolTip("Parar a reprodução e voltar ao início.")
            stop_button.clicked.connect(self.stop_playback)
            media_controls.addWidget(stop_button)
            back_button = QPushButton("-5s")
            back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
            back_button.setToolTip("Voltar 5 segundos no áudio. (Ctrl+Esquerda; Alt+Esquerda com o cursor no texto)")
            back_button.clicked.connect(lambda: self.seek_relative(-5))
            media_controls.addWidget(back_button)
            forward_button = QPushButton("+5s")
            forward_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
            forward_button.setToolTip("Avançar 5 segundos no áudio. (Ctrl+Direita; Alt+Direita com o cursor no texto)")
            forward_button.clicked.connect(lambda: self.seek_relative(5))
            media_controls.addWidget(forward_button)
            media_controls.addWidget(self.action_button(self.repeat_turn_action))
            self.position_slider = QSlider(Qt.Orientation.Horizontal)
            self.position_slider.setAccessibleName("Posição do áudio")
            self.position_slider.setToolTip("Arraste para navegar no áudio da entrevista.")
            self.position_slider.sliderPressed.connect(self._slider_pressed)
            self.position_slider.sliderReleased.connect(self._slider_released)
            media_controls.addWidget(self.position_slider, stretch=1)
            self.time_label = QLabel("00:00:00 / 00:00:00")
            media_controls.addWidget(self.time_label)
            self.speed_combo = QComboBox()
            self.speed_combo.setAccessibleName("Velocidade de reprodução")
            self.speed_combo.setToolTip("Velocidade de reprodução do áudio (0.75x a 2.0x). F7 diminui, F8 aumenta.")
            for label, rate in [("0.75x", 0.75), ("1.0x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2.0x", 2.0)]:
                self.speed_combo.addItem(label, rate)
            self.speed_combo.setCurrentIndex(1)
            self.speed_combo.currentIndexChanged.connect(self.update_playback_rate)
            media_controls.addWidget(self.speed_combo)
            media_controls.addWidget(QLabel("Vol:"))
            self.volume_slider = QSlider(Qt.Orientation.Horizontal)
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(100)
            self.volume_slider.setFixedWidth(80)
            self.volume_slider.setToolTip("Volume de reprodução do áudio.")
            self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
            media_controls.addWidget(self.volume_slider)
            self.follow_playback_checkbox = QCheckBox("Acompanhar reprodução")
            self.follow_playback_checkbox.setToolTip("Quando ativo, a tabela de blocos acompanha automaticamente o ponto de reprodução do áudio.")
            self.follow_playback_checkbox.setChecked(True)
            media_controls.addWidget(self.follow_playback_checkbox)
            # Controle contextual de painel (sem QAction/menu, como os
            # botoes da onda): so aparece quando a midia tem imagem.
            self.video_toggle_button = QPushButton("Ocultar vídeo")
            self.video_toggle_button.setToolTip(
                "Oculta a imagem do vídeo; o áudio continua tocando.")
            self.video_toggle_button.setVisible(False)
            self.video_toggle_button.clicked.connect(self._toggle_video_panel)
            media_controls.addWidget(self.video_toggle_button)
            media_layout.addLayout(media_controls)

            self.turn_table = QTableWidget(0, 4)
            self.turn_table.setAccessibleName("Blocos da transcrição")
            self.turn_table.setHorizontalHeaderLabels(["Tempo", "Falante", "Texto", "Marcações"])
            self.turn_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.turn_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.turn_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.turn_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self.turn_table.setWordWrap(True)
            self.turn_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self.turn_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.turn_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.turn_table.cellClicked.connect(self.on_turn_cell_clicked)
            self.turn_table.cellDoubleClicked.connect(self.seek_turn_from_row)
            self.turn_table.itemSelectionChanged.connect(self.on_turn_selection_changed)
            turn_panel = QWidget()
            turn_layout = QVBoxLayout(turn_panel)
            turn_layout.setContentsMargins(0, 0, 0, 0)
            # Slot UNICO de banner (R2): os tres avisos declaram interesse
            # e a area mostra so o de maior prioridade — fim da pilha de
            # faixas simultaneas. Fachadas _update_*_banner continuam donas
            # da decisao de QUANDO cada um quer aparecer.
            from .ui_banners import BannerArea
            self.banner_area = BannerArea()
            # Banner contextual (plano D2.6): a acao importante mora onde e
            # necessaria — o menu e acesso secundario.
            self.voice_banner = QFrame()
            self.voice_banner.setVisible(False)
            self.voice_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}"
            )
            banner_layout = QHBoxLayout(self.voice_banner)
            banner_layout.setContentsMargins(10, 6, 10, 6)
            banner_label = QLabel("🔊 As vozes desta transcrição ainda não foram confirmadas.")
            banner_label.setWordWrap(True)
            banner_layout.addWidget(banner_label, 1)
            self.voice_banner_button = QPushButton("Dar nome às vozes…")
            self.voice_banner_button.clicked.connect(self._on_banner_identify_clicked)
            banner_layout.addWidget(self.voice_banner_button)
            banner_dismiss = QPushButton("Não perguntar neste projeto")
            banner_dismiss.setFlat(True)
            banner_dismiss.setToolTip("Desliga a pergunta neste projeto. Reative em Ferramentas.")
            banner_dismiss.clicked.connect(self._on_banner_dismiss_clicked)
            banner_layout.addWidget(banner_dismiss)
            # Banner de diarizacao falhada (plano U1.7): o lote continua, mas
            # o aviso ficava enterrado na coluna Erro da fila.
            self.diar_failed_banner = QFrame()
            self.diar_failed_banner.setVisible(False)
            self.diar_failed_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.WARN)} }}"
            )
            diar_failed_layout = QHBoxLayout(self.diar_failed_banner)
            diar_failed_layout.setContentsMargins(10, 6, 10, 6)
            diar_failed_label = QLabel("⚠ A identificação de falantes desta transcrição não foi concluída — o texto está sem separação de vozes.")
            diar_failed_label.setWordWrap(True)
            diar_failed_layout.addWidget(diar_failed_label, 1)
            diar_failed_button = QPushButton("Tentar novamente")
            diar_failed_button.setToolTip("Refaz a identificação de falantes deste arquivo e remonta a transcrição.")
            diar_failed_button.clicked.connect(self.improve_speakers_current_file)
            diar_failed_layout.addWidget(diar_failed_button)
            # Banner de trocas de falante suspeitas (plano 2026-08-25): a
            # verificacao acustica marca blocos cuja voz e igual a do bloco
            # seguinte; o banner aponta para as marcacoes, sem depender de menu.
            self.boundary_banner = QFrame()
            self.boundary_banner.setVisible(False)
            self.boundary_banner.setStyleSheet(
                f"QFrame {{ {ui_tokens.banner_style(ui_tokens.WARN)} }}"
            )
            boundary_layout = QHBoxLayout(self.boundary_banner)
            boundary_layout.setContentsMargins(10, 6, 10, 6)
            self.boundary_banner_label = QLabel("")
            self.boundary_banner_label.setWordWrap(True)
            boundary_layout.addWidget(self.boundary_banner_label, 1)
            boundary_prev = QPushButton("‹")
            boundary_prev.setFixedWidth(28)
            boundary_prev.setToolTip("Bloco marcado anterior")
            boundary_prev.clicked.connect(lambda: self._on_boundary_nav(-1))
            boundary_layout.addWidget(boundary_prev)
            boundary_next = QPushButton("›")
            boundary_next.setFixedWidth(28)
            boundary_next.setToolTip("Próximo bloco marcado")
            boundary_next.clicked.connect(lambda: self._on_boundary_nav(1))
            boundary_layout.addWidget(boundary_next)
            # Dispensavel (pedido 2026-08-31): o aviso pode ser minimizado.
            # Vale por entrevista, na sessao — reabrir o app o traz de volta.
            boundary_dismiss = QPushButton("×")
            boundary_dismiss.setFixedWidth(28)
            boundary_dismiss.setToolTip(
                "Dispensar este aviso nesta entrevista (as marcações "
                "continuam na coluna Marcações).")
            boundary_dismiss.clicked.connect(self._dismiss_boundary_banner)
            boundary_layout.addWidget(boundary_dismiss)
            # Prioridades: separacao falhada > trocas suspeitas > vozes.
            self.banner_area.add_banner("diar_failed", self.diar_failed_banner, 0)
            self.banner_area.add_banner("boundary", self.boundary_banner, 1)
            self.banner_area.add_banner("voice", self.voice_banner, 2)
            turn_layout.addWidget(self.banner_area)
            # Barra de busca no arquivo (Ctrl+F) — so existe enquanto usada.
            self.find_bar = QFrame()
            self.find_bar.setVisible(False)
            find_layout = QHBoxLayout(self.find_bar)
            find_layout.setContentsMargins(6, 4, 6, 4)
            self.find_input = QLineEdit()
            self.find_input.setPlaceholderText("🔍 buscar neste arquivo…")
            self.find_input.textChanged.connect(self._apply_find_filter)
            self.find_input.returnPressed.connect(lambda: self._find_step(1))
            find_layout.addWidget(self.find_input, 1)
            self.find_count_label = QLabel("")
            find_layout.addWidget(self.find_count_label)
            find_prev = QPushButton("‹")
            find_prev.setFixedWidth(28)
            find_prev.setToolTip("Bloco anterior com o termo")
            find_prev.clicked.connect(lambda: self._find_step(-1))
            find_layout.addWidget(find_prev)
            find_next = QPushButton("›")
            find_next.setFixedWidth(28)
            find_next.setToolTip("Próximo bloco com o termo")
            find_next.clicked.connect(lambda: self._find_step(1))
            find_layout.addWidget(find_next)
            find_close = QPushButton("✕")
            find_close.setFixedWidth(28)
            find_close.setToolTip("Fechar a busca (Esc)")
            find_close.clicked.connect(self._close_find_bar)
            find_layout.addWidget(find_close)
            QShortcut(QKeySequence("Escape"), self.find_input, activated=self._close_find_bar)
            turn_layout.addWidget(self.find_bar)
            turn_header = QHBoxLayout()
            turn_header.addWidget(QLabel("Blocos da transcrição"))
            find_toggle = QPushButton("🔍 Buscar")
            find_toggle.setFlat(True)
            find_toggle.setToolTip("Buscar nos blocos deste arquivo (Ctrl+F)")
            find_toggle.clicked.connect(self.show_find_bar)
            turn_header.addWidget(find_toggle)
            turn_header.addStretch()
            self.wrap_turns_checkbox = QCheckBox("Quebrar linhas")
            self.wrap_turns_checkbox.setChecked(True)
            self.wrap_turns_checkbox.setToolTip("Liga ou desliga a quebra de linhas na tabela de blocos sem alterar a navegacao.")
            self.wrap_turns_checkbox.stateChanged.connect(self.toggle_turn_word_wrap)
            turn_header.addWidget(self.wrap_turns_checkbox)
            turn_layout.addLayout(turn_header)
            turn_layout.addWidget(self.turn_table)
            # 4 paineis COLAPSAVEIS (pedido 2026-08-31): video | audio |
            # blocos | editor — arrastar alem do minimo colapsa a zero, e
            # com os vizinhos colapsados qualquer painel ocupa tudo.
            self.review_splitter.addWidget(self.video_widget)
            self.review_splitter.addWidget(media_panel)
            self.review_splitter.addWidget(turn_panel)
            self.review_splitter.addWidget(self._build_editor_panel())
            for indice in range(4):
                self.review_splitter.setCollapsible(indice, True)
            self.review_splitter.setStretchFactor(0, 1)
            self.review_splitter.setStretchFactor(1, 1)
            self.review_splitter.setStretchFactor(2, 3)
            self.review_splitter.setStretchFactor(3, 2)
            # Uma fonte de verdade com a redistribuicao reativa (920 =
            # nominal da janela 1440x900; a 1a exibicao reescala).
            self.review_splitter.setSizes(media_splitter_sizes(920, False))
            return panel

        def _build_editor_panel(self) -> QWidget:
            group = QGroupBox("Editar bloco selecionado")
            grid = QGridLayout(group)
            grid.addWidget(QLabel("Falante:"), 0, 0)
            self.speaker_combo = QComboBox()
            # Editavel (D2.2): o usuario pode digitar qualquer nome; NoInsert
            # evita que textos parciais entrem na lista de opcoes.
            self.speaker_combo.setEditable(True)
            self.speaker_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.speaker_combo.addItems(list(SPEAKER_LABELS))
            self.speaker_combo.currentIndexChanged.connect(self.editor_changed)
            self.speaker_combo.editTextChanged.connect(self.editor_changed)
            speaker_layout = QHBoxLayout()
            speaker_layout.addWidget(self.speaker_combo, 1)
            self.apply_speaker_all_button = QPushButton("Aplicar a todos desta voz")
            self.apply_speaker_all_button.setToolTip("Dá este nome a todos os blocos desta mesma voz na transcrição. Ctrl+Z desfaz.")
            self.apply_speaker_all_button.clicked.connect(self.apply_speaker_label_to_all)
            speaker_layout.addWidget(self.apply_speaker_all_button)
            grid.addLayout(speaker_layout, 0, 1)

            self.inaudivel_checkbox = QCheckBox(FLAG_LABELS["inaudivel"])
            self.duvida_checkbox = QCheckBox(FLAG_LABELS["duvida"])
            self.sobreposicao_checkbox = QCheckBox(FLAG_LABELS["sobreposicao"])
            for checkbox in [self.inaudivel_checkbox, self.duvida_checkbox, self.sobreposicao_checkbox]:
                checkbox.stateChanged.connect(self.editor_changed)
            flags_layout = QHBoxLayout()
            flags_layout.addWidget(self.inaudivel_checkbox)
            flags_layout.addWidget(self.duvida_checkbox)
            flags_layout.addWidget(self.sobreposicao_checkbox)
            flags_layout.addStretch()
            grid.addLayout(flags_layout, 0, 2)
            # Explicacao da marcacao do bloco selecionado (antes so existia
            # como tooltip na tabela — descoberta ruim; feedback 2026-08-25).
            self.turn_note_label = QLabel("")
            self.turn_note_label.setWordWrap(True)
            self.turn_note_label.setStyleSheet(_style_muted())
            self.turn_note_label.setVisible(False)
            grid.addWidget(self.turn_note_label, 1, 2)

            time_layout = QHBoxLayout()
            time_layout.addWidget(QLabel("Início:"))
            self.start_time_edit = QLineEdit()
            self.start_time_edit.setPlaceholderText("00:00:00.000")
            self.start_time_edit.setAccessibleName("Inicio do bloco")
            self.start_time_edit.editingFinished.connect(self.editor_changed)
            time_layout.addWidget(self.start_time_edit)
            start_now_button = QPushButton("Usar ponto atual")
            start_now_button.setToolTip("Define o início deste bloco pelo ponto atual do áudio.")
            start_now_button.clicked.connect(self.use_player_as_start)
            time_layout.addWidget(start_now_button)
            time_layout.addSpacing(18)
            time_layout.addWidget(QLabel("Fim:"))
            self.end_time_edit = QLineEdit()
            self.end_time_edit.setPlaceholderText("00:00:00.000")
            self.end_time_edit.setAccessibleName("Fim do bloco")
            self.end_time_edit.editingFinished.connect(self.editor_changed)
            time_layout.addWidget(self.end_time_edit)
            end_now_button = QPushButton("Usar ponto atual")
            end_now_button.setToolTip("Define o fim deste bloco pelo ponto atual do áudio.")
            end_now_button.clicked.connect(self.use_player_as_end)
            time_layout.addWidget(end_now_button)
            time_layout.addStretch()
            grid.addLayout(time_layout, 1, 0, 1, 4)

            self.text_edit = TurnTextEdit()
            # 60 (era 120): o QTextEdit tem scrollbar propria e este minimo
            # dita o piso do painel do editor no review_splitter — 120
            # deixava o divisor vertical quase sem curso com video aberto.
            self.text_edit.setMinimumHeight(60)
            self.text_edit.setAccessibleName("Texto do bloco selecionado")
            self.text_edit.textChanged.connect(self.editor_changed)
            self.text_edit.word_seek_requested.connect(self._seek_word_at_char)
            # Ancorar undo/redo do editor no text_edit (WidgetWithChildrenShortcut).
            # Com foco no editor, Ctrl+Z aciona QUndoStack; fora dele, trash_undo_action (ApplicationShortcut).
            self.text_edit.addAction(self.undo_action)
            self.text_edit.addAction(self.redo_action)
            grid.addWidget(self.text_edit, 2, 0, 1, 4)

            button_row = QHBoxLayout()
            self.save_block_button = QPushButton("Salvar bloco")
            self.save_block_button.setToolTip("Salva as alterações do bloco atual. Trocar de bloco também salva automaticamente.")
            self.save_block_button.clicked.connect(lambda _checked=False: self.save_current_turn(force=True))
            button_row.addWidget(self.save_block_button)
            # Ordem de leitura: anterior à esquerda, próximo à direita. Os três
            # são espelhos das ações (action_button): um só estado, um só
            # tooltip, e o atalho aparece escrito no botão que o usuário já usa.
            self.merge_prev_button = self.action_button(self.merge_prev_block_action)
            button_row.addWidget(self.merge_prev_button)
            self.merge_button = self.action_button(self.merge_block_action)
            button_row.addWidget(self.merge_button)
            self.split_button = self.action_button(self.split_block_action)
            button_row.addWidget(self.split_button)
            button_row.addStretch()
            grid.addLayout(button_row, 3, 0, 1, 4)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            grid.addWidget(line, 4, 0, 1, 4)
            hint = QLabel(
                "Dica: clique no texto para editar; duplo clique numa palavra leva o áudio até ela; "
                "clique no tempo ou duplo clique na linha para ir ao início do bloco.")
            hint.setStyleSheet(_style_muted())
            grid.addWidget(hint, 5, 0, 1, 4)
            return group

        def _connect_player(self) -> None:
            self.player.positionChanged.connect(self.on_position_changed)
            self.player.durationChanged.connect(self.on_duration_changed)
            self.player.playbackStateChanged.connect(self.on_playback_state_changed)
            self.player.errorOccurred.connect(self.on_player_error)

        def refresh_interviews(self) -> None:
            if self.context is None:
                # Sem projeto NAO e acidente: estado desenhado, com a drop
                # zone em modo "criar/abrir projeto" e as acoes coerentes.
                if hasattr(self, "_empty_state_widget"):
                    self._update_empty_state_mode()
                    self._empty_state_widget.setVisible(True)
                    self.interview_table.setVisible(False)
                if hasattr(self, "diar_offer_banner"):
                    self.diar_offer_banner.setVisible(False)
                if hasattr(self, "engine_offer_banner"):
                    self.engine_offer_banner.setVisible(False)
                if hasattr(self, "busy_hint_banner"):
                    self.busy_hint_banner.setVisible(False)
                if hasattr(self, "voice_batch_banner"):
                    self._voice_batch_ids = []
                    self.voice_batch_banner.setVisible(False)
                if hasattr(self, "project_label"):
                    self._update_project_label()
                self.update_action_states()
                return
            try:
                self.context = app_service.load_project(config_path=self.context.config_path)
                self.statuses = app_service.list_interviews(self.context)
                self._refresh_error_shown = False
            except Exception as exc:  # noqa: BLE001 - volume removido/sem permissao
                # load_project faz mkdir/escrita e refresh roda de ~15
                # lugares: um pendrive/Dropbox indisponivel derrubava o slot
                # em silencio (excepthook sem console) e a janela ficava
                # meio-atualizada. Avisar UMA vez e manter o estado atual.
                _logger.warning("refresh_interviews: projeto inacessivel: %s", exc)
                if not getattr(self, "_refresh_error_shown", False):
                    self._refresh_error_shown = True
                    QMessageBox.warning(
                        self, "Projeto inacessível",
                        "Não foi possível reler a pasta do projeto — o disco pode "
                        "ter sido removido ou estar sem permissão de escrita.\n\n"
                        f"{sanitize_message(str(exc))}")
                return
            self._status_map = {s.interview_id: s for s in self.statuses}
            if hasattr(self, "project_label"):
                self._update_project_label()
            self._sync_diarize_toggle()
            self._sync_voice_prompt_action()
            self.interview_table.setSortingEnabled(False)
            self.interview_table.blockSignals(True)
            self.interview_table.setRowCount(0)
            # Ordenar self.statuses por interview_order quando ordem manual ativa
            manual_order_active = bool(self.context.project.get("manual_order_active"))
            if manual_order_active:
                order = list(self.context.project.get("interview_order") or [])
                order_index = {iid: i for i, iid in enumerate(order)}
                self.statuses = sorted(
                    self.statuses,
                    key=lambda s: (order_index.get(s.interview_id, len(order_index)), s.interview_id),
                )
            for status in self.statuses:
                row = self.interview_table.rowCount()
                self.interview_table.insertRow(row)
                metadata = self.context.metadata.get(status.interview_id, {})
                metadata_display = project_store.metadata_display(metadata)
                job = self.context.jobs.get(status.interview_id, {})
                display_title = str(metadata.get("title") or "").strip() or status.interview_id
                # Column 0: checkbox
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                check_item.setCheckState(
                    Qt.CheckState.Checked if status.interview_id in self._checked_ids else Qt.CheckState.Unchecked
                )
                self.interview_table.setItem(row, COL_CHECK, check_item)
                # Columns 1-9: data
                values = [
                    display_title,
                    media_format_label(status),
                    self.friendly_state(status, job),
                    format_clock(float(status.duration_sec) if status.duration_sec else 0),
                    metadata_display["language"],
                    metadata_display["speakers"],
                    metadata_display["speaker_labels"],
                    metadata_display["context"],
                    status.qc_notes,
                ]
                for column, value in enumerate(values, start=COL_ARQUIVO):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if column == COL_ARQUIVO:
                        item.setData(Qt.ItemDataRole.UserRole, status.interview_id)
                        if display_title != status.interview_id:
                            item.setToolTip(status.interview_id)
                    if column == COL_TRANSCRICAO and str(value) == "Falha":
                        item.setToolTip(str((job or {}).get("last_error") or "")
                                        or "A última transcrição falhou — veja "
                                           "Ferramentas → Ver fila de processamento.")
                    self.interview_table.setItem(row, column, item)
            self.interview_table.blockSignals(False)
            self.interview_table.setSortingEnabled(not manual_order_active)
            if manual_order_active:
                self.interview_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            has_rows = len(self.statuses) > 0
            self.interview_table.setVisible(has_rows)
            if hasattr(self, "_empty_state_widget"):
                self._update_empty_state_mode()
                self._empty_state_widget.setVisible(not has_rows)
                self.interview_table.setVisible(True)
            self._update_add_media_emphasis(has_rows)
            self._update_engine_offer_banner()
            self._update_diar_offer_banner()
            # R4: aba Propriedades visivel acompanha o estado novo (o
            # metodo preserva o form quando ha edicao nao salva).
            if (getattr(self, "_props_tab", None) is not None
                    and self.review_tabs.currentWidget() is self._props_tab):
                self._refresh_props_panel()
            self._apply_interview_filter()
            self.update_action_states()

        def _review_has_user_edits(self, interview_id: str) -> bool:
            """Mesmo criterio de app_service.refresh_unedited_reviews:
            a chave "edits" no JSON da transcricao editavel."""
            if self.context is None:
                return False
            caminho = self.context.paths.review_dir / "edits" / f"{interview_id}.review.json"
            if not caminho.exists():
                return False
            try:
                dados = json.loads(caminho.read_text(encoding="utf-8"))
            except Exception:
                return True  # ilegivel: nao arriscar sobrescrever
            return bool(dados.get("edits"))

        def _update_diar_offer_banner(self) -> None:
            """Oferta da lista (R3): entrevistas transcritas sem separacao
            de vozes, com o recurso ativo nesta maquina e sem edicoes
            humanas — um clique completa em lote, sem transcrever de novo."""
            if not hasattr(self, "diar_offer_banner"):
                return
            ids: list[str] = []
            busy = bool(self.worker and self.worker.isRunning())
            if (self.context is not None and not busy
                    and app_service.diarize_effective(self.context.config or {})[0]):
                brutos = diar_offer_candidates(
                    self.statuses, edited_ids=set(), channel_ids=set())
                # As duas exclusoes caras (canais e edicoes) so rodam para
                # os candidatos brutos — refresh e chamado de ~15 lugares.
                ids = [iid for iid in brutos
                       if not self._channels_diarization_exists(iid)
                       and not self._review_has_user_edits(iid)]
            self._diar_offer_ids = ids
            if ids:
                plural = "s" if len(ids) > 1 else ""
                self.diar_offer_label.setText(
                    f"🗣 {len(ids)} entrevista{plural} transcrita{plural} ainda "
                    "sem separação de vozes — dá para separar sem transcrever "
                    "de novo.")
            self.diar_offer_banner.setVisible(bool(ids))

        def _on_diar_offer_clicked(self) -> None:
            ids = list(getattr(self, "_diar_offer_ids", []) or [])
            if ids:
                self.run_diarization_job(ids=ids)

        def _update_engine_offer_banner(self) -> None:
            """Faixa da lista: TAGARELA para maquinas sem GPU cujo projeto
            ainda transcreve com o Whisper (migracao visivel, 2026-09-02)."""
            if not hasattr(self, "engine_offer_banner"):
                return
            visivel = False
            if self.context is not None:
                from . import runtime as _runtime_b, model_manager as _mm_b
                flag = _runtime_b.app_data_dir() / "parakeet_cpu_offer_dismissed.flag"
                device = _runtime_b.resolve_device(
                    str(self.context.config.get("asr_device") or "auto"))[0]
                motor = str(self.context.config.get("asr_model") or "")
                engine = (_mm_b.ASR_VARIANTS.get(motor) or {}).get("engine")
                busy = bool(self.worker and self.worker.isRunning())
                # Projeto em outro idioma: o TAGARELA nao serve — sem faixa.
                idioma_fora = bool(languages_outside_pt([str(self.context.config.get("asr_language") or "")]))
                visivel = (not idioma_fora) and engine_offer_due(
                    device, engine, sys.platform, self._engine_offer_declined(flag), busy)
            self.engine_offer_banner.setVisible(visivel)

        def _busy_state_text(self) -> str:
            """Texto atual do lote (a barra de baixo), so enquanto ha worker."""
            if not (self.worker and self.worker.isRunning()):
                return ""
            return str(self.progress_label.text() if hasattr(self, "progress_label") else "")

        def _sync_busy_hints(self, busy: bool | None = None) -> None:
            """Faixa da lista + linha do menu Analisar acompanham o lote (2026-09-02)."""
            if busy is None:
                busy = bool(self.worker and self.worker.isRunning())
            estado = self._busy_state_text() if busy else ""
            if hasattr(self, "busy_hint_banner"):
                if busy and self.context is not None:
                    # Nao sobrescrever a resposta a um clique enquanto ela pisca.
                    if not getattr(self, "_busy_click_active", False):
                        self.busy_hint_label.setText(busy_hint_text(estado))
                    self.busy_hint_banner.setVisible(True)
                else:
                    self.busy_hint_banner.setVisible(False)
            if busy and hasattr(self, "voice_batch_banner"):
                # Durante um lote a faixa de vozes sai de cena (as acoes
                # dela abririam transcricoes que o lote esta recriando).
                self.voice_batch_banner.setVisible(False)
            if hasattr(self, "busy_menu_hint_action"):
                if busy:
                    resumo = estado.split(" · ")[0] if estado else ""
                    detalhe = f" ({resumo})" if resumo else ""
                    self.busy_menu_hint_action.setText(
                        f"⏳ Aguardando o lote terminar{detalhe} — as análises com AI voltam sozinhas")
                self.busy_menu_hint_action.setVisible(busy)
                self.busy_menu_hint_separator.setVisible(busy)

        def _explain_busy(self, acao: str) -> bool:
            """True (e explica na faixa) quando uma acao foi acionada durante um
            lote: nada executa, nada modal — a faixa mostra o motivo e pisca."""
            if not (self.worker and self.worker.isRunning()):
                return False
            texto = busy_click_text(acao, self._busy_state_text())
            if hasattr(self, "busy_hint_banner"):
                self._busy_click_active = True
                self.busy_hint_label.setText(texto)
                self.busy_hint_banner.setStyleSheet(
                    f"QFrame {{ {ui_tokens.banner_style(ui_tokens.WARN)} }}")
                self.busy_hint_banner.setVisible(True)

                def _volta() -> None:
                    self._busy_click_active = False
                    if hasattr(self, "busy_hint_banner"):
                        self.busy_hint_banner.setStyleSheet(
                            f"QFrame {{ {ui_tokens.banner_style(ui_tokens.INFO)} }}")
                    self._sync_busy_hints()

                QTimer.singleShot(1500, _volta)
            else:
                self.progress_label.setText(texto)
            return True

        # --- servidor de separacao de falantes do lote (B1, 2026-09-02) ---
        def _start_diarize_server(self, n_files: int) -> None:
            """Um processo `diarize-serve` por LOTE em vez de um `diarize` por
            arquivo: o modelo carrega uma vez, em paralelo com o preparo e a
            transcricao do 1o arquivo (start() nao espera). Lote de 1
            arquivo segue na rota antiga — nada a ganhar."""
            self._stop_diarize_server()
            if n_files < 2 or self.context is None:
                return
            try:
                from .diarize_client import DiarizeServer
                server = DiarizeServer(self.context.paths.project_root)
                if server.start():
                    self._diarize_server = server
            except Exception as exc:  # noqa: BLE001 - sem servidor = rota por arquivo
                _logger.warning("servidor de diarizacao nao abriu: %s", exc)

        def _stop_diarize_server(self) -> None:
            server = getattr(self, "_diarize_server", None)
            self._diarize_server = None
            if server is not None:
                try:
                    server.stop()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("servidor de diarizacao nao encerrou limpo: %s", exc)

        def _release_batch_models(self) -> None:
            """Solta os modelos que ficam vivos ENTRE arquivos de um lote.

            O TAGARELA em CPU custa 2,2 GB e passou a ser carregado uma vez por
            lote (2026-09-05) — sem soltar ao fim, o app ficaria segurando isso
            enquanto a pessoa revisa. Chamado nos mesmos tres pontos do
            servidor de diarizacao: fim, falha e fechar a janela."""
            try:
                from . import parakeet_runner as _pk_rel

                _pk_rel.release_cpu_model()
            except Exception as exc:  # noqa: BLE001 - soltar memoria nunca derruba nada
                _logger.warning("nao consegui soltar o modelo de transcricao: %s", exc)

        # --- preparo dos audios em paralelo (B2, 2026-09-02) ---
        def _prepare_batch(
            self,
            ids: list[str],
            start_progress: int,
            end_progress: int,
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Um passo so para o lote, duas conversoes ffmpeg por vez.

            Quem falha entra em self._prepare_failed e o passo de
            transcricao DAQUELE arquivo levanta o erro — skip-and-continue
            do worker, como sempre; os demais seguem. jobs.json e atualizado
            SO por esta thread: as threads do pool apenas convertem.
            """
            from concurrent.futures import ThreadPoolExecutor, as_completed
            self._prepare_failed = set()
            context = self.context
            total = len(ids)
            agora = datetime.now().isoformat(timespec="seconds")
            for iid in ids:
                app_service.update_job(context, iid, {
                    "status": "Rodando", "stage": "preparar audio", "progress": start_progress,
                    "started_at": agora, "last_error": "", "estimated_finish_at": ""})

            def _um(iid: str) -> tuple[str, int]:
                try:
                    return iid, int(app_service.prepare_interviews(context, ids=[iid]).failures)
                except Exception as exc:  # noqa: BLE001 - falha do arquivo, nao do lote
                    _logger.warning("preparo do audio de %s falhou: %s", iid, exc)
                    return iid, 1

            def _emit(done: int) -> None:
                if progress_callback is not None:
                    progress_callback({
                        "event": "prepare_progress",
                        "progress": int(100 * done / max(1, total)),
                        "message": f"Preparando os áudios ({done} de {total})...",
                    })

            _emit(0)
            done = 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(_um, iid): iid for iid in ids}
                for future in as_completed(futures):
                    iid, failures = future.result()
                    done += 1
                    if failures:
                        self._prepare_failed.add(iid)
                    else:
                        app_service.update_job(context, iid, {
                            "status": "Rodando", "stage": "preparar audio", "progress": end_progress})
                    _emit(done)
                    if should_cancel is not None and should_cancel():
                        for pendente in futures:
                            pendente.cancel()
                        break
            return app_service.JobResult("prepare-audio", 0)

        def _transcribe_prepared(
            self,
            interview_id: str,
            asr_model: str,
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Passo de transcricao de um arquivo do lote: recusa quem falhou
            no preparo em paralelo — o worker pula o resto DESTE arquivo e
            segue com os outros."""
            if interview_id in getattr(self, "_prepare_failed", set()):
                raise RuntimeError(
                    "não foi possível converter o áudio para WAV (ffmpeg) — "
                    "veja a fila de processamento em Ferramentas")
            return app_service.transcribe_interviews(
                self.context,
                ids=[interview_id],
                # asr_model efetivo SEMPRE no override: igual a config no
                # fluxo normal; o escolhido na rodada de "Transcrever
                # novamente…".
                overrides={"diarize": False, "asr_model": asr_model},
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )

        # --- vozes por identificar ao fim do lote (C, 2026-09-02) ---
        def _turns_for_voice_check(self, interview_id: str) -> list[dict[str, Any]]:
            if interview_id == self.current_interview_id and self.review:
                return list(self.turns)
            status = self.status_by_interview_id(interview_id)
            if status is None or not status.review_exists or self.context is None:
                return []
            return review_store.review_turns(
                app_service.load_review(self.context, interview_id, create=False))

        def _update_voice_batch_banner(self) -> None:
            """Recalcula as pendentes do ultimo lote e mostra/esconde a faixa."""
            if not hasattr(self, "voice_batch_banner"):
                return
            ids = list(getattr(self, "_voice_batch_ids", []) or [])
            busy = bool(self.worker and self.worker.isRunning())
            pendentes: list[str] = []
            if ids and self.context is not None and not busy:
                pendentes = voice_naming_pending(
                    ids, self.context.config, self.context.metadata, self._turns_for_voice_check)
            self._voice_batch_ids = pendentes
            if pendentes:
                n = len(pendentes)
                plural = "s" if n > 1 else ""
                self.voice_batch_label.setText(
                    f"🎙 {n} entrevista{plural} com vozes por identificar — diga "
                    f"quem fala em cada uma, em sequência.")
            self.voice_batch_banner.setVisible(bool(pendentes))

        def _on_voice_batch_identify(self) -> None:
            """Percorre as pendentes: abre a transcricao e pergunta "De quem é
            esta voz?". Cancelar no dialogo interrompe a sequencia; a faixa
            segue com o restante."""
            if self._explain_busy("Identificar vozes"):
                return
            for iid in list(getattr(self, "_voice_batch_ids", []) or []):
                self.open_review(iid)
                if self.current_interview_id != iid:
                    break
                if self._review_has_speaker_edits():
                    self._persist_confirmed_from_edits(iid)
                    continue
                if not self.open_voice_naming_dialog():
                    break
                self._update_voice_banner()
            self._update_voice_batch_banner()

        def _on_voice_batch_later(self) -> None:
            self._voice_batch_ids = []
            if hasattr(self, "voice_batch_banner"):
                self.voice_batch_banner.setVisible(False)

        def _on_engine_offer_accept(self) -> None:
            if self.context is None:
                return
            if not self._switch_engine_to_parakeet(machine_default=True):
                return
            self.engine_offer_banner.setVisible(False)
            self.refresh_interviews()
            # Baixar JA: sem isto o proximo Transcrever cairia no modelo
            # antigo em silencio (resolve_asr_model substitui o nao baixado).
            self.ensure_models_ready(asr_variants=["parakeet-pt"], retry=self.refresh_interviews)

        def _on_engine_offer_decline(self) -> None:
            from . import runtime as _runtime_d
            flag = _runtime_d.app_data_dir() / "parakeet_cpu_offer_dismissed.flag"
            try:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text("dismissed", encoding="utf-8")
            except OSError:
                pass
            self.engine_offer_banner.setVisible(False)

        def _batch_estimate_text(self, ids: list[str], asr_model: str, device: str) -> str | None:
            """Estimativa honesta do lote em maquina sem GPU (None sem duracoes)."""
            from . import capabilities as _caps_e, model_manager as _mm_e, runtime as _runtime_e
            total = 0.0
            for iid in ids:
                status = self.status_by_interview_id(iid)
                try:
                    total += float(getattr(status, "duration_sec", "") or 0)
                except (TypeError, ValueError):
                    pass
            if total <= 0:
                return None
            engine = (_mm_e.ASR_VARIANTS.get(asr_model) or {}).get("engine")
            asr_dev = device
            if engine == "parakeet_onnx" and device == "cuda":
                from . import parakeet_runner as _pk_e
                asr_dev = _pk_e.planned_device(device)   # CPU ate o onnx-gpu existir
            # Historico DESTA maquina: depois das primeiras transcricoes ele
            # vale mais que qualquer formula (a formula nao enxerga o clock nem
            # a reducao termica de cada nucleo).
            asr_hist: list[tuple[float, float]] = []
            diar_hist: list[tuple[float, float]] = []
            try:
                entradas = project_store.read_jobs_log(self.context.paths)
                duracoes = {}
                for linha in (self.context.rows or []):
                    try:
                        duracoes[str(linha.get("interview_id"))] = float(linha.get("duration_sec") or 0)
                    except (TypeError, ValueError):
                        pass
                asr_hist = project_store.stage_samples(entradas, "transcribe", duracoes)
                diar_hist = project_store.stage_samples(entradas, "diarize", duracoes)
            except Exception as exc:  # noqa: BLE001 - sem historico, vale a formula
                _logger.debug("Sem historico para a estimativa: %s", exc)
            asr_s, diar_s = _caps_e.batch_time_estimate(
                total, engine, device, _runtime_e.cpu_cores(), asr_device=asr_dev,
                asr_samples=asr_hist, diar_samples=diar_hist)
            medido = _caps_e.estimate_is_measured(asr_hist)
            onde = "sem placa de vídeo" if device != "cuda" else "com placa de vídeo"
            # Ate 2026-09-05 este texto dizia que a separacao era "a etapa
            # demorada" — a formula supunha escala linear com nucleos e a
            # inflava por ~2x. Medida, ela e a metade barata, e adia-la economiza
            # menos do que a frase antiga prometia. A dica agora diz quanto.
            dica = ("A separação é a etapa demorada — dá para deixá-la para depois "
                    "(a lista oferece um botão) e já revisar o texto."
                    if diar_s > asr_s else
                    "A transcrição é a etapa demorada. Deixar a separação de falantes "
                    f"para depois (a lista oferece um botão) adianta cerca de "
                    f"{_caps_e.describe_seconds(diar_s)}.")
            base = ("com base no que este computador já levou nas transcrições anteriores"
                    if medido else "estimativa aproximada — ela se ajusta depois da primeira transcrição")
            return (f"Neste computador ({onde}), para {_caps_e.describe_seconds(total)} "
                    f"de áudio: transcrição ≈ {_caps_e.describe_seconds(asr_s)} · separação de "
                    f"falantes ≈ {_caps_e.describe_seconds(diar_s)} ({base}).\n{dica}")

        def _apply_interview_filter(self) -> None:
            """Hide/show table rows based on status combo and text search."""
            if not hasattr(self, "filter_status_combo"):
                return
            status_filter = self.filter_status_combo.currentText()
            # Mesmo normalizador da busca no texto: "joao" acha "Entrevista João"
            # (sem acentos, sem caixa, NFC/NFD) — auditoria 2026-09-02.
            from . import search as _search_f
            text_filter = _search_f.normalize(self.filter_text_edit.text().strip())
            visible_count = 0
            for row_idx in range(self.interview_table.rowCount()):
                id_item = self.interview_table.item(row_idx, COL_ARQUIVO)
                state_item = self.interview_table.item(row_idx, COL_TRANSCRICAO)
                if not id_item or not state_item:
                    continue
                real_id = _search_f.normalize(str(id_item.data(Qt.ItemDataRole.UserRole) or ""))
                displayed_text = _search_f.normalize(id_item.text())
                state_text = state_item.text()
                show_by_status = True
                if status_filter == "Transcritas":
                    show_by_status = state_text == "Transcrita"
                elif status_filter == "Pendentes":
                    # Arquivo com falha continua pendente de transcricao.
                    show_by_status = state_text in ("Não transcrita", "Falha")
                elif status_filter == "Processando":
                    # Em andamento: "Na fila" ou "<etapa> N%" (row_progress_text)
                    show_by_status = state_text == "Na fila" or state_text.endswith("%")
                show_by_text = (text_filter in real_id or text_filter in displayed_text) if text_filter else True
                hidden = not (show_by_status and show_by_text)
                self.interview_table.setRowHidden(row_idx, hidden)
                if not hidden:
                    visible_count += 1
            total = self.interview_table.rowCount()
            if text_filter or status_filter != "Todas":
                self.progress_label.setText(f"{visible_count} de {total} entrevista(s) visível(eis).")
            else:
                self.progress_label.setText(f"{total} entrevista(s) na lista." if total else "Nenhuma entrevista na lista.")

        def friendly_state(self, status: Any, job: dict[str, Any] | None = None) -> str:
            job = job or {}
            em_andamento = row_progress_text(job)
            if em_andamento is not None:
                return em_andamento
            if status.review_exists or status.canonical_exists:
                # Falha em RETRANSCRICAO nao esconde transcricao utilizavel.
                return "Transcrita"
            if job.get("status") == "Falha":
                # Antes aparecia como "Não transcrita" e o erro so existia
                # na Fila de tarefas.
                return "Falha"
            return "Não transcrita"

        def selected_interview_id(self) -> str | None:
            ids = self.selected_interview_ids()
            return ids[0] if ids else None

        def selected_interview_ids(self) -> list[str]:
            """Return IDs of checked (checkbox) interviews, in visual order."""
            if not self._checked_ids:
                return []
            ids: list[str] = []
            for row in range(self.interview_table.rowCount()):
                if self.interview_table.isRowHidden(row):
                    continue
                item = self.interview_table.item(row, COL_ARQUIVO)
                if not item:
                    continue
                iid = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
                if iid in self._checked_ids:
                    ids.append(iid)
            return ids

        def _visible_interview_ids_in_order(self) -> list[str]:
            ids: list[str] = []
            for row in range(self.interview_table.rowCount()):
                if self.interview_table.isRowHidden(row):
                    continue
                item = self.interview_table.item(row, COL_ARQUIVO)
                if not item:
                    continue
                ids.append(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
            return ids

        def _visually_selected_interview_ids(self) -> set[str]:
            """Ids das linhas com selecao visual. Ignora linhas ocultas por filtro."""
            ids: set[str] = set()
            for index in self.interview_table.selectionModel().selectedRows(COL_ARQUIVO):
                row = index.row()
                if self.interview_table.isRowHidden(row):
                    continue
                item = self.interview_table.item(row, COL_ARQUIVO)
                if item:
                    iid = item.data(Qt.ItemDataRole.UserRole) or item.text()
                    if iid:
                        ids.add(str(iid))
            return ids

        def destructive_target_ids(self, cursor_row: int | None = None) -> list[str]:
            """Alvo de acoes destrutivas: so selecao visual/cursor (S5)."""
            cursor_row_id: str | None = None
            if cursor_row is not None and cursor_row >= 0:
                item = self.interview_table.item(cursor_row, COL_ARQUIVO)
                if item:
                    cursor_row_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
            return _compute_destructive_target_ids(
                self._visible_interview_ids_in_order(),
                self._visually_selected_interview_ids(),
                cursor_row_id,
            )

        def effective_target_ids(self, cursor_row: int | None = None) -> list[str]:
            """Targets for actions, following Windows Explorer precedence.

            See _compute_effective_target_ids for the rules.
            Fallback: se nada selecionado mas editor aberto, usa current_interview_id.
            Fallback: se nada selecionado mas ha currentItem na tabela, usa esse.
            """
            cursor_row_id: str | None = None
            if cursor_row is not None and cursor_row >= 0:
                item = self.interview_table.item(cursor_row, COL_ARQUIVO)
                if item:
                    cursor_row_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
            result = _compute_effective_target_ids(
                self._visible_interview_ids_in_order(),
                set(self._checked_ids),
                self._visually_selected_interview_ids(),
                cursor_row_id,
            )
            if result:
                return result
            # Fallback 1: arquivo aberto no editor
            if self.current_interview_id:
                return [self.current_interview_id]
            # Fallback 2: currentItem (cursor de teclado, mesmo sem linha "selecionada")
            current = self.interview_table.currentItem()
            if current is not None:
                row = current.row()
                if not self.interview_table.isRowHidden(row):
                    arq_item = self.interview_table.item(row, COL_ARQUIVO)
                    if arq_item:
                        iid = arq_item.data(Qt.ItemDataRole.UserRole) or arq_item.text()
                        if iid:
                            return [str(iid)]
            return []

        def pending_transcription_ids(self) -> list[str]:
            return [
                status.interview_id
                for status in self.statuses
                if not (status.review_exists or status.canonical_exists)
            ]

        def add_audio_folder(self) -> None:
            if not self._require_project("Adicionar pasta"):
                return
            folder = QFileDialog.getExistingDirectory(self, "Escolha uma pasta com áudios ou vídeos", self._browse_dir())
            if not folder:
                return
            try:
                self.context = app_service.add_audio_root(self.context, Path(folder))
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível adicionar a pasta", sanitize_message(str(exc)))
                return
            self.refresh_interviews()
            QMessageBox.information(
                self,
                "Pasta adicionada",
                "A pasta foi adicionada como fonte de mídia. Arquivos com o mesmo ID/nome aparecem uma vez como selecionados; cópias concorrentes ficam marcadas como duplicatas no registro interno.",
            )

        def new_project(self, *_args: Any) -> None:
            if not self.save_current_turn():
                return
            dialog = NewProjectDialog(self, initial_dir=self._browse_dir())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.refresh_interviews()  # cancelou: empty-state, nunca tela morta
                return
            project_root = dialog.project_root()
            name = dialog.project_name()
            try:
                context = app_service.create_project(project_root, project_name=name)
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível criar o projeto", sanitize_message(str(exc)))
                return
            self.switch_project_context(context)
            self.progress_label.setText(
                "Projeto criado. Use o botão + Adicionar mídia para começar — "
                "suas gravações continuam onde estão.")

        def open_project(self) -> None:
            if not self.save_current_turn():
                return
            from .project_store import PROJECT_EXTENSION
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Abrir projeto",
                self._browse_dir(),
                f"Projetos Transcritório (*{PROJECT_EXTENSION});;Todos os arquivos (*)",
            )
            if not file_path:
                return
            try:
                context = app_service.open_project(Path(file_path))
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível abrir o projeto", sanitize_message(str(exc)))
                return
            self.switch_project_context(context)
            self.progress_label.setText("Projeto aberto.")

        def switch_project_context(self, context: app_service.ProjectContext) -> None:
            self.player.stop()
            self._sync_video_panel(False)  # video do projeto anterior
            self.context = context
            from . import recent_projects
            recent_projects.save_recent(context.paths.project_root)
            # R4: sucesso anunciado pertence ao projeto ANTERIOR.
            if getattr(self, "docs_panel", None) is not None:
                self.docs_panel.clear_success()
            # Janelas cacheadas com estado POR PROJETO (temas e códigos): sem
            # esquecer, uma ação depois da troca gravaria no projeto errado.
            temas = getattr(self, "_themes_dialog", None)
            if temas is not None:
                temas.reset_for_project()
            self.review = None
            self.current_interview_id = None
            self.current_turn_id = None
            self.current_play_row = None
            self.media_candidates = []
            self.media_candidate_index = 0
            self.turns = []
            self.review_title.setText("Abra um arquivo para editar a transcrição.")
            self.turn_table.setRowCount(0)
            self.waveform_widget.set_waveform([], 0)
            self.text_edit.clear()
            self.set_editor_enabled(False)
            self.undo_stack.clear()
            # Estado por-projeto: checkboxes e pilhas de undo/redo da lixeira
            # nao podem sobreviver a troca (Ctrl+Z restauraria no projeto errado).
            self._checked_ids.clear()
            self._trash_undo.clear()
            self._trash_redo.clear()
            self.set_save_state("Projeto aberto.")
            self.refresh_interviews()

        def add_audio_files(self) -> None:
            if not self._require_project("Adicionar arquivos"):
                return
            extensions = " ".join(f"*{ext}" for ext in self.context.config.get("media_extensions", []))
            files, _filter = QFileDialog.getOpenFileNames(
                self,
                "Escolha arquivos de áudio ou vídeo",
                self._browse_dir(),
                f"Mídia ({extensions});;Todos os arquivos (*)",
            )
            if not files:
                return
            self._ingest_media_paths([Path(path) for path in files])

        def _ingest_media_paths(self, paths: list[Path]) -> None:
            """Add a list of media files to the current project.

            Shared by the Add files dialog and the drag-and-drop handler.
            Silently expands directories into their direct children.
            """
            if not paths:
                return
            if not self._require_project("Adicionar arquivos"):
                return
            allowed = {ext.lower() for ext in self.context.config.get("media_extensions", [])}
            expanded: list[Path] = []
            skipped_dirs: list[str] = []
            for entry in paths:
                if entry.is_dir():
                    try:
                        children = list(entry.iterdir())
                    except OSError:
                        # Pasta sem permissao/drive desconectado no drag-and-drop:
                        # avisar em vez de estourar dentro do dropEvent.
                        skipped_dirs.append(entry.name)
                        continue
                    for child in children:
                        if child.is_file() and (not allowed or child.suffix.lower() in allowed):
                            expanded.append(child)
                elif entry.is_file():
                    if not allowed or entry.suffix.lower() in allowed:
                        expanded.append(entry)
            if skipped_dirs:
                QMessageBox.warning(
                    self,
                    "Pasta inacessível",
                    "Não foi possível ler: " + ", ".join(skipped_dirs),
                )
            if not expanded:
                QMessageBox.information(
                    self,
                    "Nenhum arquivo compatível",
                    "Os itens arrastados não contêm arquivos de áudio ou vídeo "
                    "em formatos reconhecidos pelo Transcritório.",
                )
                return
            try:
                self.context = app_service.add_audio_files(self.context, expanded)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Não foi possível adicionar os arquivos",
                    sanitize_message(str(exc)),
                )
                return
            self.refresh_interviews()
            # R4: o modal "Arquivos adicionados" era um beco (nao apontava
            # o proximo passo). CTA nao-modal na statusbar; o refresh acima
            # ja deixou o Transcrever primary quando tudo esta pendente.
            plural = "s" if len(expanded) > 1 else ""
            self.progress_label.setText(
                f"{len(expanded)} arquivo{plural} adicionado{plural}. "
                "Clique em Transcrever para começar.")

        def dragEnterEvent(self, event) -> None:
            # R4: aceitar TAMBEM sem projeto — o empty state convida a
            # arrastar, e o drop ignorado em silencio era um beco.
            mime = event.mimeData()
            if mime is not None and mime.hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()

        def dragMoveEvent(self, event) -> None:
            mime = event.mimeData()
            if mime is not None and mime.hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event) -> None:
            mime = event.mimeData()
            if mime is None or not mime.hasUrls():
                event.ignore()
                return
            paths: list[Path] = []
            for url in mime.urls():
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))
            if not paths:
                event.ignore()
                return
            event.acceptProposedAction()
            if self.context is None:
                self._offer_project_for_dropped(paths)
                return
            self._ingest_media_paths(paths)

        def _offer_project_for_dropped(self, paths: list[Path]) -> None:
            """Drop sem projeto aberto (R4): oferecer criar o projeto e
            ingerir os arquivos arrastados em seguida. `paths` e local —
            nunca vaza para um drop ou projeto seguinte."""
            plural = "ns" if len(paths) > 1 else "m"
            answer = QMessageBox.question(
                self,
                "Criar projeto para estas gravações?",
                f"Você arrastou {len(paths)} ite{plural}, mas ainda não há "
                "projeto aberto.\n\nCriar um projeto agora? As gravações "
                "são adicionadas a ele em seguida — e continuam onde estão.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.new_project()
            if self.context is not None:
                self._ingest_media_paths(paths)

        def open_project_folder(self) -> None:
            if not self._require_project("Abrir pasta do projeto"):
                return
            open_folder_in_explorer(self.context.paths.project_root)

        def apply_metadata_to_selected(self) -> None:
            # R4: effective_target_ids (regra Explorer) no lugar de
            # checked-only — a acao habilitava por um criterio e o handler
            # exigia outro, e o clique com selecao visual so repreendia.
            ids = self.effective_target_ids()
            if not ids:
                QMessageBox.information(self, "Selecione arquivos", "Selecione um ou mais arquivos do projeto.")
                return
            dialog = MetadataDialog(len(ids), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            updates = dialog.updates()
            if not updates:
                QMessageBox.information(self, "Nada para aplicar", "Marque pelo menos um campo para alterar.")
                return
            self._apply_metadata_updates(ids, updates)
            self.progress_label.setText(f"Propriedades atualizadas em {len(ids)} arquivo(s).")

        def _apply_metadata_updates(self, ids: list[str], updates: dict[str, str]) -> None:
            """Persistencia + side-effects compartilhados entre o dialogo de
            lote e o salvar da aba Propriedades (R4)."""
            if "speaker_labels" in updates:
                # Rotulos escolhidos explicitamente contam como confirmacao
                # humana das vozes (plano D2.5, item 9).
                updates["speakers_confirmed"] = "true"
            if "speaker_mode" in updates:
                # Configuracao explicita de falantes dispensa o "Quantas
                # pessoas falam?" na transcricao (plano D3.1).
                updates["speaker_setup"] = "true"
            self.context = app_service.update_file_metadata(self.context, ids, updates)
            self.refresh_interviews()
            if "speaker_labels" in updates:
                self._offer_rerender_after_label_change(ids)

        def _offer_rerender_after_label_change(self, ids: list[str]) -> None:
            """Rotulos por arquivo so aparecem nos documentos apos remontar a
            transcricao (o mapeamento acontece no render) — oferecer isso na
            hora, em vez de exigir que o usuario descubra o menu (plano D2.3)."""
            transcribed = []
            for interview_id in ids:
                status = self.status_by_interview_id(interview_id)
                if status and (status.canonical_exists or status.review_exists):
                    transcribed.append(interview_id)
            if not transcribed:
                return
            if self.worker and self.worker.isRunning():
                self.progress_label.setText(
                    "Rótulos salvos. Uma tarefa está em andamento — para aplicá-los aos documentos, salve os rótulos de novo quando ela terminar."
                )
                return
            answer = QMessageBox.question(
                self,
                "Aplicar os novos rótulos?",
                f"{len(transcribed)} arquivo(s) já transcrito(s). Aplicar os novos rótulos de falantes aos documentos agora?\n\n"
                "Transcrições com edições manuais mantêm as edições (nelas, use o botão \"Aplicar a todos desta voz\" no editor).",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.start_worker(
                "Aplicar rótulos de falantes",
                [
                    *[
                        (f"Remontando transcricao ({item})...",
                         lambda item=item: app_service.render_interviews(
                             self.context, ids=[item],
                             overrides=self._render_source_overrides(item)))
                        for item in transcribed
                    ],
                    (
                        "Atualizando transcrições editáveis...",
                        lambda: app_service.refresh_unedited_reviews(self.context, transcribed),
                    ),
                ],
            )

        def _on_interview_cell_clicked(self, row: int, column: int) -> None:
            if column == COL_CHECK:
                self._toggle_row_check(row)
            else:
                item = self.interview_table.item(row, COL_ARQUIVO)
                if item:
                    self.open_review(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))

        def _toggle_row_check(self, row: int) -> None:
            item = self.interview_table.item(row, COL_ARQUIVO)
            if not item:
                return
            interview_id = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
            check_item = self.interview_table.item(row, COL_CHECK)
            if not check_item:
                return
            if interview_id in self._checked_ids:
                self._checked_ids.discard(interview_id)
                check_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                self._checked_ids.add(interview_id)
                check_item.setCheckState(Qt.CheckState.Checked)
            self.update_action_states()

        def _on_header_section_clicked(self, section: int) -> None:
            if section != COL_CHECK:
                # Click em cabecalho de coluna de dados: desativar ordem manual se estava ativa
                if self.context and self.context.project.get("manual_order_active"):
                    self.context = app_service.set_interview_order(
                        self.context,
                        list(self.context.project.get("interview_order") or []),
                        manual_active=False,
                    )
                    self.interview_table.setSortingEnabled(True)
                    self.progress_label.setText("Ordem manual desativada. Ordenando por coluna.")
                return
            visible_ids: list[str] = []
            for row in range(self.interview_table.rowCount()):
                if self.interview_table.isRowHidden(row):
                    continue
                item = self.interview_table.item(row, COL_ARQUIVO)
                if item:
                    visible_ids.append(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
            all_checked = all(vid in self._checked_ids for vid in visible_ids) if visible_ids else False
            self.interview_table.blockSignals(True)
            for row in range(self.interview_table.rowCount()):
                if self.interview_table.isRowHidden(row):
                    continue
                item = self.interview_table.item(row, COL_ARQUIVO)
                check_item = self.interview_table.item(row, COL_CHECK)
                if not item or not check_item:
                    continue
                iid = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
                if all_checked:
                    self._checked_ids.discard(iid)
                    check_item.setCheckState(Qt.CheckState.Unchecked)
                else:
                    self._checked_ids.add(iid)
                    check_item.setCheckState(Qt.CheckState.Checked)
            self.interview_table.blockSignals(False)
            self.update_action_states()

        def open_selected_review(self) -> None:
            # Abrir a linha ATIVA (cursor), nao o primeiro checkbox marcado:
            # Enter/duplo-clique agem sobre a linha em foco, como no Explorer.
            row = self.interview_table.currentRow()
            if row >= 0 and not self.interview_table.isRowHidden(row):
                item = self.interview_table.item(row, COL_ARQUIVO)
                if item:
                    self.open_review(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
                    return
            interview_id = self.selected_interview_id()
            if not interview_id:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista na lista.")
                return
            self.open_review(interview_id)

        def open_word_search(self, query: str = "") -> None:
            """Janela de busca de palavras (Ctrl+Shift+F / botao / menu)."""
            if self.context is None:
                QMessageBox.information(self, "Abra um projeto", "Abra um projeto para buscar nas transcrições.")
                return
            if getattr(self, "_word_search_dialog", None) is None:
                self._word_search_dialog = WordSearchDialog(self)
            dialog = self._word_search_dialog
            dialog.show()
            dialog.raise_()
            if query:
                dialog.set_query(query)
            dialog.query_input.setFocus()

        def open_explore(self) -> None:
            """Janela Buscar por sentido e perguntar (botao da barra / menu)."""
            if self._explain_busy("Buscar por sentido e perguntar"):
                return
            if self.context is None:
                QMessageBox.information(self, "Abra um projeto", "Abra um projeto para explorar as entrevistas.")
                return
            if getattr(self, "_explore_dialog", None) is None:
                self._explore_dialog = ExploreDialog(self)
            else:
                # Dialogo cacheado: re-anunciar o estado (modelos podem
                # ter sido baixados/removidos desde a ultima abertura).
                self._explore_dialog._announce_readiness()
            self._explore_dialog.show()
            self._explore_dialog.raise_()
            self._explore_dialog.query_input.setFocus()

        def open_themes(self) -> None:
            """Janela Temas das entrevistas (Parte B) — cacheada como a Perguntar."""
            if self._explain_busy("Temas das entrevistas"):
                return
            if self.context is None:
                QMessageBox.information(self, "Abra um projeto", "Abra um projeto para ver os temas das entrevistas.")
                return
            if getattr(self, "_themes_dialog", None) is None:
                self._themes_dialog = ThemesDialog(self)
            else:
                self._themes_dialog._announce_readiness()
            self._themes_dialog.show()
            self._themes_dialog.raise_()

        def open_search_hit(self, interview_id: str, start: float) -> None:
            """Abre a entrevista no bloco mais proximo do tempo dado (busca)."""
            if interview_id != self.current_interview_id:
                self.open_review(interview_id)
                if self.current_interview_id != interview_id:
                    return  # abertura falhou/cancelada
            if not self.turns:
                return
            target = min(
                range(len(self.turns)),
                key=lambda i: abs(float(self.turns[i].get("start", 0) or 0) - start),
            )
            self.select_turn_by_index(target, seek=True)
            item = self.turn_table.item(target, 0)
            if item is not None:
                self.turn_table.scrollToItem(item)

        def _review_title_text(self, interview_id: str) -> str:
            """Titulo do painel com o modelo que PRODUZIU esta transcricao
            (review.transcript.asr_model, gravado pelo render). O cabecalho
            da janela mostra o modelo CONFIGURADO para as proximas — com
            "Transcrever novamente" e mais de um modelo instalado, os dois
            podem divergir e o usuario precisa ver qual gerou o texto."""
            modelo = str((((self.review or {}).get("transcript") or {}).get("asr_model")) or "")
            if not modelo:
                return f"Transcrição: {interview_id}"
            from . import model_manager as _mm
            rotulo = str((_mm.ASR_VARIANTS.get(modelo) or {}).get("label") or modelo)
            return f"Transcrição: {interview_id}  ·  modelo {rotulo}"

        def open_review(self, interview_id: str) -> None:
            if not self.save_current_turn():
                return
            status = self.status_by_interview_id(interview_id)
            if status is None:
                # Lista desatualizada (arquivo removido/renomeado fora): sem
                # este guard, load_review criava uma review vazia e o
                # get_media_candidates estourava um KeyError em ingles.
                QMessageBox.information(
                    self, "Arquivo não encontrado",
                    "Este arquivo não está mais na lista do projeto. "
                    "Recarregue a lista (F5) e tente de novo.")
                return
            if not status.review_exists and not status.canonical_exists:
                self.open_media_only(interview_id)
                return
            try:
                self.review = app_service.load_review(self.context, interview_id, create=True)
                self.current_interview_id = interview_id
                self.turns = review_store.review_turns(self.review)
                self.media_candidates = app_service.get_media_candidates(self.context, interview_id)
                self.undo_stack.clear()
                # Fase 3: tempos por palavra direto do ASR raw (fail-soft:
                # sem ASR/words, as features de palavra somem em silencio).
                from . import words as words_mod
                try:
                    self.word_index = words_mod.load_word_index(self.context.paths, interview_id)
                except Exception:  # noqa: BLE001 - palavras sao opcionais
                    self.word_index = []
                self._word_uncertain_cutoff = words_mod.uncertain_threshold(self.word_index)
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível abrir", sanitize_message(str(exc)))
                # Falha pode ocorrer apos atribuicoes parciais (review/id novos
                # com editor antigo) — resetar para estado coerente "nada aberto".
                self.close_open_file()
                return
            if not self.media_candidates:
                QMessageBox.critical(self, "Mídia não encontrada", "Não encontrei o áudio/vídeo desta entrevista.")
                self.close_open_file()
                return
            self.review_title.setText(self._review_title_text(interview_id))
            self.review_title.setToolTip(
                "Modelo que produziu ESTA transcrição. O cabeçalho da janela "
                "mostra o modelo configurado para as próximas.")
            self.set_editor_enabled(True)
            self.set_media_source(preferred_media_index(self.media_candidates))
            self.load_waveform()
            cutoff = self._word_uncertain_cutoff
            self.waveform_widget.set_words([
                (word["start"],
                 cutoff is not None and word["score"] is not None and word["score"] <= cutoff)
                for word in self.word_index])
            self.load_turn_table()
            if self.turns:
                self.select_turn_by_index(0, seek=False)
            self.set_save_state(saved_status_message())
            self.update_action_states()
            # Tempos por palavra ausentes degradavam em SILENCIO: o duplo
            # clique numa palavra simplesmente nao fazia nada. Uma linha de
            # status explica e aponta o caminho (sem popup, sem widget novo).
            if self.turns and not self.word_index:
                try:
                    _palavras_estado = self._capability_state("tempos_por_palavra")[0]
                except Exception:  # noqa: BLE001 - sonda nunca atrapalha a abertura
                    _palavras_estado = "pronta"
                if _palavras_estado != "pronta":
                    self.progress_label.setText(
                        "Este arquivo não tem tempos por palavra (o duplo clique numa "
                        "palavra não leva ao áudio). Instale \"Tempos por palavra\" em "
                        "Ferramentas → Gerenciar modelos… e transcreva novamente.")
                else:
                    self.progress_label.setText(
                        "Este arquivo foi transcrito sem tempos por palavra — "
                        "transcreva novamente para gerá-los.")
            # Abertura comum: o banner contextual assume (o dialogo automatico
            # fica so para o fim de uma transcricao — plano D2.6).
            self._update_voice_banner()

        def _offer_voice_naming_after_job(self, interview_id: str) -> None:
            """Versao pos-job da oferta: so vale se o mesmo arquivo continua
            aberto (o usuario pode ter trocado durante o delay do QTimer)."""
            if interview_id != self.current_interview_id:
                return
            self._maybe_offer_voice_naming(interview_id)
            self._update_voice_banner()
            # A faixa do lote sai da conta a transcricao recem-confirmada.
            self._update_voice_batch_banner()

        def _review_has_speaker_edits(self) -> bool:
            return any(
                str(edit.get("action") or "") in ("set_speaker", "set_speaker_all")
                for edit in (self.review or {}).get("edits", [])
            )

        def _persist_confirmed_from_edits(self, interview_id: str) -> None:
            """Migracao implicita do parque legado: quem ja mexeu em falantes
            nesta review confirmou na pratica — gravar o flag, sem perguntar."""
            if interview_id in self._confirm_migrated:
                return
            self._confirm_migrated.add(interview_id)
            try:
                self.context = app_service.update_file_metadata(
                    self.context, [interview_id], {"speakers_confirmed": "true"}
                )
            except Exception as exc:
                _logger.warning("Falha ao gravar speakers_confirmed: %s", exc)

        def _update_voice_banner(self) -> None:
            """Banner "vozes nao confirmadas" acima da tabela de blocos.

            E o caminho persistente e nao-intrusivo (plano D2.6): o dialogo
            automatico so acontece ao FIM de uma transcricao; em todos os
            outros momentos o estado fica visivel aqui, a um clique."""
            if not hasattr(self, "voice_banner"):
                return
            visible = False
            if self.review and self.current_interview_id and self.context is not None:
                metadata = self.context.metadata.get(self.current_interview_id, {})
                if should_offer_voice_naming(self.context.config, metadata, self.turns):
                    if self._review_has_speaker_edits():
                        self._persist_confirmed_from_edits(self.current_interview_id)
                    else:
                        visible = True
            self.banner_area.set_wanted("voice", visible)
            self._update_diar_failed_banner()

        def _update_diar_failed_banner(self) -> None:
            if not hasattr(self, "diar_failed_banner"):
                return
            visible = False
            if self.review and self.current_interview_id and self.context is not None:
                job = self.context.jobs.get(self.current_interview_id) or {}
                visible = "Identificação de falantes não concluída" in str(job.get("last_error") or "")
            self.banner_area.set_wanted("diar_failed", visible)
            self._update_boundary_banner()
            # Abrir/trocar de entrevista muda o alvo da aba Documentos.
            self._on_review_tab_changed(-1)

        def _boundary_suspect_rows(self) -> list[int]:
            """Indices dos turnos marcados e ainda nao tratados."""
            return boundary_flagged_rows(self.turns)

        def _dismiss_boundary_banner(self) -> None:
            if self.current_interview_id:
                self._boundary_dismissed.add(self.current_interview_id)
            self._update_boundary_banner()

        def _update_boundary_banner(self) -> None:
            if not hasattr(self, "boundary_banner"):
                return
            dispensado = (self.current_interview_id or "") in self._boundary_dismissed
            rows = (self._boundary_suspect_rows()
                    if self.review and not dispensado else [])
            if rows:
                plural = "s" if len(rows) > 1 else ""
                self.boundary_banner_label.setText(
                    f"🔍 {len(rows)} troca{plural} de falante com vozes parecidas — confira as marcações."
                )
            self.banner_area.set_wanted("boundary", bool(rows))

        def show_find_bar(self) -> None:
            if not self.review:
                QMessageBox.information(self, "Abra uma transcrição", "Abra uma transcrição para buscar nela.")
                return
            self.find_bar.setVisible(True)
            self.find_input.setFocus()
            self.find_input.selectAll()

        def _close_find_bar(self) -> None:
            if not hasattr(self, "find_bar"):
                return
            self.find_bar.setVisible(False)
            self.find_input.blockSignals(True)
            self.find_input.clear()
            self.find_input.blockSignals(False)
            self._find_matches = []
            self.find_count_label.setText("")
            for row in range(self.turn_table.rowCount()):
                self.turn_table.setRowHidden(row, False)

        def _apply_find_filter(self, text: str) -> None:
            from .search import search_turns
            query = text.strip()
            if not query:
                self._find_matches = []
                self.find_count_label.setText("")
                for row in range(self.turn_table.rowCount()):
                    self.turn_table.setRowHidden(row, False)
                return
            match_rows = {hit["turn_index"] for hit in search_turns(self.turns, query)}
            self._find_matches = sorted(match_rows)
            for row in range(self.turn_table.rowCount()):
                self.turn_table.setRowHidden(row, row not in match_rows)
            plural = "s" if len(match_rows) != 1 else ""
            self.find_count_label.setText(f"{len(match_rows)} bloco{plural}")

        def _find_step(self, step: int) -> None:
            matches = getattr(self, "_find_matches", [])
            if not matches:
                return
            current = self.turn_table.currentRow()
            if step > 0:
                target = next((row for row in matches if row > current), matches[0])
            else:
                target = next((row for row in reversed(matches) if row < current), matches[-1])
            self.select_turn_by_index(target, seek=True)
            item = self.turn_table.item(target, 0)
            if item is not None:
                self.turn_table.scrollToItem(item)

        def _on_boundary_nav(self, step: int) -> None:
            """Navega para o proximo/anterior bloco marcado (ciclico),
            relativo a selecao atual da tabela."""
            rows = self._boundary_suspect_rows()
            if not rows:
                return
            current = self.turn_table.currentRow()
            if step > 0:
                target = next((row for row in rows if row > current), rows[0])
            else:
                target = next((row for row in reversed(rows) if row < current), rows[-1])
            self.select_turn_by_index(target, seek=True)
            item = self.turn_table.item(target, 0)
            if item is not None:
                self.turn_table.scrollToItem(item)

        def _on_banner_identify_clicked(self) -> None:
            self.open_voice_naming_dialog()
            self._update_voice_banner()

        def _on_banner_dismiss_clicked(self) -> None:
            self._set_voice_naming_prompt(False)
            self._update_voice_banner()

        def _maybe_offer_voice_naming(self, interview_id: str) -> None:
            """Dialogo automatico do "De quem é esta voz?" — usado apenas ao
            FIM de uma transcricao (plano D2.6); nos demais momentos o banner
            contextual assume. Recusa vale pela sessão."""
            if interview_id in self._voice_naming_declined:
                return
            if self.worker and self.worker.isRunning():
                return
            if not self.review or self.context is None:
                return
            metadata = self.context.metadata.get(interview_id, {})
            if not should_offer_voice_naming(self.context.config, metadata, self.turns):
                return
            if self._review_has_speaker_edits():
                self._persist_confirmed_from_edits(interview_id)
                return
            if not self.open_voice_naming_dialog():
                self._voice_naming_declined.add(interview_id)

        def open_voice_naming_dialog(self) -> bool:
            """Dialogo "De quem é esta voz?" para a transcrição aberta.

            Lista TODAS as vozes cruas (SPEAKER_NN) — o rótulo default
            posicional não conta como nome confirmado (plano D2.5). Retorna
            True quando o usuário confirmou (mesmo mantendo os nomes atuais)."""
            if not self.review or not self.current_interview_id or not self.media_candidates:
                QMessageBox.information(self, "Abra uma transcrição", "Abra uma transcrição para identificar as vozes.")
                return False
            voices = raw_voice_ids(self.turns)
            if not voices:
                QMessageBox.information(
                    self,
                    "Sem vozes para identificar",
                    "Esta transcrição não tem vozes separadas (diarização desligada ou voz única).",
                )
                return False
            if not self.save_current_turn():
                return False
            interview_id = self.current_interview_id
            dominant = {voice: dominant_speaker_key(self.turns, voice) for voice in voices}
            current_names: dict[str, str] = {}
            for voice in voices:
                current_names[voice] = next(
                    (display_speaker(turn) for turn in self.turns
                     if raw_speaker_key(turn) == voice
                     and review_store.turn_speaker_key(turn) == dominant[voice]
                     and str(turn.get("human_label") or "").strip()),
                    "",
                )
            suggestions = order_role_suggestions(self.turns, voices, key_fn=raw_speaker_key)
            embeddings: dict[str, list[float]] = {}
            matches: dict[str, tuple[str, float]] = {}
            try:
                embeddings = voice_recognition.load_speaker_embeddings(self.context.paths, interview_id)
                if embeddings:
                    matches = voice_recognition.match_voices(
                        embeddings,
                        voice_recognition.load_anchors(self.context.paths),
                        float(self.context.config.get("voice_match_threshold") or 0.65),
                    )
            except Exception as exc:
                _logger.warning("Reconhecimento de vozes indisponivel: %s", exc)
            rows: list[dict[str, Any]] = []
            for position, voice in enumerate(voices, start=1):
                seconds, blocks = speaker_talk_summary(self.turns, voice, key_fn=raw_speaker_key)
                minutes = int(seconds // 60)
                talk = f"{minutes} min" if minutes else f"{int(seconds)} s"
                options = list(suggestions.get(voice, []))
                recognized = matches.get(voice)
                if recognized:
                    options = [recognized[0]] + [option for option in options if option.casefold() != recognized[0].casefold()]
                current = current_names.get(voice, "")
                if current and all(option.casefold() != current.casefold() for option in options):
                    options.append(current)
                if recognized:
                    title = f"Voz {position} — parece ser \"{recognized[0]}\" (confira na amostra) — {talk} em {blocks} bloco(s)"
                elif current:
                    title = f"Voz {position} — hoje \"{current}\" — {talk} em {blocks} bloco(s)"
                else:
                    title = f"Voz {position} — {talk} de fala em {blocks} bloco(s)"
                rows.append({"title": title, "samples": speaker_sample_clips(self.turns, voice, key_fn=raw_speaker_key), "suggestions": options})
            # Tocar o WAV preparado, nunca o original: os timestamps do
            # pipeline referem-se ao WAV, e seek em MP3/M4A (VBR) e impreciso
            # — o erro cresce com a posicao e toca a pessoa errada (bug pego
            # no uso real, 2026-08-23).
            dialog_media = next(
                (path for path in self.media_candidates if path.suffix.lower() == ".wav"),
                self.media_candidates[0],
            )
            dialog = SpeakerNamingDialog(dialog_media, rows, self)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if dialog.dont_ask():
                self._set_voice_naming_prompt(False)
            if not accepted:
                return False
            before = deepcopy(self.review)
            applied = 0
            confirmed_names: dict[str, str] = {}
            for voice, label in zip(voices, dialog.labels()):
                if not label:
                    continue  # em branco = manter o nome atual, decidir depois
                confirmed_names[voice] = label
                try:
                    applied += review_store.apply_label_to_raw_speaker(
                        self.review, voice, speaker_internal_label(label), dominant[voice]
                    )
                except ValueError:
                    continue
            if applied:
                try:
                    app_service.save_review(self.context, interview_id, self.review)
                except Exception as exc:
                    self.review = before
                    self.turns = review_store.review_turns(self.review)
                    QMessageBox.critical(self, "Não foi possível salvar", sanitize_message(str(exc)))
                    return False
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                if self.turns:
                    self.select_turn_by_index(0, seek=False)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Identificar vozes", before, self.review, self.current_turn_id))
                self.set_save_state(saved_status_message())
            # Rotulos POSICIONAIS (ordem dos SPEAKER_XX, a mesma do render) +
            # confirmacao humana — o aceite conta mesmo sem mudar nomes.
            positional: list[str] = []
            for key in ordered_speaker_keys(self.turns):
                named = next(
                    (display_speaker(turn) for turn in self.turns
                     if raw_speaker_key(turn) == key and str(turn.get("human_label") or "").strip()),
                    "",
                )
                positional.append(named or key)
            try:
                self.context = app_service.update_file_metadata(
                    self.context, [interview_id],
                    {"speaker_labels": "|".join(positional), "speakers_confirmed": "true"},
                )
            except Exception as exc:
                _logger.warning("Falha ao gravar rotulos no metadado: %s", exc)
            # Ancoras do reconhecimento local: cada voz nomeada com embedding
            # disponivel vira referencia do projeto (recorrentes = candidatas).
            try:
                if embeddings and confirmed_names:
                    anchors = voice_recognition.load_anchors(self.context.paths)
                    for voice, name in confirmed_names.items():
                        vector = embeddings.get(voice)
                        if vector:
                            anchors = voice_recognition.add_anchor(anchors, name, interview_id, vector)
                    voice_recognition.save_anchors(self.context.paths, anchors)
            except Exception as exc:
                _logger.warning("Falha ao gravar ancoras de voz: %s", exc)
            if applied and not (self.worker and self.worker.isRunning()):
                self.start_worker(
                    "Aplicar nomes aos documentos",
                    [(
                        "Remontando transcrição...",
                        lambda item=interview_id: app_service.render_interviews(
                            self.context, ids=[item],
                            overrides=self._render_source_overrides(item)
                        ),
                    )],
                )
            self._update_voice_banner()
            self.progress_label.setText(f"Nomes aplicados a {applied} bloco(s)." if applied else "Vozes confirmadas.")
            return True

        def _ask_speaker_counts_if_needed(self, ids: list[str]) -> bool:
            """"Quantas pessoas falam?" — uma vez por lote, so para arquivos
            nunca configurados (plano D3.1). False = usuario cancelou."""
            pending = ids_without_speaker_setup(self.context.metadata, ids)
            if not pending:
                return True
            dialog = SpeakerCountDialog(len(pending), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            try:
                self.context = app_service.update_file_metadata(self.context, pending, dialog.updates())
            except Exception as exc:
                _logger.warning("Falha ao gravar configuracao de falantes: %s", exc)
            return True

        def _reset_speakers_confirmed(self, ids: list[str]) -> None:
            """Re-diarizacao gera clusters novos: os nomes precisam ser
            reconfirmados (plano D2.5, item 10). Limpar antes do job — se ele
            falhar, a re-oferta e benigna."""
            try:
                self.context = app_service.update_file_metadata(self.context, ids, {"speakers_confirmed": ""})
            except Exception as exc:
                _logger.warning("Falha ao limpar speakers_confirmed: %s", exc)

        def _set_voice_naming_prompt(self, enabled: bool) -> None:
            """Liga/desliga a pergunta "De quem é esta voz?" no projeto atual."""
            if self.context is None:
                return
            try:
                self.context = app_service.update_engine_config(self.context, {"voice_naming_prompt": bool(enabled)})
            except Exception as exc:
                _logger.warning("update_engine_config(voice_naming_prompt) falhou: %s", exc)
            self._sync_voice_prompt_action()
            self._update_voice_banner()

        def _on_voice_prompt_toggled(self, checked: bool) -> None:
            self._set_voice_naming_prompt(bool(checked))

        def _sync_voice_prompt_action(self) -> None:
            if not hasattr(self, "voice_prompt_action"):
                return
            self.voice_prompt_action.blockSignals(True)
            self.voice_prompt_action.setChecked(
                bool(self.context.config.get("voice_naming_prompt", True)) if self.context else True
            )
            self.voice_prompt_action.blockSignals(False)

        def open_media_only(self, interview_id: str) -> None:
            try:
                self.media_candidates = app_service.get_media_candidates(self.context, interview_id)
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível abrir a mídia", sanitize_message(str(exc)))
                return
            if not self.media_candidates:
                QMessageBox.critical(self, "Mídia não encontrada", "Não encontrei o áudio/vídeo deste arquivo.")
                return
            self.player.stop()
            self.review = None
            self.current_interview_id = interview_id
            self.current_turn_id = None
            self.current_play_row = None
            self.turns = []
            self.word_index = []
            self._word_uncertain_cutoff = None
            self.turn_table.setRowCount(0)
            self.text_edit.clear()
            self.undo_stack.clear()
            self.review_title.setText(f"Mídia: {interview_id} - ainda sem transcrição")
            self.set_editor_enabled(False)
            self.set_media_source(preferred_media_index(self.media_candidates))
            self.load_waveform()
            # Mesma cascata do close_open_file: sem ela, os banners da
            # entrevista aberta ANTES (trocas de falante, vozes) ficavam
            # pintados sobre a midia sem transcricao (teste real do b44).
            self._update_voice_banner()
            self.set_save_state("Arquivo sem transcrição. Use Transcrever este arquivo para gerar a transcrição editável.")
            self.progress_label.setText("Arquivo aberto como mídia. Use Transcrever este arquivo para criar a transcrição.")
            self.update_action_states()

        def close_open_file(self, *_args: Any) -> None:
            if not self.save_current_turn():
                return
            self.player.stop()
            self.player.setSource(QUrl())
            self._sync_video_panel(False)  # sem isto, retangulo preto residual
            self.review = None
            self.current_interview_id = None
            self.current_turn_id = None
            self.current_play_row = None
            self.media_candidates = []
            self.media_candidate_index = 0
            self.turns = []
            self.word_index = []
            self._word_uncertain_cutoff = None
            self.review_title.setText("Abra um arquivo para editar a transcrição.")
            self.turn_table.setRowCount(0)
            self.waveform_widget.set_waveform([], 0)
            self.text_edit.clear()
            self.set_editor_enabled(False)
            self.undo_stack.clear()
            self.set_save_state("Sem transcrição aberta.")
            self._update_voice_banner()
            self.progress_label.setText("Arquivo fechado.")
            self.update_action_states()

        def set_editor_enabled(self, enabled: bool) -> None:
            for widget in [
                self.speaker_combo,
                self.inaudivel_checkbox,
                self.duvida_checkbox,
                self.sobreposicao_checkbox,
                self.start_time_edit,
                self.end_time_edit,
                self.text_edit,
            ]:
                widget.setEnabled(enabled)

        def status_by_interview_id(self, interview_id: str) -> Any | None:
            return self._status_map.get(interview_id)

        def current_turn(self) -> dict[str, Any] | None:
            if not self.review or not self.current_turn_id:
                return None
            try:
                return self.turns[review_store.find_turn_index(self.review, self.current_turn_id)]
            except Exception:
                return None

        def speaker_options_for_current_file(self) -> list[str]:
            metadata = (self.context.metadata if self.context else {}).get(self.current_interview_id or "", {})
            labels = project_store.speaker_labels_for_metadata(metadata)
            existing = {label.casefold() for label in labels}
            for turn in self.turns:
                label = display_speaker(turn)
                if label and label.casefold() not in existing:
                    labels.append(label)
                    existing.add(label.casefold())
            return labels

        def set_media_source(self, index: int) -> None:
            self.media_candidate_index = index
            self._fallback_media_attempted = False
            media_path = self.media_candidates[index]
            self._sync_video_panel(self.media_has_video(media_path))
            self.player.setSource(QUrl.fromLocalFile(str(media_path)))

        def _sync_video_panel(self, has_video: bool) -> None:
            """Choke point do painel de video (2026-08-31): botao contextual
            + redistribuicao do review_splitter SO na transicao de estado
            (o arrasto do usuario dentro do mesmo estado e respeitado).
            Cobre todos os call sites de set_media_source, o toggle e os
            fluxos de fechar/trocar projeto (retangulo preto residual)."""
            self.video_toggle_button.setVisible(has_video)
            self.video_toggle_button.setText(
                "Mostrar vídeo" if self._video_user_hidden else "Ocultar vídeo")
            efetivo = has_video and not self._video_user_hidden
            if efetivo == self._video_panel_visible:
                return
            self._video_panel_visible = efetivo
            # setVisible ANTES do setSizes: o minimo do painel de midia
            # muda com o video, e o QSplitter clamparia contra o antigo.
            self.video_widget.setVisible(efetivo)
            total = sum(self.review_splitter.sizes())  # handles fora
            if total > 0:
                self.review_splitter.setSizes(media_splitter_sizes(total, efetivo))

        def _toggle_video_panel(self) -> None:
            # O botao so esta visivel quando a midia tem imagem.
            self._video_user_hidden = not self._video_user_hidden
            self._sync_video_panel(True)

        def media_has_video(self, path: Path) -> bool:
            return path.suffix.lower() in VIDEO_SUFFIXES

        def load_waveform(self) -> None:
            wav_path = next((path for path in self.media_candidates if path.suffix.lower() == ".wav"), None)
            if wav_path:
                peaks, duration = load_waveform_peaks(wav_path)
                self.waveform_widget.set_waveform(peaks, duration)
                return
            source_path = self.media_candidates[0] if self.media_candidates else None
            if not source_path or not self.current_interview_id:
                self.waveform_widget.set_waveform([], 0)
                return
            if self.context is None:
                return
            cache_path = waveform_cache_path(self.context.paths.output_root, self.current_interview_id)
            cached = load_waveform_cache(cache_path, source_path)
            if cached is not None:
                peaks, duration = cached
                self.waveform_widget.set_waveform(peaks, duration)
                return
            previous_status = self.progress_label.text() if hasattr(self, "progress_label") else ""
            if hasattr(self, "progress_label"):
                self.progress_label.setText("Gerando onda sonora da mídia original...")
            peaks: list[float] = []
            duration: float = 0.0
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                peaks, duration = load_media_waveform_peaks(source_path)
                if peaks:
                    save_waveform_cache(cache_path, source_path, peaks, duration)
            except Exception as exc:
                print(f"Aviso: nao foi possivel gerar onda sonora: {exc}", file=sys.stderr)
                peaks, duration = [], 0.0
            finally:
                QApplication.restoreOverrideCursor()
            if hasattr(self, "progress_label"):
                self.progress_label.setText(previous_status or "Onda sonora pronta.")
            self.waveform_widget.set_waveform(peaks, duration)

        def zoom_waveform_in(self) -> None:
            self.waveform_widget.zoom_in()

        def zoom_waveform_out(self) -> None:
            self.waveform_widget.zoom_out()

        def zoom_waveform_fit(self) -> None:
            self.waveform_widget.fit_all()

        def center_waveform_on_player(self) -> None:
            self.waveform_widget.center_on_playhead()

        def zoom_waveform_to_current_turn(self) -> None:
            turn = self.current_turn()
            if not turn:
                return
            start = float(turn.get("start", 0) or 0)
            end = float(turn.get("end", start) or start)
            self.waveform_widget.zoom_to_range(start, end)

        def _follow_default_audio_output(self) -> None:
            """Reaponta a saida para o novo dispositivo padrao do sistema."""
            try:
                self.audio_output.setDevice(QMediaDevices.defaultAudioOutput())
            except Exception as exc:  # noqa: BLE001 - audio nunca derruba o app
                _logger.warning("troca de dispositivo de audio falhou: %s", exc)

        def seek_player(self, target_ms: int) -> None:
            """Seek com confirmacao anti-WMF: o backend de midia do Windows
            DESCARTA silenciosamente o setPosition feito com o player
            pausado (mesmo bug corrigido no dialogo de vozes em 2026-08-24).
            Reconfere em 80/300ms e reaplica se o player ignorou; um seek
            novo invalida as conferencias do anterior via token."""
            target_ms = max(0, int(target_ms))
            self._seek_token = getattr(self, "_seek_token", 0) + 1
            token = self._seek_token
            self.player.setPosition(target_ms)

            def _ensure() -> None:
                if getattr(self, "_seek_token", 0) != token:
                    return
                if abs(self.player.position() - target_ms) > 1500:
                    self.player.setPosition(target_ms)

            QTimer.singleShot(80, _ensure)
            QTimer.singleShot(300, _ensure)

        def seek_waveform(self, seconds: float) -> None:
            self.waveform_widget.set_edit_cursor(seconds)
            self.seek_player(int(seconds * 1000))

        def _seek_word_at_char(self, char_pos: int) -> None:
            """Duplo clique no texto: leva o audio ate a palavra (fase 3).

            Sem indice de palavras (arquivo so-midia, ASR ausente), o gesto
            degrada para a selecao padrao da palavra, sem seek.
            """
            if not self.review or not self.current_turn_id or not self.word_index:
                if self.review and self.current_turn_id and not self.word_index:
                    # Feedback NO MOMENTO do gesto: o duplo clique mudo era o
                    # unico sinal de que os tempos por palavra nao existem
                    # (a dica da abertura ja tinha sido sobrescrita).
                    try:
                        pronto = self._capability_state("tempos_por_palavra")[0] == "pronta"
                    except Exception:  # noqa: BLE001
                        pronto = True
                    self.progress_label.setText(
                        "Este arquivo não tem tempos por palavra — o duplo clique "
                        "não leva ao áudio. "
                        + ("Transcreva novamente para gerá-los."
                           if pronto else
                           "Instale \"Tempos por palavra\" em Ferramentas → "
                           "Gerenciar modelos… e transcreva novamente."))
                return
            try:
                index = review_store.find_turn_index(self.review, self.current_turn_id)
            except KeyError:
                return
            from . import words as words_mod
            turn = self.turns[index]
            turn_start = float(turn.get("start", 0) or 0)
            turn_end = float(turn.get("end", turn_start) or turn_start)
            time_s, _exact = words_mod.word_time_for_char(
                words_mod.words_in_range(self.word_index, turn_start, turn_end),
                self.text_edit.toPlainText(), char_pos)
            if time_s is not None:
                self.seek_waveform(float(time_s))

        def load_turn_table(self) -> None:
            self.current_play_row = None
            self.turn_table.setRowCount(0)
            # Cor estavel por voz na coluna Falante (plano D3.2) — so quando
            # ha mais de uma voz; entrevista sem diarizacao fica neutra.
            colors = voice_color_map(self.turns)
            for turn in self.turns:
                row = self.turn_table.rowCount()
                self.turn_table.insertRow(row)
                start = float(turn.get("start", 0) or 0)
                end = float(turn.get("end", start) or start)
                values = [
                    f"{format_clock(start)}-{format_clock(end)}",
                    display_speaker(turn),
                    " ".join(str(turn.get("text", "")).split()),
                    display_flags(turn),
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip(value)
                    if column == 3:
                        # A explicacao da marcacao (notes) so existia no JSON e
                        # nos exports; o tooltip a torna visivel na revisao.
                        notes = str(turn.get("notes") or "").strip()
                        if notes:
                            item.setToolTip(notes)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, turn.get("id"))
                    elif column == 1 and len(colors) > 1:
                        color = colors.get(raw_speaker_key(turn))
                        if color:
                            item.setForeground(QBrush(QColor(color)))
                    self.turn_table.setItem(row, column, item)
            if hasattr(self, "wrap_turns_checkbox"):
                self.toggle_turn_word_wrap()

        def toggle_turn_word_wrap(self, *_args: Any) -> None:
            enabled = True
            if hasattr(self, "wrap_turns_checkbox"):
                enabled = self.wrap_turns_checkbox.isChecked()
            self.turn_table.setWordWrap(enabled)
            if enabled:
                self.turn_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                self.turn_table.resizeRowsToContents()
            else:
                self.turn_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                for row in range(self.turn_table.rowCount()):
                    self.turn_table.setRowHeight(row, 28)

        def on_turn_selection_changed(self) -> None:
            if self._changing_selection:
                return
            rows = self.turn_table.selectionModel().selectedRows()
            if not rows:
                return
            self.select_turn_by_index(rows[0].row(), seek=False)

        def on_turn_cell_clicked(self, row: int, column: int) -> None:
            self.select_turn_by_index(row, seek=(column == 0))

        def seek_turn_from_row(self, row: int, _column: int) -> None:
            self.select_turn_by_index(row, seek=True)

        def select_turn_by_index(self, row: int, seek: bool) -> None:
            if row < 0 or row >= len(self.turns):
                return
            if not self.save_current_turn():
                return
            self._changing_selection = True
            try:
                self.turn_table.selectRow(row)
            finally:
                self._changing_selection = False
            turn = self.turns[row]
            self.current_turn_id = str(turn.get("id"))
            self.load_turn_editor(turn)
            start = float(turn.get("start", 0) or 0)
            end = float(turn.get("end", start) or start)
            self.waveform_widget.set_selected_range(start, end)
            if seek:
                self.waveform_widget.set_edit_cursor(start)
                self.seek_player(int(start * 1000))
            self.update_action_states()

        def load_turn_editor(self, turn: dict[str, Any]) -> None:
            self._loading_editor = True
            widgets = [
                self.speaker_combo,
                self.inaudivel_checkbox,
                self.duvida_checkbox,
                self.sobreposicao_checkbox,
                self.start_time_edit,
                self.end_time_edit,
                self.text_edit,
            ]
            for widget in widgets:
                widget.blockSignals(True)
            try:
                self.speaker_combo.clear()
                self.speaker_combo.addItems(self.speaker_options_for_current_file())
                index = self.speaker_combo.findText(display_speaker(turn))
                if index >= 0:
                    self.speaker_combo.setCurrentIndex(index)
                elif display_speaker(turn):
                    self.speaker_combo.addItem(display_speaker(turn))
                    self.speaker_combo.setCurrentIndex(self.speaker_combo.count() - 1)
                self.inaudivel_checkbox.setChecked("inaudivel" in turn.get("flags", []))
                self.duvida_checkbox.setChecked("duvida" in turn.get("flags", []))
                self.sobreposicao_checkbox.setChecked("sobreposicao" in turn.get("flags", []))
                if hasattr(self, "turn_note_label"):
                    note = str(turn.get("notes") or "").strip()
                    self.turn_note_label.setText(note)
                    self.turn_note_label.setVisible(bool(note))
                self.start_time_edit.setText(format_timecode(float(turn.get("start", 0) or 0)))
                self.end_time_edit.setText(format_timecode(float(turn.get("end", turn.get("start", 0)) or 0)))
                self.text_edit.setPlainText(str(turn.get("text", "")))
            finally:
                for widget in widgets:
                    widget.blockSignals(False)
                self._editor_dirty = False
                self._loading_editor = False
                self.update_action_states()

        def editor_changed(self) -> None:
            if self._loading_editor or not self.current_turn_id:
                return
            self._editor_dirty = True
            self._save_failed = False
            self.set_save_state("Alterações pendentes...")
            self.update_action_states()
            self.autosave_timer.start()

        def set_save_state(self, message: str, error: bool = False, tooltip: str | None = None) -> None:
            if not hasattr(self, "save_status_label"):
                return
            self.save_status_label.setText(message)
            self.save_status_label.setStyleSheet(_style_err() if error else _style_muted())
            if tooltip is not None:
                self.save_status_label.setToolTip(tooltip)
            elif message == saved_status_message():
                self.save_status_label.setToolTip(saved_status_tooltip())
            else:
                self.save_status_label.setToolTip("")

        def save_current_turn(self, force: bool = False) -> bool:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                if force:
                    self.set_save_state("Abra uma transcrição para salvar.")
                    return False
                return True
            if not self._editor_dirty and not force:
                return True
            if not self._editor_dirty and force:
                self.set_save_state(saved_status_message())
                return True
            self.set_save_state("Salvando...")
            try:
                flags = []
                if self.inaudivel_checkbox.isChecked():
                    flags.append("inaudivel")
                if self.duvida_checkbox.isChecked():
                    flags.append("duvida")
                if self.sobreposicao_checkbox.isChecked():
                    flags.append("sobreposicao")
                start = parse_timecode(self.start_time_edit.text())
                end = parse_timecode(self.end_time_edit.text())
                review_store.set_turn_times(self.review, self.current_turn_id, start, end)
                review_store.set_turn_text(self.review, self.current_turn_id, self.text_edit.toPlainText())
                review_store.set_turn_speaker_label(self.review, self.current_turn_id, speaker_internal_label(self.speaker_combo.currentText()))
                review_store.set_turn_flags(self.review, self.current_turn_id, flags)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.update_current_row_preview()
                self._update_boundary_banner()
                self._editor_dirty = False
                self._save_failed = False
                self.autosave_timer.stop()
                self.set_save_state(saved_status_message())
                self.progress_label.setText("Alterações salvas.")
                self.update_action_states()
                return True
            except Exception as exc:
                self._save_failed = True
                self._editor_dirty = True
                self.set_save_state("Erro ao salvar.", error=True)
                message = QMessageBox(self)
                message.setIcon(QMessageBox.Icon.Critical)
                message.setWindowTitle("Não foi possível salvar")
                message.setText("A transcrição não foi salva.")
                message.setInformativeText("Corrija o problema indicado e tente salvar novamente antes de trocar de entrevista ou fechar o aplicativo.")
                message.setDetailedText(sanitize_message(str(exc)))
                message.exec()
                self.update_action_states()
                return False

        def apply_speaker_label_to_all(self) -> None:
            """Aplica o nome do combo a todos os blocos da mesma voz (D2.2)."""
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                QMessageBox.information(self, "Abra uma transcrição", "Abra uma transcrição e selecione um bloco primeiro.")
                return
            stored = self.current_turn()
            if stored is None:
                return
            # A identidade da voz e capturada ANTES de salvar o turno atual:
            # depois do save, o turno ja carrega o nome novo e a key mudaria.
            reference_key = review_store.turn_speaker_key(stored)
            label = speaker_internal_label(self.speaker_combo.currentText())
            if not self.save_current_turn():
                return
            before = deepcopy(self.review)
            try:
                changed = review_store.apply_label_to_speaker_key(self.review, reference_key, label)
            except ValueError as exc:
                QMessageBox.warning(self, "Nome inválido", sanitize_message(str(exc)))
                return
            if not changed:
                self.progress_label.setText("Todos os blocos desta voz já têm este nome.")
                return
            try:
                app_service.save_review(self.context, self.current_interview_id, self.review)
            except Exception as exc:
                self.review = before
                self.turns = review_store.review_turns(self.review)
                QMessageBox.critical(self, "Não foi possível salvar", sanitize_message(str(exc)))
                return
            self.turns = review_store.review_turns(self.review)
            self.load_turn_table()
            try:
                self.select_turn_by_index(review_store.find_turn_index(self.review, self.current_turn_id), seek=False)
            except Exception:
                pass
            self.undo_stack.push(ReviewSnapshotCommand(self, "Aplicar falante a todos", before, self.review, self.current_turn_id))
            self.set_save_state(saved_status_message())
            self.progress_label.setText(f"Nome aplicado a {changed} bloco(s) desta voz.")

        def _set_action(self, action: QAction, enabled: bool, disabled_reason: str = "",
                        enabled_note: str = "") -> None:
            """Habilita/desabilita a acao e explica o motivo no tooltip.

            O tooltip ORIGINAL fica guardado na propria acao: usar a
            primeira linha do tooltip corrente como base truncava
            permanentemente os tooltips de varias linhas (Perguntar,
            Resumir) na primeira desabilitacao.

            enabled_note: nota anexada quando a acao FICA habilitada
            (ex.: "Baixa o modelo de nomes (~1,1 GB) na primeira
            utilizacao.") — o estado "instalavel" nao desabilita, mas
            o usuario merece saber antes de clicar.
            """
            base = action.property("tooltip_base")
            if base is None:
                base = action.toolTip() or action.text()
                action.setProperty("tooltip_base", base)
            action.setEnabled(enabled)
            if not enabled and disabled_reason:
                action.setToolTip(f"{base}\n({disabled_reason})")
            elif enabled and enabled_note:
                action.setToolTip(f"{base}\n({enabled_note})")
            else:
                action.setToolTip(str(base))

        def _capability_state(self, key: str) -> tuple[str, str, float]:
            """Estado da capacidade NESTA maquina, com cache.

            update_action_states roda a cada troca de selecao; sondar GPU
            e disco toda vez seria caro. O cache e invalidado quando algo
            que muda a resposta acontece (download concluido, assistente).
            """
            from . import capabilities as _caps
            estado_cache = getattr(self, "_caps_cache", None)
            if estado_cache is None:
                variante = None
                idioma = None
                try:
                    if self.context is not None:
                        variante = str(self.context.config.get("asr_model") or "") or None
                        idioma = self.context.config.get("asr_language")
                except Exception:  # noqa: BLE001
                    variante = None
                estado_cache = (
                    _caps.hardware_snapshot(),
                    _caps.cached_model_keys(variante, idioma),
                    _caps.model_sizes_from_registry(variante, idioma),
                )
                self._caps_cache = estado_cache
            hardware, em_cache, tamanhos = estado_cache
            return _caps.capability_status(
                _caps.capability(key), hardware, em_cache, tamanhos)

        def _capability_warning(self, key: str) -> str:
            """Aviso "roda, mas por conta e risco" (VRAM abaixo do minimo)
            desta capacidade NESTA maquina; "" sem ressalvas."""
            from . import capabilities as _caps
            try:
                cache = getattr(self, "_caps_cache", None)
                hw = cache[0] if cache else _caps.hardware_snapshot()
                return _caps.hardware_warning(_caps.capability(key), hw)
            except Exception:  # noqa: BLE001 - aviso nunca derruba a UI
                return ""

        def _invalidate_capability_cache(self) -> None:
            self._caps_cache = None
            self.update_action_states()

        def update_action_states(self) -> None:
            if not hasattr(self, "save_action"):
                return
            busy = bool(self.worker and self.worker.isRunning())
            has_project = self._has_project()
            has_selected = bool(self.selected_interview_id() or self.current_interview_id)
            has_table_selection = bool(self.effective_target_ids())
            # Habilitar pela MESMA regua da execucao (F7): as destrutivas
            # miram a selecao visual; transcrever/diarizar/render miram os
            # checkboxes (com fallback no arquivo aberto). Habilitar por uma
            # regua e executar por outra produzia cliques que so repreendiam.
            has_destructive = bool(self.destructive_target_ids())
            reason_destructive = ("Selecione (destaque) ao menos um arquivo na "
                                  "lista — as caixas ☑ escolhem o que "
                                  "transcrever, não o que apagar.")
            reason_checked = ("Marque ☑ ao menos um arquivo na lista "
                              "(ou abra um arquivo).")
            has_review = bool(self.current_interview_id and self.review)
            has_open_file = bool(self.current_interview_id)
            has_untranscribed_open_file = bool(self.current_interview_id and not self.review)
            has_turn = bool(has_review and self.current_turn_id)
            reason_busy = busy_reason_text(self._busy_state_text() if busy else "")
            reason_project = "Abra ou crie um projeto primeiro."
            reason_select = "Selecione ao menos um arquivo na lista."
            reason_open = "Abra uma transcrição primeiro."
            reason_turn = "Selecione um bloco na transcrição."
            self._set_action(self.new_project_action, not busy, reason_busy)
            self._set_action(self.open_project_action, not busy, reason_busy)
            self._set_action(self.add_folder_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.add_files_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.open_project_folder_action, not busy and has_project, reason_busy if busy else reason_project)
            self.exit_action.setEnabled(True)
            self.apply_metadata_action.setEnabled(not busy and has_project and has_table_selection)
            self.queue_action.setEnabled(has_project)
            self._set_action(self.engine_settings_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.reload_list_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.open_transcript_action, not busy and has_table_selection, reason_busy if busy else reason_select)
            self._set_action(self.transcribe_action, not busy and has_selected, reason_busy if busy else reason_checked)
            self._set_action(self.transcribe_pending_action, not busy and bool(self.pending_transcription_ids()), reason_busy if busy else "Não há arquivos pendentes.")
            self._set_action(self.transcribe_current_action, not busy and has_untranscribed_open_file, reason_busy if busy else reason_open)
            self._set_action(self.retranscribe_current_action, not busy and has_review, reason_busy if busy else reason_open)
            self._set_action(self.save_action, not busy and has_turn, reason_busy if busy else reason_turn)
            self._set_action(self.generate_files_action, not busy and (has_review or has_table_selection or any(status.review_exists or status.canonical_exists for status in self.statuses)), reason_busy if busy else "Nenhuma transcrição disponível.")
            self._set_action(self.delete_transcription_action, not busy and has_destructive, reason_busy if busy else reason_destructive)
            # Rename e reorder exigem UM unico alvo
            single_target = bool(has_project and len(self.effective_target_ids()) == 1)
            single_target_busy = False
            if single_target and self.context:
                only = self.effective_target_ids()[0]
                single_target_busy = (self.context.jobs.get(only) or {}).get("status") in ("Rodando", "Na fila")
            rename_reason = "Selecione um único arquivo para renomear." if not single_target else "Aguarde a transcrição terminar."
            reorder_reason = "Selecione um único arquivo para reordenar." if not single_target else "Aguarde a transcrição terminar."
            self._set_action(self.rename_interview_action, not busy and single_target and not single_target_busy, reason_busy if busy else rename_reason)
            self._set_action(self.move_up_action, not busy and single_target and not single_target_busy, reason_busy if busy else reorder_reason)
            self._set_action(self.move_down_action, not busy and single_target and not single_target_busy, reason_busy if busy else reorder_reason)
            # Trash actions
            trash_busy = bool(getattr(self, "_trash_busy", False))
            any_busy = busy or trash_busy
            self._set_action(
                self.trash_selected_action,
                not any_busy and has_project and has_destructive,
                reason_busy if any_busy else reason_destructive,
            )
            can_undo = bool(getattr(self, "_trash_undo", []))
            can_redo = bool(getattr(self, "_trash_redo", []))
            self._set_action(
                self.trash_undo_action,
                not any_busy and can_undo,
                reason_busy if any_busy else "Nada a desfazer nesta sessão.",
            )
            self._set_action(
                self.trash_redo_action,
                not any_busy and can_redo,
                reason_busy if any_busy else "Nada a refazer nesta sessão.",
            )
            self._set_action(self.close_open_file_action, not busy and has_open_file, reason_busy if busy else "Nenhum arquivo aberto.")
            self._set_action(self.open_export_folder_action, not busy, reason_busy)
            # Acoes que dependem do modelo de falantes: instalavel mantem
            # habilitada, mas a nota avisa que o download exigira conta HF.
            falantes_estado, _falantes_motivo, falantes_gb = self._capability_state("separar_falantes")
            falantes_nota = (
                f"Modelo de separação de falantes não instalado (~{falantes_gb:.1f} GB) — "
                "o download exigirá conta gratuita no Hugging Face."
                if falantes_estado == "instalavel" else "")
            self._set_action(self.improve_speakers_action, not busy and has_review,
                             reason_busy if busy else reason_open,
                             enabled_note=falantes_nota)
            self._set_action(self.name_voices_action, not busy and has_review, reason_busy if busy else reason_open)
            self._set_action(self.voice_prompt_action, not busy and has_project, reason_busy if busy else "Abra ou crie um projeto primeiro.")
            # QC sem projeto quebrava (context None no run_qc_job).
            self._set_action(self.qc_action, not busy and has_project,
                             reason_busy if busy else reason_project)
            # Acoes de AI (etapa 2 do plano de perfis): a regra e simples e
            # vale para todas — INCOMPATIVEL com a maquina desabilita e diz
            # por que; falta só baixar mantem habilitada, porque o clique
            # oferece o download (com a nota avisando o tamanho antes).
            # Nunca clique-morto, nunca erro.
            resumo_estado, resumo_motivo, resumo_gb = self._capability_state("resumo_perguntar")
            resumo_travado = resumo_estado == "incompativel"
            notas_resumo: list[str] = []
            if resumo_estado == "instalavel":
                notas_resumo.append(f"Baixa o modelo de análise (~{resumo_gb:.1f} GB) "
                                    "na primeira utilização.")
            resumo_aviso = self._capability_warning("resumo_perguntar")
            if not resumo_travado and resumo_aviso:
                notas_resumo.append(f"Atenção: {resumo_aviso} — por sua conta e risco.")
            # Durante um lote as acoes de AI continuam CLICAVEIS (decisao do
            # usuario 2026-09-02): o clique nao executa — explica na faixa da
            # lista (_explain_busy). A nota do tooltip diz o mesmo.
            nota_lote = reason_busy if busy else ""
            self._set_action(
                self.summarize_action,
                has_review and not resumo_travado,
                resumo_motivo if resumo_travado else reason_open,
                enabled_note=" ".join([nota_lote] + notas_resumo).strip(),
            )
            busca_estado, _busca_motivo, busca_gb = self._capability_state("busca_semantica")
            self._set_action(self.explore_action, has_project, reason_project,
                             enabled_note=" ".join([nota_lote, (
                                 f"Baixa um modelo de ~{fmt_gb(busca_gb)} na primeira utilização."
                                 if busca_estado == "instalavel" else "")]).strip())
            self._set_action(self.themes_action, has_project, reason_project,
                             enabled_note=" ".join([nota_lote, (
                                 f"Baixa um modelo de ~{fmt_gb(busca_gb)} na primeira utilização."
                                 if busca_estado == "instalavel" else "")]).strip())
            glos_estado, glos_motivo, glos_gb = self._capability_state("glossario_nomes")
            glos_travado = glos_estado == "incompativel"  # hoje impossivel (CPU basta); regra geral
            self._set_action(self.glossario_action,
                             has_project and not glos_travado,
                             glos_motivo if glos_travado else reason_project,
                             enabled_note=" ".join([nota_lote, (
                                 f"Baixa o modelo de nomes (~{fmt_gb(glos_gb)}) na primeira utilização."
                                 if glos_estado == "instalavel" else "")]).strip())
            self._set_action(self.spelling_action, has_project, reason_project,
                             enabled_note=nota_lote)
            self.cancel_job_action.setEnabled(busy)
            if hasattr(self, "progress_bar"):
                self.progress_bar.setVisible(busy)
            if hasattr(self, "cancel_job_button"):
                self.cancel_job_button.setVisible(busy)
            if hasattr(self, "transcribe_button"):
                self.transcribe_button.setEnabled(not busy and has_project)
            self._set_action(self.diarize_toggle_action, not busy and has_project,
                             reason_busy if busy else reason_project)
            if hasattr(self, "save_block_button"):
                self.save_block_button.setEnabled(not busy and has_turn)
            # Bloco aberto: as ações e os três botões que as espelham. O motivo
            # vai no tooltip — botão cinza sem explicação foi reclamação de campo.
            motivo_bloco = reason_busy if busy else "Abra uma entrevista e escolha um bloco."
            for acao_bloco in (self.merge_prev_block_action, self.merge_block_action,
                               self.split_block_action, self.repeat_turn_action,
                               self.next_block_action, self.prev_block_action,
                               self.next_flagged_action, self.prev_flagged_action,
                               self.focus_toggle_action):
                self._set_action(acao_bloco, not busy and has_turn, motivo_bloco)
            if hasattr(self, "transcribe_current_button"):
                self.transcribe_current_button.setVisible(has_untranscribed_open_file)
                self.transcribe_current_button.setEnabled(not busy and has_untranscribed_open_file)
            if hasattr(self, "retranscribe_current_button"):
                self.retranscribe_current_button.setVisible(has_review)
                self.retranscribe_current_button.setEnabled(not busy and has_review)
            if hasattr(self, "improve_speakers_button"):
                self.improve_speakers_button.setVisible(has_review)
                self.improve_speakers_button.setEnabled(not busy and has_review)
            if hasattr(self, "open_resumo_button"):
                has_resumo = False
                if self.current_interview_id and self.context is not None:
                    from .summarize import resumo_path as _resumo_path
                    has_resumo = _resumo_path(self.context.paths, self.current_interview_id).exists()
                self.open_resumo_button.setVisible(has_resumo)
                if hasattr(self, "generate_resumo_button"):
                    self.generate_resumo_button.setVisible(has_review and not has_resumo)
                    # Mesma regra da acao: maquina incompativel desabilita o
                    # botao e explica no tooltip, em vez de deixar clicar
                    # para so entao dizer que nao da.
                    # Clicavel durante o lote (explica na faixa); tooltip proprio.
                    self.generate_resumo_button.setEnabled(has_review and not resumo_travado)
                    self.generate_resumo_button.setToolTip(
                        resumo_motivo if resumo_travado else
                        (reason_busy if busy else self.summarize_action.toolTip()))
            self._sync_busy_hints(busy)

        def restore_review_snapshot(self, snapshot: dict[str, Any], selected_turn_id: str | None = None) -> None:
            if not self.current_interview_id:
                return
            self.review = deepcopy(snapshot)
            app_service.save_review(self.context, self.current_interview_id, self.review)
            self.turns = review_store.review_turns(self.review)
            self._editor_dirty = False
            self._save_failed = False
            self.load_turn_table()
            target_index = 0
            if selected_turn_id:
                try:
                    target_index = review_store.find_turn_index(self.review, selected_turn_id)
                except Exception:
                    target_index = min(target_index, max(0, len(self.turns) - 1))
            if self.turns:
                self.select_turn_by_index(target_index, seek=False)
            self.set_save_state(saved_status_message())
            self.update_action_states()
            self._update_boundary_banner()

        def update_current_row_preview(self) -> None:
            if not self.review or not self.current_turn_id:
                return
            try:
                index = review_store.find_turn_index(self.review, self.current_turn_id)
            except Exception:
                return
            turn = self.turns[index]
            start = float(turn.get("start", 0) or 0)
            end = float(turn.get("end", start) or start)
            self.turn_table.item(index, 0).setText(f"{format_clock(start)}-{format_clock(end)}")
            self.turn_table.item(index, 1).setText(display_speaker(turn))
            text = " ".join(str(turn.get("text", "")).split())
            self.turn_table.item(index, 2).setText(text)
            self.turn_table.item(index, 2).setToolTip(text)
            self.turn_table.item(index, 3).setText(display_flags(turn))

        def _back_to_text(self, mensagem: str) -> None:
            """Anuncia na linha de estado e DEVOLVE O FOCO ao texto.

            O foco importa mais do que parece: depois de clicar num botão de
            bloco ele ficava no botão, e daí o Ctrl+Z escapava do editor (ver
            `_undo_belongs_to_editor`). Devolver o foco também deixa o próximo
            atalho de teclado funcionar sem passar pelo mouse."""
            self.progress_label.setText(mensagem)
            if hasattr(self, "text_edit"):
                self.text_edit.setFocus()

        def _edit_warning(self, mensagem: str) -> None:
            """Erro de edição de bloco na faixa de estado, não em janela modal
            (padrão da R4). Pisca em WARN e volta ao normal."""
            self.progress_label.setText(mensagem)
            try:
                self.progress_label.setStyleSheet(f"color: {ui_tokens.WARN}; font-weight: 600;")
                QTimer.singleShot(2500, lambda: self.progress_label.setStyleSheet(""))
            except Exception:  # noqa: BLE001 - aviso nunca derruba a edicao
                pass
            if hasattr(self, "text_edit"):
                self.text_edit.setFocus()

        def merge_current_turn_with_previous(self) -> None:
            """Junta com o bloco de cima. O contorno — subir um bloco e juntar
            para a frente — troca o bloco aberto e faz perder o cursor."""
            self.merge_current_turn(para_tras=True)

        def merge_current_turn(self, *_args: object, para_tras: bool = False) -> None:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                return
            # Sem confirmação (2026-09-04): juntar não apaga nada, não perde
            # texto e tem desfazer completo — e era uma das duas caixas fixas
            # de um ciclo de 219 blocos por hora de áudio. O aviso vai para a
            # linha de estado, com o Ctrl+Z dito por extenso.
            if not self.save_current_turn():
                return
            before = deepcopy(self.review)
            juntar = review_store.merge_turn_with_previous if para_tras else review_store.merge_turn_with_next
            try:
                merged_id = juntar(self.review, self.current_turn_id)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                self.select_turn_by_index(review_store.find_turn_index(self.review, merged_id), seek=False)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Juntar blocos", before, self.review, merged_id))
                self.set_save_state(saved_status_message())
                self._back_to_text("Blocos juntados. Ctrl+Z desfaz.")
            except Exception as exc:
                self._edit_warning(f"Não foi possível juntar: {sanitize_message(str(exc))}")

        def split_current_turn(self) -> None:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                return
            # Sem confirmação: dividir é reversível por inteiro (ver merge).
            if not self.save_current_turn():
                return
            try:
                current_index = review_store.find_turn_index(self.review, self.current_turn_id)
            except KeyError as exc:
                self._edit_warning(f"Não foi possível dividir: {sanitize_message(str(exc))}")
                return
            before = deepcopy(self.review)
            current_turn = self.turns[current_index]
            turn_start = float(current_turn.get("start", 0) or 0)
            turn_end = float(current_turn.get("end", turn_start) or turn_start)
            stripped = self.text_edit.toPlainText().strip()
            cursor_pos = self.text_edit.textCursor().position()
            split_char = review_store.choose_split_char(stripped, cursor_pos)
            from . import words as words_mod
            split_time, split_note = choose_split_time(
                turn_start,
                turn_end,
                self.waveform_widget.edit_cursor,
                bool(getattr(self.waveform_widget, "edit_cursor_from_click", False)),
                self.player.position() / 1000 if self.player.position() else None,
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                words_mod.words_in_range(self.word_index, turn_start, turn_end),
                stripped,
                split_char,
            )
            try:
                new_id = review_store.split_turn(self.review, self.current_turn_id, split_time=split_time, split_char=split_char)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                self.select_turn_by_index(review_store.find_turn_index(self.review, new_id), seek=False)
                self.waveform_widget.set_edit_cursor(split_time)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Dividir bloco", before, self.review, new_id))
                self.set_save_state(saved_status_message())
                self._back_to_text(f"Bloco dividido; {split_note}. Ctrl+Z desfaz.")
            except Exception as exc:
                self._edit_warning(f"Não foi possível dividir: {sanitize_message(str(exc))}")

        def use_player_as_start(self) -> None:
            self.apply_player_time_to_boundary("start")

        def use_player_as_end(self) -> None:
            self.apply_player_time_to_boundary("end")

        def apply_player_time_to_boundary(self, boundary: str) -> None:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                return
            if not self.save_current_turn():
                return
            index = review_store.find_turn_index(self.review, self.current_turn_id)
            player_time = self.player.position() / 1000
            turn = self.turns[index]
            start = float(turn.get("start", 0) or 0)
            end = float(turn.get("end", start) or start)
            before = deepcopy(self.review)
            try:
                if boundary == "start":
                    if player_time >= end:
                        raise ValueError("A posição do player precisa ficar antes do fim do bloco.")
                    review_store.set_turn_times(self.review, self.current_turn_id, player_time, end)
                    if index > 0:
                        previous = self.turns[index - 1]
                        previous_start = float(previous.get("start", 0) or 0)
                        if previous_start < player_time:
                            review_store.set_turn_times(self.review, str(previous["id"]), previous_start, player_time)
                else:
                    if player_time <= start:
                        raise ValueError("A posição do player precisa ficar depois do início do bloco.")
                    review_store.set_turn_times(self.review, self.current_turn_id, start, player_time)
                    if index < len(self.turns) - 1:
                        following = self.turns[index + 1]
                        following_end = float(following.get("end", player_time) or player_time)
                        if player_time < following_end:
                            review_store.set_turn_times(self.review, str(following["id"]), player_time, following_end)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                self.select_turn_by_index(index, seek=False)
                self.waveform_widget.set_edit_cursor(player_time)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Ajustar tempo", before, self.review, self.current_turn_id))
                self.set_save_state(saved_status_message())
                self.progress_label.setText("Tempo ajustado pela posição do player.")
            except Exception as exc:
                QMessageBox.warning(self, "Não foi possível ajustar o tempo", sanitize_message(str(exc)))

        def delete_selected_transcriptions(self, *_args: Any) -> None:
            _logger.info("delete_selected_transcriptions triggered: context=%s", self.context is not None)
            if self.context is None:
                return
            # Alvo DESTRUTIVO = selecao visual (simetria com a Lixeira,
            # pos-incidente 2026-08-25) ou a linha sob o cursor do menu de
            # contexto; as caixas de marcacao escolhem "o que transcrever",
            # nunca o que apagar.
            ids = self.destructive_target_ids(getattr(self, "_context_cursor_row", None))
            _logger.info("  destructive_target_ids: %s | checked=%s visual=%s current_iid=%s",
                         ids, sorted(self._checked_ids),
                         sorted(self._visually_selected_interview_ids()),
                         self.current_interview_id)
            if not ids:
                QMessageBox.information(
                    self, "Selecione arquivos",
                    "Selecione (destaque) ao menos um arquivo na lista para limpar "
                    "a transcricao.\nAs caixas de marcação não contam para esta "
                    "ação — elas escolhem o que transcrever.")
                return
            n = len(ids)
            if n == 1:
                msg = "Limpar a transcrição gerada deste arquivo?"
            else:
                msg = f"Limpar a transcrição gerada de {n} arquivos?"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Apagar transcrição")
            box.setText(msg)
            box.setInformativeText(
                "Os arquivos gerados (transcrição bruta, identificação de "
                "falantes, transcrição editável, métricas) serão apagados. O "
                "áudio original é mantido no projeto — você pode gerar a "
                "transcrição de novo depois.\n\n"
                "Se houver edições manuais, uma cópia de segurança da transcrição "
                "editável fica em 05_transcripts_review/edits/backups/.\n\n"
                "Esta ação não pode ser desfeita.")
            box.setDetailedText("Arquivos afetados:\n\n" + "\n".join(ids))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
            if self.current_interview_id and self.current_interview_id in ids:
                self.close_open_file()
            try:
                deleted, self.context = app_service.delete_transcription_outputs(self.context, ids)
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao apagar", sanitize_message(str(exc))[:2000])
                return
            self.refresh_interviews()
            self.progress_label.setText(f"{deleted} arquivo(s) apagado(s) de {n} entrevista(s).")

        def rename_selected_interview(self, *_args: Any, cursor_row: int | None = None) -> None:
            if self.context is None:
                return
            ids = self.effective_target_ids(cursor_row)
            if len(ids) != 1:
                QMessageBox.information(self, "Selecione um arquivo", "Selecione um único arquivo para renomear.")
                return
            interview_id = ids[0]
            busy = [iid for iid in ids if (self.context.jobs.get(iid) or {}).get("status") in ("Rodando", "Na fila")]
            if busy:
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcrição terminar ou cancele o job na fila de processamento.")
                return
            metadata = self.context.metadata.get(interview_id, {})
            current_title = str(metadata.get("title") or "").strip() or interview_id
            raw, ok = QInputDialog.getText(
                self,
                "Renomear rótulo",
                "Novo rótulo para exibição (deixe vazio para usar o nome do arquivo):",
                text=current_title,
            )
            if not ok:
                return
            new_title, truncated = _sanitize_rename_title(raw)
            title_to_store = new_title if new_title and new_title != interview_id else ""
            try:
                self.context = app_service.rename_interview(self.context, interview_id, title_to_store)
            except app_service.InterviewBusyError:
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcrição terminar ou cancele o job na fila de processamento.")
                return
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao renomear", str(exc)[:2000])
                return
            self._trash_redo.clear()
            self.refresh_interviews()
            self._select_row_by_interview_id(interview_id)
            if not title_to_store:
                self.progress_label.setText("Rotulo removido. Exibindo o nome do arquivo.")
            elif truncated:
                self.progress_label.setText(f'Rotulo atualizado para "{title_to_store}" (limitado a 200 caracteres).')
            else:
                self.progress_label.setText(f'Rotulo atualizado para "{title_to_store}".')

        def move_selected_up(self, *_args: Any, cursor_row: int | None = None) -> None:
            self._move_selected(cursor_row=cursor_row, direction=-1)

        def move_selected_down(self, *_args: Any, cursor_row: int | None = None) -> None:
            self._move_selected(cursor_row=cursor_row, direction=+1)

        def _move_selected(self, cursor_row: int | None, direction: int) -> None:
            if self.context is None:
                return
            ids = self.effective_target_ids(cursor_row)
            if len(ids) != 1:
                QMessageBox.information(self, "Selecione um arquivo", "Selecione um único arquivo para reordenar.")
                return
            moving_id = ids[0]
            if (self.context.jobs.get(moving_id) or {}).get("status") in ("Rodando", "Na fila"):
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcrição terminar ou cancele o job na fila de processamento.")
                return
            # Primeira ativacao de ordem manual: captura ordem VISUAL atual
            was_manual = bool(self.context.project.get("manual_order_active"))
            if not was_manual:
                existing_order = list(self.context.project.get("interview_order") or [])
                if existing_order:
                    reply = QMessageBox.question(
                        self,
                        "Substituir ordem manual",
                        "Ja existe uma ordem manual salva neste projeto. Substituir pela ordem atual da lista?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                visual_order = self._visible_interview_ids_in_order()
                hidden_ids = [
                    row.get("interview_id", "")
                    for row in self.context.rows
                    if row.get("interview_id") and row.get("interview_id") not in visual_order
                ]
                base_order = visual_order + hidden_ids
                self.context = app_service.set_interview_order(self.context, base_order, manual_active=True)
                first_activation_msg = (
                    "Ordem manual ativada (ordem anterior substituida). Clique em um cabecalho de coluna para ordenar por coluna."
                    if existing_order
                    else "Ordem manual ativada. Clique em um cabecalho de coluna para ordenar por coluna."
                )
            else:
                first_activation_msg = None
            hidden_set = {
                row.get("interview_id", "")
                for row in self.context.rows
                if row.get("interview_id") and self._is_interview_hidden(row.get("interview_id", ""))
            }
            try:
                self.context = app_service.move_interviews(
                    self.context, [moving_id], direction, hidden_ids=list(hidden_set)
                )
            except app_service.InterviewBusyError:
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcrição terminar ou cancele o job na fila de processamento.")
                return
            except ValueError as exc:
                QMessageBox.critical(self, "Erro ao reordenar", str(exc)[:2000])
                return
            self._trash_redo.clear()
            self.refresh_interviews()
            self._select_row_by_interview_id(moving_id)
            if first_activation_msg:
                self.progress_label.setText(first_activation_msg)
            else:
                direction_txt = "para cima" if direction < 0 else "para baixo"
                self.progress_label.setText(f"Arquivo movido {direction_txt}.")

        def _is_interview_hidden(self, interview_id: str) -> bool:
            for row_idx in range(self.interview_table.rowCount()):
                item = self.interview_table.item(row_idx, COL_ARQUIVO)
                if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == interview_id:
                    return self.interview_table.isRowHidden(row_idx)
            return False

        TRASH_ASYNC_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB

        def trash_selected_interviews(self, *_args: Any, cursor_row: int | None = None) -> None:
            if cursor_row is None:
                cursor_row = getattr(self, "_context_cursor_row", None)
            _logger.info("trash_selected_interviews triggered: context=%s busy=%s cursor_row=%s",
                         self.context is not None, self._trash_busy, cursor_row)
            if self.context is None or self._trash_busy:
                _logger.info("  return: context None or busy")
                return
            # S5 (incidente 2026-08-25): acao destrutiva NUNCA usa o escopo
            # dos checkboxes (que significam "transcrever estes") — so a
            # selecao visual/cursor.
            ids = self.destructive_target_ids(cursor_row)
            _logger.info("  destructive_target_ids: %s | visual=%s",
                         ids, sorted(self._visually_selected_interview_ids()))
            if not ids:
                QMessageBox.information(
                    self, "Selecione arquivos",
                    "Selecione (destaque) ao menos um arquivo na lista para enviar a Lixeira.\n"
                    "As caixas de marcação não contam para esta acao — elas escolhem o que transcrever.")
                return
            busy_ids = [iid for iid in ids if (self.context.jobs.get(iid) or {}).get("status") in ("Rodando", "Na fila")]
            if busy_ids:
                QMessageBox.information(self, "Acao bloqueada", "Não é possível enviar arquivos com transcrição em andamento. Aguarde ou cancele o job na fila de processamento.")
                return
            n = len(ids)
            listing = "\n".join(f"  • {iid}" for iid in ids[:10])
            if n > 10:
                listing += f"\n  • ... e mais {n - 10}"
            if n == 1:
                text = f"Enviar este arquivo para a Lixeira do projeto?\n\n{listing}"
            else:
                text = f"Enviar estes {n} arquivos para a Lixeira do projeto?\n\n{listing}"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Enviar para Lixeira")
            box.setText(text)
            box.setInformativeText("O áudio original, a transcrição e os metadados serão movidos para a Lixeira do projeto (00_project/.trash/). Voce pode desfazer com Ctrl+Z enquanto esta sessao estiver aberta.")
            box.setDetailedText("Arquivos afetados:\n\n" + "\n".join(ids))
            yes_btn = box.addButton("Enviar para Lixeira", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(yes_btn)
            # Default must be Cancel for safety
            cancel_btn = None
            for btn in box.buttons():
                if box.buttonRole(btn) == QMessageBox.ButtonRole.RejectRole:
                    cancel_btn = btn
                    break
            if cancel_btn:
                box.setDefaultButton(cancel_btn)
            box.exec()
            if box.clickedButton() is not yes_btn:
                return
            if self.current_interview_id and self.current_interview_id in ids:
                self.close_open_file()
            try:
                trash_entry = app_service.prepare_trash_move(self.context, ids)
            except app_service.InterviewBusyError:
                QMessageBox.information(self, "Acao bloqueada", "Não é possível mover arquivos com transcrição em andamento. Aguarde ou cancele o job na fila de processamento.")
                return
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao preparar exclusão", str(exc)[:2000])
                return
            total_bytes = trash_entry.get("total_bytes", 0)
            self._trash_busy = True
            self.trash_selected_action.setEnabled(False)
            self.trash_undo_action.setEnabled(False)
            self.trash_redo_action.setEnabled(False)
            if total_bytes > self.TRASH_ASYNC_THRESHOLD_BYTES:
                self._run_trash_worker(trash_entry, n)
            else:
                self._run_trash_sync(trash_entry, n)

        def _run_trash_sync(self, trash_entry: dict, n: int) -> None:
            """Trash para < 50 MB: roda sem worker, rapido."""
            import shutil
            from pathlib import Path as _Path
            from .utils import write_json as _write_json
            try:
                trash_dir = _Path(trash_entry["trash_dir"])
                staging = trash_dir / "staging"
                staging.mkdir(parents=True, exist_ok=True)
                project_root = _Path(trash_entry["project_root"])
                moved_files: list[dict] = []
                for mf in trash_entry.get("files_to_move") or []:
                    src = _Path(mf["original"])
                    if not src.exists():
                        continue
                    try:
                        rel = src.resolve().relative_to(project_root.resolve())
                        dest = staging / rel
                    except ValueError:
                        dest = staging / src.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        stem = dest.stem
                        suffix = dest.suffix
                        counter = 1
                        while (dest.parent / f"{stem}__{counter}{suffix}").exists():
                            counter += 1
                        dest = dest.parent / f"{stem}__{counter}{suffix}"
                    shutil.copy2(str(src), str(dest))
                    if src.stat().st_size != dest.stat().st_size:
                        raise RuntimeError(f"tamanho divergente: {src.name}")
                    trashed_rel = str(dest.relative_to(trash_dir)).replace("\\", "/")
                    moved_files.append({
                        "original": str(src.resolve()),
                        "trashed": trashed_rel,
                        "size": int(src.stat().st_size),
                        "mtime": float(src.stat().st_mtime),
                    })
                files_dir = trash_dir / "files"
                _promote_staging_to_files(staging, files_dir)
                for mf in moved_files:
                    mf["trashed"] = mf["trashed"].replace("staging/", "files/", 1)
                entry_dict = project_store._build_undo_entry(
                    trash_id=trash_entry["trash_id"],
                    interview_ids=trash_entry["interview_ids"],
                    csv_mtimes=trash_entry.get("csv_mtimes") or {},
                    snapshots=trash_entry.get("snapshots") or {},
                    moved_files=moved_files,
                    status="complete",
                )
                entry_dict["project_root"] = str(project_root)
                _write_json(trash_dir / project_store.TRASH_MANIFEST, entry_dict)
                entry_dict["trash_dir"] = str(trash_dir)
            except Exception as exc:
                # Falha na fase de copia/staging: originais ainda intactos,
                # descartar o trash_dir incompleto e abortar.
                shutil.rmtree(_Path(trash_entry["trash_dir"]), ignore_errors=True)
                self._trash_busy = False
                self.update_action_states()
                QMessageBox.critical(self, "Erro ao mover para lixeira", str(exc)[:2000])
                return
            # FORA do try: a partir daqui finalize_trash_move deleta os originais —
            # nunca fazer rollback (rmtree) do trash_dir, que vira a UNICA copia.
            self._on_trash_worker_finished(entry_dict, "", n, async_mode=False)

        def _run_trash_worker(self, trash_entry: dict, n: int) -> None:
            """Trash para >= 50 MB: worker + QProgressDialog."""
            self._trash_progress_dialog = QProgressDialog(
                f"Preparando {n} arquivo(s)...",
                "Cancelar",
                0,
                max(1, len(trash_entry.get("files_to_move") or [])),
                self,
            )
            self._trash_progress_dialog.setWindowTitle("Movendo para a lixeira")
            self._trash_progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self._trash_progress_dialog.setMinimumDuration(500)
            self._trash_progress_dialog.setAutoClose(False)
            self._trash_progress_dialog.setAutoReset(False)
            worker = TrashMoveWorker(trash_entry)
            self._trash_worker = worker
            self._trash_progress_dialog.canceled.connect(worker.request_cancel)
            worker.progress.connect(self._on_trash_progress)
            worker.stage_changed.connect(self._on_trash_stage_changed)
            worker.finished_result.connect(
                lambda entry, err: self._on_trash_worker_finished(entry, err, n, async_mode=True)
            )
            worker.finished.connect(worker.deleteLater)
            worker.start()

        def _on_trash_progress(self, current: int, total: int, name: str) -> None:
            if self._trash_progress_dialog is not None:
                self._trash_progress_dialog.setMaximum(total)
                self._trash_progress_dialog.setValue(current)
                if name:
                    self._trash_progress_dialog.setLabelText(f"Movendo ({current}/{total}): {name}")

        def _on_trash_stage_changed(self, label: str) -> None:
            if self._trash_progress_dialog is not None:
                self._trash_progress_dialog.setLabelText(label)

        def _on_trash_worker_finished(self, entry: dict | None, err: str, n: int, async_mode: bool) -> None:
            _logger.info("_on_trash_worker_finished: async=%s entry=%s err=%r", async_mode, entry is not None, err)
            if async_mode and self._trash_progress_dialog is not None:
                self._trash_progress_dialog.close()
                self._trash_progress_dialog = None
            self._trash_worker = None
            if entry is None:
                self._trash_busy = False
                self.update_action_states()
                if err == "cancelado":
                    self.progress_label.setText("Exclusao cancelada.")
                else:
                    QMessageBox.critical(self, "Erro ao mover para lixeira", err[:2000] if err else "Erro desconhecido")
                return
            try:
                trashed_ids = list(entry.get("interview_ids") or [])
                _logger.info("finalize_trash_move chamado para ids=%s", trashed_ids)
                trash_id, self.context = app_service.finalize_trash_move(self.context, entry)
                _logger.info("finalize OK, trash_id=%s, context.rows=%d", trash_id, len(self.context.rows))
            except Exception as exc:
                _logger.exception("finalize_trash_move FALHOU: %s", exc)
                self._trash_busy = False
                self.update_action_states()
                QMessageBox.critical(self, "Erro ao finalizar exclusão", str(exc)[:2000])
                return
            self._trash_undo.append(trash_id)
            self._trash_redo.clear()
            self._trash_session_ids.append(trash_id)
            # Limpar _checked_ids dos ids trashados para evitar estado stale
            for _tid in trashed_ids:
                self._checked_ids.discard(_tid)
            self._trash_busy = False
            self.refresh_interviews()
            _logger.info("apos refresh_interviews: tabela=%d linhas, statuses=%d",
                         self.interview_table.rowCount(), len(self.statuses))
            self.interview_table.setFocus()
            self.update_action_states()
            if n == 1:
                self.progress_label.setText("1 arquivo enviado para a Lixeira. Ctrl+Z para desfazer.")
            else:
                self.progress_label.setText(f"{n} arquivos enviados para a Lixeira. Ctrl+Z para desfazer.")

        def _undo_belongs_to_editor(self) -> bool:
            """O Ctrl+Z deve desfazer uma edição da transcrição (e não a
            exclusão de um arquivo)? Sim quando há algo desfeito a desfazer E o
            foco está dentro do painel de revisão — inclusive quando ele está
            num BOTÃO desse painel, que é onde ele fica logo depois de Juntar
            ou Dividir."""
            if not (hasattr(self, "undo_action") and self.undo_action.isEnabled()):
                return False
            foco = QApplication.focusWidget()
            if foco is None:
                return False
            if isinstance(foco, QTextEdit) and not foco.isReadOnly():
                return True
            painel = getattr(self, "review_splitter", None)
            return bool(painel is not None and (painel is foco or painel.isAncestorOf(foco)))

        def undo_last_trash(self, *_args: Any) -> None:
            if self._trash_busy or self.context is None:
                return
            # A quem pertence o Ctrl+Z agora? Ate 2026-09-04 a pergunta era
            # "o foco esta num QTextEdit editavel?" — e depois de clicar em
            # "Juntar" ou "Dividir" o foco fica no BOTAO, entao o Ctrl+Z
            # escapava do editor e caia aqui: no melhor caso nao fazia nada, no
            # pior restaurava um ARQUIVO da Lixeira em vez de desfazer a fusao.
            # Agora basta o foco estar dentro do painel de revisao e haver algo
            # a desfazer; a Lixeira so fica com o Ctrl+Z na lista de arquivos.
            if self._undo_belongs_to_editor():
                if hasattr(self, "undo_action") and self.undo_action.isEnabled():
                    self.undo_action.trigger()
                return
            if not self._trash_undo:
                return
            trash_id = self._trash_undo[-1]
            try:
                warnings, self.context = app_service.restore_from_trash(self.context, trash_id, overwrite=False)
            except app_service.CollisionError as exc:
                conflict_paths = "\n".join(c["original"] for c in exc.conflicts[:20])
                if len(exc.conflicts) > 20:
                    conflict_paths += f"\n...e mais {len(exc.conflicts) - 20} arquivos."
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Conflito ao restaurar")
                box.setText(f"{len(exc.conflicts)} arquivo(s) ja existem no destino original.")
                box.setInformativeText("Restaurar vai sobrescrever os arquivos atuais. Essa ação não pode ser desfeita.")
                box.setDetailedText(conflict_paths)
                box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
                box.button(QMessageBox.StandardButton.Yes).setText("Sobrescrever")
                box.setDefaultButton(QMessageBox.StandardButton.Cancel)
                if box.exec() != QMessageBox.StandardButton.Yes:
                    return
                try:
                    warnings, self.context = app_service.restore_from_trash(self.context, trash_id, overwrite=True)
                except Exception as exc2:
                    QMessageBox.critical(self, "Erro ao restaurar", str(exc2)[:2000])
                    return
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao restaurar", str(exc)[:2000])
                return
            self._trash_undo.pop()
            self._trash_redo.append(trash_id)
            n = len(self._trash_entry_interview_ids(trash_id))
            self.refresh_interviews()
            self.interview_table.setFocus()
            self.update_action_states()
            self.progress_label.setText(f"{n} arquivo(s) restaurado(s) da Lixeira. Ctrl+Shift+Z para refazer.")
            if warnings:
                self.progress_label.setText(self.progress_label.text() + " Aviso: " + "; ".join(warnings))

        def redo_last_trash(self, *_args: Any) -> None:
            if self._trash_busy or self.context is None or not self._trash_redo:
                return
            focus = QApplication.focusWidget()
            if isinstance(focus, QTextEdit) and not focus.isReadOnly():
                if hasattr(self, "redo_action") and self.redo_action.isEnabled():
                    self.redo_action.trigger()
                return
            trash_id = self._trash_redo[-1]
            try:
                _, self.context = app_service.redo_trash(self.context, trash_id)
            except app_service.RedoUnavailableError as exc:
                self._trash_redo.clear()
                self.update_action_states()
                QMessageBox.warning(
                    self,
                    "Não é possível refazer",
                    f"O projeto foi alterado desde a ultima acao. Refazer foi cancelado para preservar suas mudancas.\n\nDetalhe: {exc}",
                )
                return
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao refazer", str(exc)[:2000])
                return
            self._trash_redo.pop()
            self._trash_undo.append(trash_id)
            n = len(self._trash_entry_interview_ids(trash_id))
            self.refresh_interviews()
            self.interview_table.setFocus()
            self.update_action_states()
            self.progress_label.setText(f"{n} arquivo(s) enviado(s) para a Lixeira novamente.")

        def _trash_entry_interview_ids(self, trash_id: str) -> list[str]:
            if self.context is None:
                return []
            from .utils import read_json as _read_json
            try:
                manifest = _read_json(project_store.trash_root(self.context.paths) / trash_id / project_store.TRASH_MANIFEST)
            except Exception:
                return []
            return list((manifest or {}).get("interview_ids") or [])

        def _maybe_purge_session_trash(self) -> None:
            """Chamado ao fechar projeto/app. Pergunta se deve apagar permanentemente
            itens da lixeira criados nesta sessao."""
            if self.context is None or not self._trash_session_ids:
                return
            total_bytes = 0
            existing_ids: list[str] = []
            root = project_store.trash_root(self.context.paths)
            for tid in self._trash_session_ids:
                entry_dir = root / tid
                if not entry_dir.exists():
                    continue
                existing_ids.append(tid)
                for f in entry_dir.rglob("*"):
                    if f.is_file():
                        try:
                            total_bytes += f.stat().st_size
                        except OSError:
                            pass
            if not existing_ids:
                return
            size_mb = total_bytes / (1024 * 1024)
            n = len(existing_ids)
            # S5: apagar definitivamente exige saber O QUE — listar nomes.
            names: list[str] = []
            for tid in existing_ids:
                names.extend(self._trash_entry_interview_ids(tid))
            listing = "\n".join(f"  • {name}" for name in names[:10])
            if len(names) > 10:
                listing += f"\n  • ... e mais {len(names) - 10}"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Lixeira do projeto")
            box.setText(
                f"Ha {n} item(ns) na lixeira desta sessao ({size_mb:.1f} MB):\n\n{listing}\n\n"
                "Manter em .trash/ ou apagar definitivamente?")
            keep = box.addButton("Manter", QMessageBox.ButtonRole.AcceptRole)
            purge = box.addButton("Apagar definitivamente", QMessageBox.ButtonRole.DestructiveRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is purge:
                try:
                    app_service.purge_trash_entries(self.context, existing_ids)
                except Exception as exc:
                    _logger.warning("purge_trash_entries falhou: %s", exc)
            self._trash_session_ids = []
            self._trash_undo = []
            self._trash_redo = []

        def _select_row_by_interview_id(self, interview_id: str) -> None:
            """Restaura selecao visual apos refresh_interviews. Critico: usar
            selectionModel().setCurrentIndex(idx, flags) ao inves do metodo do
            widget, que aplica SelectionFlag dependente dos modificadores teclados
            (e apaga a selecao quando a chamada acontece dentro de um handler de
            shortcut com Ctrl/Alt pressionados)."""
            from PySide6.QtCore import QItemSelectionModel
            flags = (
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows
            )
            for row_idx in range(self.interview_table.rowCount()):
                if self.interview_table.isRowHidden(row_idx):
                    continue
                item = self.interview_table.item(row_idx, COL_ARQUIVO)
                if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == interview_id:
                    sel_model = self.interview_table.selectionModel()
                    idx = self.interview_table.model().index(row_idx, COL_ARQUIVO)
                    sel_model.setCurrentIndex(idx, flags)
                    self.interview_table.scrollToItem(item)
                    self.interview_table.setFocus()
                    self.update_action_states()
                    return

        def _show_library_context_menu(self, pos) -> None:
            if self.context is None:
                return
            viewport = self.interview_table.viewport()
            # Shift+F10 / menu key: pos pode vir invalido; usar centro do item atual
            if pos.x() < 0 or pos.y() < 0:
                current = self.interview_table.currentItem()
                if current is None:
                    return
                rect = self.interview_table.visualItemRect(current)
                pos = rect.center()
                cursor_row = current.row()
            else:
                cursor_row = self.interview_table.rowAt(pos.y())
            if cursor_row < 0:
                return  # area vazia: sem menu
            target_ids = self.effective_target_ids(cursor_row)
            single = len(target_ids) == 1
            job_status = (self.context.jobs.get(target_ids[0]) or {}).get("status") if single else ""
            busy_single = job_status in ("Rodando", "Na fila")
            # busy GLOBAL tambem: o setEnabled direto daqui contornava o
            # update_action_states e reabilitava rename/mover durante um job
            # de OUTRO arquivo (escrita concorrente em metadados.csv).
            busy = bool(self.worker and self.worker.isRunning())
            menu = QMenu(self)
            self.rename_interview_action.setEnabled(single and not busy_single and not busy)
            self.move_up_action.setEnabled(single and not busy_single and not busy)
            self.move_down_action.setEnabled(single and not busy_single and not busy)
            menu.addAction(self.rename_interview_action)
            menu.addSeparator()
            menu.addAction(self.move_up_action)
            menu.addAction(self.move_down_action)
            menu.addSeparator()
            menu.addAction(self.delete_transcription_action)
            menu.addAction(self.trash_selected_action)
            # A linha sob o cursor vale como alvo para as acoes destrutivas
            # DESTE menu (botao direito numa linha nao-selecionada apagava a
            # OUTRA linha destacada). Limpo ao fechar: o menu Editar nao
            # pode herdar um cursor velho.
            self._context_cursor_row = cursor_row
            try:
                menu.exec(viewport.mapToGlobal(pos))
            finally:
                self._context_cursor_row = None

        def export_reviews(self, *_args: Any) -> None:
            if not self.save_current_turn(force=bool(self.review and self.current_turn_id)):
                return
            has_open = bool(self.current_interview_id)
            open_title = ""
            if has_open and self.context is not None:
                metadata = self.context.metadata.get(self.current_interview_id, {}) or {}
                open_title = str(metadata.get("title") or "").strip() or self.current_interview_id
            n_selected = len(self.selected_interview_ids())
            n_total = len(self.statuses)
            dialog = ExportDialog(
                has_open=has_open,
                open_title=open_title,
                n_selected=n_selected,
                n_total=n_total,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            formats = dialog.selected_formats()
            if not formats:
                QMessageBox.information(self, "Nenhum formato", "Escolha pelo menos um formato.")
                return
            scope = dialog.selected_scope()
            ids = self.ids_for_export_scope(scope)
            if not ids:
                QMessageBox.information(self, "Nada para exportar", "Não encontrei transcrições para o escopo escolhido.")
                return
            exported: list[Path] = []
            skipped: list[str] = []
            try:
                for interview_id in ids:
                    if not self.ensure_review_for_export(interview_id):
                        skipped.append(interview_id)
                        continue
                    exported.extend(app_service.export_review(self.context, interview_id, formats=formats))
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao exportar", sanitize_message(str(exc)))
                return
            # Feedback de sucesso via ExportResultDialog (lista clicavel + botoes)
            if exported:
                result_dialog = ExportResultDialog(
                    exported_paths=exported,
                    skipped_ids=skipped,
                    results_folder=self._results_folder_for_user(),
                    parent=self,
                )
                result_dialog.exec()
                self.progress_label.setText(f"{len(exported)} arquivo(s) exportado(s).")
            else:
                QMessageBox.information(
                    self,
                    "Nada exportado",
                    "Nenhum arquivo foi gerado. Verifique se as transcrições estão prontas." + (
                        "\n\nSem transcrição exportável:\n" + "\n".join(skipped) if skipped else ""
                    ),
                )

        def ids_for_export_scope(self, scope: str) -> list[str]:
            if scope == "current":
                return [self.current_interview_id] if self.current_interview_id else []
            if scope == "selected":
                return self.selected_interview_ids()
            return [status.interview_id for status in self.statuses]

        def ensure_review_for_export(self, interview_id: str) -> bool:
            if self.current_interview_id == interview_id and self.review:
                return True
            status = self.status_by_interview_id(interview_id)
            if not status or not (status.review_exists or status.canonical_exists):
                return False
            app_service.load_review(self.context, interview_id, create=True)
            return True

        def open_export_folder(self) -> None:
            if not self._require_project("Abrir pasta de exportação"):
                return
            folder = self._results_folder_for_user()
            folder.mkdir(parents=True, exist_ok=True)
            open_folder_in_explorer(folder)

        def _results_folder_for_user(self) -> Path:
            """Retorna a pasta que o usuario deve abrir para ver os arquivos finais.
            Prefere {projeto}/Resultados/ (se existe e a feature esta habilitada),
            senao cai para 05_transcripts_review/final/."""
            if self.context is None:
                return Path.cwd()
            paths = self.context.paths
            if self.context.config.get("use_resultados_dir", True):
                resultados = paths.project_root / project_store.RESULTADOS_DIRNAME
                if resultados.exists():
                    return resultados
            return paths.review_dir / "final"

        def toggle_playback(self) -> None:
            if self.player.source().isEmpty():
                QMessageBox.information(self, "Abra uma entrevista", "Abra uma entrevista antes de reproduzir.")
                return
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def stop_playback(self) -> None:
            self.player.stop()

        def seek_relative(self, seconds: int) -> None:
            if self.player.source().isEmpty():
                return
            target = max(0, min(self.player.duration(), self.player.position() + (seconds * 1000)))
            self.seek_player(target)

        def _step_playback_speed(self, step: int) -> None:
            """Um passo na velocidade de reprodução (F7/F8). Anda pela mesma
            lista do seletor: nunca inventa valor fora dela."""
            if not hasattr(self, "speed_combo"):
                return
            alvo = self.speed_combo.currentIndex() + step
            if alvo < 0 or alvo >= self.speed_combo.count():
                return
            self.speed_combo.setCurrentIndex(alvo)   # dispara update_playback_rate
            # Sem roubar o foco: F7/F8 valem tanto no texto quanto na lista.
            self.progress_label.setText(f"Velocidade de reprodução: {self.speed_combo.currentText()}.")

        def _step_block(self, step: int) -> None:
            """Bloco seguinte/anterior levando o áudio junto e devolvendo o
            cursor ao texto — o ciclo inteiro sem tocar no mouse."""
            if not self.review or not self.turns:
                return
            atual = self.turn_table.currentRow()
            if atual < 0:
                atual = -1 if step > 0 else len(self.turns)
            alvo = atual + step
            if alvo < 0:
                self._back_to_text("Este já é o primeiro bloco.")
                return
            if alvo >= len(self.turns):
                self._back_to_text("Este já é o último bloco.")
                return
            self.select_turn_by_index(alvo, seek=True)
            item = self.turn_table.item(alvo, 0)
            if item is not None:
                self.turn_table.scrollToItem(item)
            if hasattr(self, "text_edit"):
                self.text_edit.setFocus()

        def _step_flagged(self, step: int) -> None:
            """Próximo/anterior bloco marcado para conferir (cíclico, como os
            botões ‹ › da faixa de marcações)."""
            if not self.review:
                return
            if not self._boundary_suspect_rows():
                self._back_to_text("Nenhum bloco marcado para conferir nesta entrevista.")
                return
            self._on_boundary_nav(step)
            if hasattr(self, "text_edit"):
                self.text_edit.setFocus()

        def _toggle_studio_focus(self) -> None:
            """F6: vai e volta entre a lista de blocos e o texto. Sem ele, sair
            do texto para navegar exigia o mouse."""
            if not hasattr(self, "text_edit") or not hasattr(self, "turn_table"):
                return
            if self.text_edit.hasFocus():
                self.turn_table.setFocus()
                self.progress_label.setText("Foco na lista de blocos. F6 volta ao texto.")
            else:
                self.text_edit.setFocus()
                self.progress_label.setText("Foco no texto do bloco. F6 vai para a lista.")

        def repeat_current_turn(self) -> None:
            if not self.review or not self.current_turn_id:
                return
            index = review_store.find_turn_index(self.review, self.current_turn_id)
            start = float(self.turns[index].get("start", 0) or 0)
            self.seek_player(int(start * 1000))
            self.player.play()

        def update_playback_rate(self) -> None:
            self.player.setPlaybackRate(float(self.speed_combo.currentData()))

        def on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.play_button.setText("Pausar")
                self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            else:
                self.play_button.setText("Reproduzir")
                self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

        def on_player_error(self, _error: object, error_string: str = "") -> None:
            if self.media_candidates and not self._fallback_media_attempted and self.media_candidate_index + 1 < len(self.media_candidates):
                self._fallback_media_attempted = True
                self.media_candidate_index += 1
                self.set_media_source(self.media_candidate_index)
                self.progress_label.setText("A mídia original não tocou; usando o WAV preparado.")
                return
            message = error_string or self.player.errorString() or "O player não conseguiu abrir esta mídia."
            QMessageBox.warning(self, "Erro no player", message)

        def on_duration_changed(self, duration_ms: int) -> None:
            self.position_slider.setRange(0, max(0, duration_ms))
            self.update_time_label(self.player.position(), duration_ms)

        def on_position_changed(self, position_ms: int) -> None:
            if not self._slider_dragging:
                self.position_slider.setValue(position_ms)
            self.update_time_label(position_ms, self.player.duration())
            self.waveform_widget.set_position(position_ms / 1000)
            self.highlight_turn_for_position(position_ms / 1000)

        def update_time_label(self, position_ms: int, duration_ms: int) -> None:
            self.time_label.setText(f"{format_clock(position_ms / 1000)} / {format_clock(duration_ms / 1000)}")

        def _slider_pressed(self) -> None:
            self._slider_dragging = True

        def _slider_released(self) -> None:
            self._slider_dragging = False
            self.seek_player(self.position_slider.value())

        def highlight_turn_for_position(self, seconds: float) -> None:
            row = None
            for index, turn in enumerate(self.turns):
                start = float(turn.get("start", 0) or 0)
                end = float(turn.get("end", start) or start)
                if start <= seconds < end:
                    row = index
                    break
            if row == self.current_play_row:
                return
            self.clear_play_highlight()
            self.current_play_row = row
            if row is None:
                return
            for column in range(self.turn_table.columnCount()):
                item = self.turn_table.item(row, column)
                if item:
                    item.setBackground(QColor(ui_tokens.HIGHLIGHT_BG))
                    item.setForeground(QColor(ui_tokens.HIGHLIGHT_TEXT))
            if self.follow_playback_checkbox.isChecked() and not self.text_edit.hasFocus():
                self.turn_table.scrollToItem(self.turn_table.item(row, 0))
            if row is not None:
                turn = self.turns[row]
                start = float(turn.get("start", 0) or 0)
                end = float(turn.get("end", start) or start)
                self.waveform_widget.set_active_range(start, end)

        def clear_play_highlight(self) -> None:
            if self.current_play_row is None:
                self.waveform_widget.set_active_range(None, None)
                return
            for column in range(self.turn_table.columnCount()):
                item = self.turn_table.item(self.current_play_row, column)
                if item:
                    item.setBackground(QBrush())
                    item.setForeground(QBrush())
            self.current_play_row = None
            self.waveform_widget.set_active_range(None, None)

        def selected_ids_for_job(self, fallback_current: bool = True) -> list[str] | None:
            ids = self.selected_interview_ids()
            if not ids and fallback_current and self.current_interview_id:
                ids = [self.current_interview_id]
            return ids or None

        def run_current_file_transcription_job(self, *_args: Any) -> None:
            if not self.current_interview_id:
                QMessageBox.information(self, "Abra um arquivo", "Abra uma mídia antes de transcrever este arquivo.")
                return
            self.run_full_transcription_job(ids=[self.current_interview_id])

        def run_pending_transcription_job(self, *_args: Any) -> None:
            ids = self.pending_transcription_ids()
            if not ids:
                QMessageBox.information(self, "Nada pendente", "Todos os arquivos do projeto já têm transcrição editável.")
                return
            self.run_full_transcription_job(ids=ids)

        def retranscribe_current_file(self, *_args: Any) -> None:
            """Refazer a transcricao do arquivo aberto, com escolha de modelo
            entre os instalados (caso de uso: comparar modelos). O dialogo
            ja avisa da recriacao — confirmed_recreate evita pergunta dupla."""
            interview_id = self.current_interview_id
            if not interview_id:
                QMessageBox.information(self, "Abra um arquivo",
                                        "Abra uma transcrição antes de refazê-la.")
                return
            if not self.save_current_turn(force=True):
                return
            from . import model_manager as _mm
            instalados = list(_mm.installed_asr_variants())
            atual = str(self.context.config.get("asr_model") or "") if self.context else ""
            dialog = RetranscribeDialog(interview_id, instalados, atual, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            escolhido = dialog.selected_model() or atual
            self.run_full_transcription_job(
                ids=[interview_id],
                asr_model=escolhido or None,
                confirmed_recreate=True,
            )

        def run_manifest_job(self) -> None:
            if not self.save_current_turn():
                return
            self.start_worker("Recarregar lista", [("Procurando gravações...", lambda: app_service.refresh_manifest(self.context))])

        def run_full_transcription_job(self, ids: list[str] | None = None, *,
                                       asr_model: str | None = None,
                                       confirmed_recreate: bool = False,
                                       diarize_now: bool | None = None) -> None:
            # diarize_now: decisao SO deste lote (caixa "Separar falantes
            # agora" da janela pre-transcricao em CPU); None = perguntar.
            # asr_model: modelo escolhido SO para esta rodada (Transcrever
            # novamente...) — sobrescreve o baseline com consentimento e nao
            # toca a config do projeto. Nada aqui usa asr_variant (singular),
            # que e a pasta A/B 02_asr_variants exclusiva da CLI.
            if not self.save_current_turn():
                return
            ids = ids or self.selected_ids_for_job(fallback_current=True)
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista para transcrever.")
                return
            # Etapa 4: o gate considera os IDIOMAS do lote (default do
            # projeto + metadado por arquivo) — pacote de alinhamento
            # faltante e oferecido ANTES; idioma sem pacote e avisado
            # ANTES (o WhisperX antigo estourava DEPOIS de transcrever).
            langs_lote, avisos_idioma = app_service.alignment_languages_for(self.context, ids)
            # E4-4: motor com tempos por palavra NATIVOS (Parakeet) nao
            # usa alinhador — nada a exigir/avisar. Mas ele so transcreve
            # portugues: outro idioma no lote bloqueia AQUI, antes do job
            # (o runner tem o mesmo guard como defesa para a CLI).
            from . import model_manager as _mm_motor
            motor_key = asr_model or str((self.context.config.get("asr_model")
                                          if self.context else "") or "")
            motor_spec = _mm_motor.ASR_VARIANTS.get(motor_key) or {}
            # Oferta única (2026-09-01): Whisper em CPU + lote em pt →
            # oferecer o TAGARELA (padrão de máquinas sem GPU). Só no fluxo
            # normal — "Transcrever novamente" é escolha explícita.
            if asr_model is None and self._maybe_offer_parakeet_cpu(
                    motor_spec, set(langs_lote) | set(avisos_idioma)):
                motor_key = "parakeet-pt"
                motor_spec = _mm_motor.ASR_VARIANTS.get(motor_key) or {}
            if motor_spec.get("engine") == "parakeet_onnx":
                fora_pt = sorted(languages_outside_pt(set(langs_lote) | set(avisos_idioma)))
                if fora_pt:
                    # 2026-09-02: em vez de um aviso com OK, a janela explica,
                    # ensina onde o modelo mora e oferece o Whisper de reserva
                    # (com a qualidade de cada opcao) so para o lote ou para o
                    # projeto; "Configurar transcrição…" abre o dialogo do motor.
                    from . import capabilities as _caps_ol
                    reserva = _caps_ol.recommended_asr_variants(_caps_ol.hardware_snapshot())[-1]
                    opcoes = whisper_reserve_options(_mm_motor.installed_asr_variants(), reserva)
                    dialog_ol = OtherLanguageEngineDialog(fora_pt, opcoes, self)
                    if dialog_ol.exec() != QDialog.DialogCode.Accepted:
                        if dialog_ol.wants_settings:
                            self.configure_engine()
                        return
                    escolhido = dialog_ol.chosen_model()
                    if not escolhido:
                        return
                    if dialog_ol.apply_to_project():
                        if not self._switch_engine_to_whisper(escolhido):
                            return
                        self.run_full_transcription_job(
                            ids=ids, confirmed_recreate=confirmed_recreate, diarize_now=diarize_now)
                    else:
                        self.run_full_transcription_job(
                            ids=ids, asr_model=escolhido, confirmed_recreate=confirmed_recreate,
                            diarize_now=diarize_now)
                    return
                langs_lote, avisos_idioma = (), ()
                # Oferta unica da aceleracao GPU (nao bloqueia: qualquer
                # resposta segue transcrevendo — so muda CPU vs GPU).
                self._maybe_offer_parakeet_gpu()
            if not self.ensure_models_ready(
                    asr_variants=[asr_model] if asr_model else None,
                    align_languages=langs_lote,
                    retry=lambda i=list(ids), m=asr_model, c=confirmed_recreate:
                        self.run_full_transcription_job(ids=i, asr_model=m,
                                                        confirmed_recreate=c)):
                return
            from . import app_settings as _settings_e4
            if avisos_idioma and _settings_e4.alignment_default():
                QMessageBox.information(
                    self, "Sem tempos por palavra",
                    "Alguns arquivos deste lote serão transcritos SEM tempos "
                    f"por palavra (idioma: {', '.join(avisos_idioma)}).\n\n"
                    "O texto e os tempos por bloco saem normalmente. Idiomas "
                    "com pacote de alinhamento podem ser escolhidos no Motor "
                    "(link \"Modelo\"/\"Motor\" na barra de status, embaixo).")
            # Tri-state "auto" (2026-08-31): decidir UMA vez por lote, no
            # momento do job. Auto resolvido para "sem falantes" (modelo
            # nao instalado) avisa com todas as letras — antes o lote saia
            # sem falantes em silencio e o usuario concluia que o app nao
            # separava.
            do_diarize, motivo_diarize = app_service.diarize_effective(
                self.context.config or {})
            if not do_diarize and motivo_diarize:
                QMessageBox.information(
                    self, "Sem separação de falantes",
                    "Este lote será transcrito SEM separar quem fala: "
                    f"{motivo_diarize}.\n\n"
                    "O texto sai normalmente. Depois de instalar o recurso, "
                    "a própria lista oferece um botão para separar as vozes "
                    "sem transcrever de novo.")
            # Retranscrever e uma decisao EXPLICITA (pipeline-safety): o
            # baseline sera sobrescrito e a transcricao editavel recriada.
            # confirmed_recreate=True vem de fluxos que ja avisaram
            # (Transcrever novamente…), para nao perguntar duas vezes.
            ja_transcritas: list[str] = []
            for iid in ids:
                status = self.status_by_interview_id(iid)
                if status is not None and (status.review_exists or status.canonical_exists):
                    ja_transcritas.append(iid)
            if ja_transcritas and not confirmed_recreate:
                n = len(ja_transcritas)
                answer = QMessageBox.question(
                    self, "Transcrever novamente?",
                    (f"{n} arquivo(s) selecionado(s) já {'tem' if n == 1 else 'têm'} "
                     "transcrição.\n\n"
                     "Transcrever de novo recria a transcrição editável DO ZERO — "
                     "edições manuais serão descartadas. Uma cópia de segurança das "
                     "versões com edições fica em:\n"
                     "Transcricoes\\05_transcripts_review\\edits\\backups\n\nContinuar?"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            if do_diarize:
                # Uma janela por lote: contagem de falantes (so para arquivos
                # nunca configurados) e, sem GPU, a estimativa honesta de tempo
                # com a caixa "Separar falantes agora" (2026-09-02: em CPU a
                # separacao leva ~0,4x o audio e dominava o lote; o texto pode
                # ficar pronto em minutos e as vozes virem depois, em lote).
                pending = ids_without_speaker_setup(self.context.metadata, ids)
                from . import runtime as _runtime_pre
                device_pre = _runtime_pre.resolve_device(
                    str(self.context.config.get("asr_device") or "auto"))[0]
                estimate_text = None
                # Estimativa + caixa "Separar falantes agora" quando algo vai
                # rodar em CPU: maquina sem GPU, ou TAGARELA sem o pacote
                # onnx-gpu numa maquina CUDA (2026-09-02).
                modelo_pre = asr_model or str(self.context.config.get("asr_model") or "")
                asr_em_cpu = device_pre != "cuda"
                if not asr_em_cpu and (_mm_motor.ASR_VARIANTS.get(modelo_pre) or {}).get("engine") == "parakeet_onnx":
                    from . import parakeet_runner as _pk_pre
                    asr_em_cpu = _pk_pre.planned_device(device_pre) == "cpu"
                if asr_em_cpu and diarize_now is None:
                    estimate_text = self._batch_estimate_text(ids, modelo_pre, device_pre)
                if pending or estimate_text:
                    dialog = SpeakerCountDialog(
                        len(pending), self, estimate_text=estimate_text, ask_counts=bool(pending))
                    if dialog.exec() != QDialog.DialogCode.Accepted:
                        return
                    if pending:
                        try:
                            self.context = app_service.update_file_metadata(
                                self.context, pending, dialog.updates())
                        except Exception as exc:
                            _logger.warning("Falha ao gravar configuracao de falantes: %s", exc)
                    if estimate_text and diarize_now is None:
                        diarize_now = dialog.diarize_now()
                if diarize_now is not False:
                    self._reset_speakers_confirmed(ids)
            steps: list[tuple] = []
            weights: list[int] = []
            # Dynamic weights from benchmark data (tests/benchmark_exhaustive_2026-04-19.csv)
            # `or` DENTRO do str(): config com `asr_model: null` virava a
            # string "None" nos overrides.
            asr_model = asr_model or str(self.context.config.get("asr_model") or _mm_motor.DEFAULT_ASR_VARIANT)
            # Rotulo do job diz o MOTOR certo (dizia "Whisper" ate com o TAGARELA).
            motor_label = ("o TAGARELA"
                           if (_mm_motor.ASR_VARIANTS.get(asr_model) or {}).get("engine") == "parakeet_onnx"
                           else f"o Whisper ({asr_model})")
            # _pipeline_weights espera o device EFETIVO ("cuda"/"cpu") — com o
            # default "auto" (v0.2+) e preciso resolver antes do lookup.
            from . import runtime as _runtime_w
            asr_device = _runtime_w.resolve_device(str(self.context.config.get("asr_device") or "auto"))[0]
            # diarize_now=False (caixa "Separar falantes agora" desmarcada) vale
            # SO para este lote: nada e gravado no run_config.yaml.
            do_diarize, do_boundary = job_step_flags(
                do_diarize, bool(self.context.config.get("boundary_check", True)), diarize_now)
            w5 = _pipeline_weights(asr_model, asr_device)
            # 7 fases: [prepare, asr, diarize, render, conferir trocas,
            # recriar transcricao editavel, qc]. Conferencia e recriacao
            # (pos-render) tem peso pequeno e fixo: custam segundos.
            w = [
                w5[0], w5[1], w5[2] if do_diarize else 0, w5[3],
                1 if do_boundary else 0, 1, w5[4],
            ]
            # Pesos POR STEP: a lista precisa ter exatamente um item por step
            # montado — desalinhar quebra o progresso a partir do 2o arquivo.
            included = [True, True, do_diarize, True, do_boundary, True, True]
            step_w = [weight for used, weight in zip(included, w) if used]
            boundaries = [0]
            for v in w:
                boundaries.append(boundaries[-1] + v)
            total_w = boundaries[-1] or 100
            r = [int(b * 100 / total_w) for b in boundaries]
            r[-1] = 100
            render_overrides = {"diarization_source": "pyannote_exclusive"} if do_diarize else {}
            for interview_id in ids:
                self.context = app_service.update_job(
                    self.context,
                    interview_id,
                    {"status": "Na fila", "stage": "aguardando", "progress": 0, "queued_at": datetime.now().isoformat(timespec="seconds"), "last_error": ""},
                )
            # B2 (2026-09-02): com 2+ arquivos, o preparo (ffmpeg) vira UM
            # passo do lote, em paralelo (2 por vez), antes das transcricoes;
            # o passo de transcricao recusa quem falhou (skip-and-continue).
            self._prepare_failed = set()
            preparo_em_lote = len(ids) >= 2
            if preparo_em_lote:
                steps.append((
                    f"Preparando {len(ids)} áudios...",
                    lambda progress, should_cancel, alvo=list(ids), lo=r[0], hi=r[1]: self._prepare_batch(
                        alvo, lo, hi, progress, should_cancel),
                    True,
                ))
                weights.append(max(1, w[0] * len(ids)))
            for index, interview_id in enumerate(ids, start=1):
                prefix = f"{index}/{len(ids)} {interview_id}"
                file_steps = []
                if not preparo_em_lote:
                    file_steps.append(
                        self.job_step(f"{prefix}: convertendo o áudio para WAV 16 kHz...", interview_id, "preparar audio", r[0], r[1], lambda item=interview_id: app_service.prepare_interviews(self.context, ids=[item])),
                    )
                file_steps.append(
                    self.job_step(
                        f"{prefix}: transcrevendo com {motor_label}...",
                        interview_id,
                        "transcrever",
                        r[1],
                        r[2],
                        lambda progress, should_cancel, item=interview_id: self._transcribe_prepared(
                            item, asr_model, progress, should_cancel),
                        accepts_progress=True,
                    ),
                )
                if do_diarize:
                    file_steps.append(
                        self.job_step(
                            f"{prefix}: separando as vozes (pyannote)...",
                            interview_id,
                            "identificar falantes",
                            r[2],
                            r[3],
                            lambda progress, should_cancel, item=interview_id: self._diarize_then_channels(
                                item, progress, should_cancel,
                            ),
                            accepts_progress=True,
                            # Falha na diarizacao nao derruba a transcricao:
                            # o render cai para o modo sem falantes (v0.2).
                            optional=True,
                        ),
                    )
                file_steps.append(
                    # overrides decididos NA HORA do render: se a diarizacao
                    # (opcional) falhou, o exclusive.json nao existe e o render
                    # cai para o modo sem falantes em vez de falhar o lote.
                    # Fonte do render decidida NA HORA: canais informativos
                    # (fase 4) tem prioridade; senao pyannote_exclusive; senao
                    # modo sem falantes.
                    # asr_model tambem no render: o canonical grava o modelo
                    # do config (render.py) — sem o override, uma rodada com
                    # outro modelo registraria o modelo ERRADO no canonical
                    # e na review.
                    self.job_step(f"{prefix}: montando transcricao editavel...", interview_id, "montar transcricao", r[3], r[4], lambda item=interview_id: app_service.render_interviews(self.context, ids=[item], overrides={
                        "asr_model": asr_model,
                        **({"diarization_source": "channels"} if self._channels_diarization_exists(item)
                           else (render_overrides if self._exclusive_diarization_exists(item) else {}))})),
                )
                if do_boundary:
                    file_steps.append(
                        self.job_step(
                            f"{prefix}: conferindo trocas de falante...",
                            interview_id,
                            "conferir trocas",
                            r[4],
                            r[5],
                            lambda progress, should_cancel, item=interview_id: self._boundary_check_via_subprocess(
                                item, progress, should_cancel,
                            ),
                            accepts_progress=True,
                            # Passo de conferencia: falha nunca derruba o lote.
                            optional=True,
                        ),
                    )
                file_steps.append(
                    # Recriar a transcricao editavel do canonical NOVO: sem
                    # isto, retranscrever nao mudava nada visivel (a review
                    # antiga ficava valendo no editor e nos exports). Vem
                    # DEPOIS da conferencia de trocas (que escreve so no
                    # canonical) e ANTES da verificacao. rebuild_review faz
                    # backup quando ha edicoes humanas — consentidas na
                    # confirmacao "Transcrever novamente?" do inicio do job.
                    self.job_step(
                        f"{prefix}: recriando transcricao editavel...",
                        interview_id,
                        "recriar transcricao",
                        r[5],
                        r[6],
                        lambda item=interview_id: app_service.rebuild_review(self.context, item),
                    ),
                )
                file_steps.append(
                    self.job_step(f"{prefix}: verificando arquivos gerados...", interview_id, "verificar arquivos", r[6], r[7], lambda item=interview_id: app_service.qc_interviews(self.context, ids=[item])),
                )
                steps.extend(file_steps)
                # step_w[0] e o peso do preparo, ja contado no passo do lote.
                weights.extend(step_w[1:] if preparo_em_lote else step_w)
            if self.current_interview_id and self.current_interview_id in ids:
                # O job vai RECRIAR a review deste arquivo: congelar o editor
                # fecha a corrida autosave x rebuild_review (texto digitado
                # durante o job era gravado e depois sobrescrito/perdido).
                # on_worker_done recarrega e reabilita; on_worker_failed idem.
                self.set_editor_enabled(False)
            self.refresh_interviews()
            # B1: servidor de separacao para o lote (abre agora, carrega em
            # paralelo). C: as vozes destes arquivos entram na faixa ao fim.
            if do_diarize:
                self._start_diarize_server(len(ids))
            self._voice_batch_ids = list(ids)
            self.start_worker(
                f"Transcrever {len(ids)} arquivo(s)",
                steps,
                weights=weights,
            )

        def job_step(
            self,
            message: str,
            interview_id: str,
            stage: str,
            start_progress: int,
            end_progress: int,
            func: Callable,
            accepts_progress: bool = False,
            optional: bool = False,
        ) -> tuple:
            # optional=True (v0.2): falha desta etapa NAO derruba o lote —
            # usado pela diarizacao dentro da transcricao completa ("transcricao
            # concluida, falantes pendentes" em vez de falha total).
            def run(
                progress_callback: Callable[[dict[str, Any]], None] | None = None,
                should_cancel: Callable[[], bool] | None = None,
            ) -> object:
                started_at = datetime.now().isoformat(timespec="seconds")
                started_mono = time.monotonic()
                app_service.update_job(
                    self.context,
                    interview_id,
                    {
                        "status": "Rodando",
                        "stage": stage,
                        "progress": start_progress,
                        "started_at": started_at,
                        "last_error": "",
                        "estimated_finish_at": "",
                    },
                )

                def relay(detail: dict[str, Any]) -> None:
                    try:
                        inner = max(0, min(100, int(detail.get("progress", 0))))
                    except (TypeError, ValueError):
                        inner = 0
                    mapped = start_progress + int(((end_progress - start_progress) * inner) / 100)
                    estimated_finish_at = ""
                    if mapped > 2:
                        elapsed = time.monotonic() - started_mono
                        if elapsed >= 8:
                            remaining = elapsed * ((100 - mapped) / max(1, mapped))
                            estimated_finish_at = (datetime.now() + timedelta(seconds=max(0, remaining))).isoformat(timespec="seconds")
                    app_service.update_job(
                        self.context,
                        interview_id,
                        {
                            "status": "Rodando",
                            "stage": stage,
                            "progress": mapped,
                            "estimated_finish_at": estimated_finish_at,
                        },
                    )
                    if progress_callback is not None:
                        forwarded = dict(detail)
                        forwarded["progress"] = inner
                        progress_callback(forwarded)

                PENDING_SPEAKERS_NOTE = "Identificação de falantes não concluída (transcrição segue sem separar falantes)."

                def _mark_optional_failure() -> object:
                    app_service.update_job(
                        self.context,
                        interview_id,
                        {
                            "status": "Rodando",
                            "stage": stage,
                            "progress": end_progress,
                            "last_error": PENDING_SPEAKERS_NOTE,
                            "estimated_finish_at": "",
                        },
                    )
                    return app_service.JobResult(stage, 0, PENDING_SPEAKERS_NOTE)

                def _cancel_pedido() -> bool:
                    try:
                        return bool(should_cancel and should_cancel())
                    except Exception:  # noqa: BLE001
                        return False

                def _mark_cancelled() -> None:
                    # Cancelar nao e falhar: o whisperx devolve o cancelamento
                    # como "1 falha(s)" e o job ficava "Falha" na tabela para
                    # uma acao que o proprio usuario pediu.
                    app_service.update_job(
                        self.context,
                        interview_id,
                        {
                            "status": "Pendente",
                            "stage": "",
                            "progress": 0,
                            "last_error": "",
                            "finished_at": "",
                            "estimated_finish_at": "",
                        },
                    )

                try:
                    result = func(relay, should_cancel or (lambda: False)) if accepts_progress else func()
                    failures = getattr(result, "failures", 0)
                    if failures:
                        if optional:
                            return _mark_optional_failure()
                        if _cancel_pedido():
                            _mark_cancelled()
                            return result
                        app_service.update_job(
                            self.context,
                            interview_id,
                            {
                                "status": "Falha",
                                "stage": stage,
                                "progress": start_progress,
                                "last_error": failure_summary(failures, getattr(result, "message", "")),
                                "finished_at": datetime.now().isoformat(timespec="seconds"),
                                "estimated_finish_at": "",
                            },
                        )
                    else:
                        updates = {"status": "Rodando", "stage": stage, "progress": end_progress, "last_error": "", "estimated_finish_at": ""}
                        # Preservar o aviso "falantes pendentes" de um step
                        # opcional anterior — sem isto o render/QC o apagariam.
                        prev_err = str(((self.context.jobs.get(interview_id) or {}).get("last_error")) or "")
                        if prev_err.startswith("Identificação de falantes não concluída"):
                            updates["last_error"] = prev_err
                        if end_progress >= 100:
                            updates["status"] = "Concluido"
                            updates["finished_at"] = datetime.now().isoformat(timespec="seconds")
                        app_service.update_job(
                            self.context,
                            interview_id,
                            updates,
                        )
                    return result
                except Exception as exc:
                    if optional:
                        _logger.warning("Etapa opcional '%s' de %s falhou: %s", stage, interview_id, exc)
                        return _mark_optional_failure()
                    if _cancel_pedido():
                        _mark_cancelled()
                        raise
                    app_service.update_job(
                        self.context,
                        interview_id,
                        {
                            "status": "Falha",
                            "stage": stage,
                            "progress": start_progress,
                            "last_error": str(exc)[-2000:],
                            "finished_at": datetime.now().isoformat(timespec="seconds"),
                            "estimated_finish_at": "",
                        },
                    )
                    raise

            # 4o campo = grupo (interview_id): o PipelineWorker usa para o
            # skip-and-continue — falha num arquivo pula so os steps dele.
            return (message, run, accepts_progress, interview_id)

        def _exclusive_diarization_exists(self, interview_id: str) -> bool:
            try:
                return (self.context.paths.diarization_dir / "json" / f"{interview_id}.exclusive.json").exists()
            except Exception:
                return False

        def _channels_diarization_exists(self, interview_id: str) -> bool:
            """channels.json com decisao informative (fase 4): os canais
            carregam microfones distintos e viram a fonte do render."""
            try:
                path = self.context.paths.diarization_dir / "json" / f"{interview_id}.channels.json"
                if not path.exists():
                    return False
                from .utils import read_json as _read_json
                return str(_read_json(path).get("decision")) == "informative"
            except Exception:
                return False

        def _render_source_overrides(self, interview_id: str) -> dict[str, Any]:
            """Fonte de falantes para REMONTAR este arquivo: a mesma decisao
            do fluxo principal (canais informativos > pyannote_exclusive >
            modo sem falantes). Forcar pyannote_exclusive fixo fazia o
            render falhar no perfil essencial, onde exclusive.json nao
            existe."""
            if self._channels_diarization_exists(interview_id):
                return {"diarization_source": "channels"}
            if self._exclusive_diarization_exists(interview_id):
                return {"diarization_source": "pyannote_exclusive"}
            return {}

        def _diarize_then_channels(
            self,
            interview_id: str,
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Diarizacao + analise de canais no MESMO passo (fase 4).

            A analise le os {id}.ch{n}.wav extraidos no preparo e casa os
            rotulos com os centroides do pyannote; e best-effort (nunca
            muda o resultado da diarizacao) e, em arquivos mono, nem o
            subprocesso e lancado.
            """
            result = self._diarize_via_subprocess(interview_id, progress_callback, should_cancel)
            channels_result = self._channels_via_subprocess(interview_id, progress_callback, should_cancel)
            if channels_result.failures:
                _logger.warning("analise de canais de %s falhou", interview_id)
            return result

        def _channels_via_subprocess(
            self,
            ids: str | list[str],
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Analise de canais via CLI em subprocesso (fase 4).

            Mesmo racional da diarizacao: a fusao de rotulos usa torch/
            pyannote e roda no processo filho. Fontes mono sao puladas
            aqui mesmo, sem custo de subprocesso.
            """
            from . import runtime as _rt
            from .channels import source_channels as _source_channels
            from .utils import parse_progress_json_line, run_command_stream
            id_list = [ids] if isinstance(ids, str) else list(ids)
            failures = 0
            for iid in id_list:
                if should_cancel is not None and should_cancel():
                    break
                row = next((r for r in self.context.rows if r["interview_id"] == iid), None)
                if row is None or _source_channels(row) < 2:
                    continue

                def on_output(line: str) -> None:
                    detail = parse_progress_json_line(line)
                    if detail is not None and progress_callback is not None:
                        progress_callback(detail)

                command = _rt.cli_command(
                    "--project", str(self.context.paths.project_root),
                    "channels", f"--ids={iid}", "--progress-json",
                )
                completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
                if completed.returncode != 0:
                    failures += 1
                    _logger.warning("analise de canais de %s saiu com codigo %s", iid, completed.returncode)
            return app_service.JobResult(
                "channels", failures,
                "" if failures == 0 else f"{failures} arquivo(s) com falha na analise de canais.",
            )

        def _diarize_via_subprocess(
            self,
            ids: str | list[str],
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Diarizacao via transcritorio-cli em SUBPROCESSO (v0.2).

            Crash de pyannote/torch/CUDA derruba o processo filho, nunca a
            GUI; cancelamento mata o subprocesso (terminate/kill do
            run_command_stream). Progresso chega por linhas '@PROGRESS {json}'
            no mesmo contrato de eventos do caminho in-process antigo."""
            from . import runtime as _rt
            from .utils import parse_progress_json_line, run_command_stream
            id_list = [ids] if isinstance(ids, str) else list(ids)
            failures = 0
            for iid in id_list:
                if should_cancel is not None and should_cancel():
                    break
                # B1 (2026-09-02): servidor do lote quando existe (modelo ja
                # carregado); se ele cair, fallback para o subprocesso por
                # arquivo — a rota de sempre.
                server = getattr(self, "_diarize_server", None)
                if server is not None:
                    resultado = server.run([iid], on_progress=progress_callback, should_cancel=should_cancel)
                    if resultado is not None:
                        failures += resultado
                        if resultado:
                            _logger.warning("diarizacao (servidor) de %s: %s falha(s)", iid, resultado)
                        continue
                    self._diarize_server = None
                    server.stop()
                    _logger.warning(
                        "servidor de diarizacao indisponivel (%s); usando subprocesso por arquivo",
                        server.failure_reason,
                    )

                def on_output(line: str) -> None:
                    detail = parse_progress_json_line(line)
                    if detail is not None and progress_callback is not None:
                        progress_callback(detail)

                command = _rt.cli_command(
                    "--project", str(self.context.paths.project_root),
                    "diarize", f"--ids={iid}", "--progress-json",
                )
                completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
                if completed.returncode != 0:
                    failures += 1
                    _logger.warning(
                        "diarizacao em subprocesso de %s saiu com codigo %s",
                        iid, completed.returncode,
                    )
            return app_service.JobResult(
                "diarize", failures,
                "" if failures == 0 else f"{failures} arquivo(s) com falha na diarizacao.",
            )

        def _boundary_check_via_subprocess(
            self,
            ids: str | list[str],
            progress_callback: Callable[[dict[str, Any]], None] | None = None,
            should_cancel: Callable[[], bool] | None = None,
        ) -> app_service.JobResult:
            """Verificacao acustica de fronteiras via CLI em subprocesso.

            Mesmo racional da diarizacao: torch/pyannote roda no processo
            filho e um crash nunca derruba a GUI."""
            from . import runtime as _rt
            from .utils import parse_progress_json_line, run_command_stream
            id_list = [ids] if isinstance(ids, str) else list(ids)
            failures = 0
            for iid in id_list:
                if should_cancel is not None and should_cancel():
                    break

                def on_output(line: str) -> None:
                    detail = parse_progress_json_line(line)
                    if detail is not None and progress_callback is not None:
                        progress_callback(detail)

                command = _rt.cli_command(
                    "--project", str(self.context.paths.project_root),
                    "check-boundaries", f"--ids={iid}", "--progress-json",
                )
                completed = run_command_stream(command, on_output=on_output, should_cancel=should_cancel)
                if completed.returncode != 0:
                    failures += 1
                    _logger.warning(
                        "verificacao de fronteiras de %s saiu com codigo %s",
                        iid, completed.returncode,
                    )
            return app_service.JobResult(
                "boundary-check", failures,
                "" if failures == 0 else f"{failures} arquivo(s) com falha na verificacao de fronteiras.",
            )

        def run_diarization_job(self, ids: list[str] | None = None) -> None:
            # R3: sem entrada de menu — chega aqui pelo banner de oferta da
            # lista (que passa os ids sem separacao) ou por fluxos internos.
            if not self.save_current_turn():
                return
            if not self.ensure_models_ready(require_diarization=True,
                                            retry=lambda: self.run_diarization_job(ids)):
                return
            if ids is None:
                ids = self.selected_ids_for_job()
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista para identificar falantes.")
                return
            # Quem transcreveu com "Separar falantes agora" desmarcado nunca
            # respondeu quantas pessoas falam — perguntar aqui (uma vez por lote).
            if not self._ask_speaker_counts_if_needed(ids):
                return
            self._reset_speakers_confirmed(ids)
            # Diarizar SEM remontar nao mudava nada visivel; o fluxo
            # completo: diarizacao -> render por arquivo -> reviews pristinas.
            steps: list[tuple] = [(
                "Identificando falantes...",
                lambda progress, should_cancel: self._diarize_via_subprocess(ids, progress, should_cancel),
                True,
            )]
            steps.extend(
                (f"Remontando transcricao ({item})...",
                 lambda item=item: app_service.render_interviews(
                     self.context, ids=[item],
                     overrides=self._render_source_overrides(item)))
                for item in ids
            )
            steps.append((
                "Atualizando transcrições editáveis...",
                lambda alvo=list(ids): app_service.refresh_unedited_reviews(self.context, alvo),
            ))
            self._start_diarize_server(len(ids))
            self._voice_batch_ids = list(ids)
            self.start_worker("Identificar falantes", steps)

        def improve_speakers_current_file(self, *_args: Any) -> None:
            if not self.current_interview_id:
                QMessageBox.information(self, "Abra uma entrevista",
                                        "Abra uma transcrição antes de refazer a separação de falantes.")
                return
            if not self.save_current_turn(force=True):
                return
            if not self.ensure_models_ready(require_diarization=True,
                                            retry=self.improve_speakers_current_file):
                return
            interview_id = self.current_interview_id
            # Destrutiva nomeia o alvo (guia verbal, regra 7) e usa a
            # frase-contrato padronizada (regra 6).
            meta = (self.context.metadata.get(interview_id) or {}) if self.context else {}
            nome = str(meta.get("title") or "").strip() or interview_id
            answer = QMessageBox.question(
                self,
                "Refazer separação de falantes",
                f"Refazer a separação de vozes de \"{nome}\"?\n\n"
                "A transcrição é recriada do zero. Suas edições serão "
                "descartadas — guardamos uma cópia em Documentos › "
                "Versões anteriores.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._reset_speakers_confirmed([interview_id])
            steps = [
                self.job_step(
                    f"{interview_id}: identificando falantes...",
                    interview_id,
                    "identificar falantes",
                    0,
                    70,
                    lambda progress, should_cancel, item=interview_id: self._diarize_via_subprocess(
                        item, progress, should_cancel,
                    ),
                    accepts_progress=True,
                ),
                self.job_step(
                    f"{interview_id}: remontando transcricao editavel...",
                    interview_id,
                    "montar transcricao",
                    70,
                    95,
                    # Mesma decisao por arquivo dos demais fluxos (canais
                    # informativos > exclusive > sem falantes) — aqui o
                    # exclusive acabou de ser gerado, mas canais podem vencer.
                    lambda item=interview_id: app_service.render_interviews(self.context, ids=[item], overrides=self._render_source_overrides(item)),
                ),
                self.job_step(
                    f"{interview_id}: recriando transcricao editavel...",
                    interview_id,
                    "recriar transcricao",
                    95,
                    100,
                    lambda item=interview_id: app_service.rebuild_review(self.context, item),
                ),
            ]
            # O passo final recria a review do arquivo aberto: congelar o
            # editor (mesma corrida autosave x rebuild do fluxo principal).
            self.set_editor_enabled(False)
            self.start_worker(f"Melhorar falantes de {interview_id}", steps, weights=[70, 25, 5])

        def run_summarize_job(self, *_args: Any, ids: list[str] | None = None) -> None:
            """Resumo com indice tematico (fase 2.1) — analise 100% local.
            ids: escopo explicito (a janela Perguntar oferece "Resumir as N
            entrevistas agora" para a visao geral); sem ids, a selecao."""
            if self._explain_busy("Resumir a entrevista com AI"):
                return
            if not self.save_current_turn():
                return
            if not self._ensure_llm_model():
                return
            ids = list(ids) if ids else self.selected_ids_for_job(fallback_current=True)
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Abra ou selecione uma entrevista transcrita para gerar o resumo.")
                return
            total = len(ids)
            steps = [
                self.job_step(
                    f"{index + 1}/{total} {iid}: gerando resumo com temas...",
                    iid,
                    "gerar resumo",
                    int(100 * index / total),
                    int(100 * (index + 1) / total),
                    lambda progress, should_cancel, item=iid: app_service.summarize_interviews(
                        self.context, ids=[item], progress_callback=progress, should_cancel=should_cancel,
                    ),
                    accepts_progress=True,
                    optional=True,
                )
                for index, iid in enumerate(ids)
            ]
            self.start_worker(f"Gerar resumo de {total} arquivo(s)", steps)
            # Ponte minima (feedback 2026-08-26: "nao sei onde salvou"):
            # ao concluir, mostrar onde ficou com Abrir resumo/Abrir pasta.
            self._pending_summary_ids = list(ids)

        def _open_current_resumo(self) -> None:
            if not self.current_interview_id or self.context is None:
                return
            from .summarize import resumo_path as _resumo_path
            path = _resumo_path(self.context.paths, self.current_interview_id)
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        # ---- aba Documentos (R2) ------------------------------------------
        def _on_review_tab_changed(self, _index: int) -> None:
            if getattr(self, "docs_panel", None) is not None and \
                    self.review_tabs.currentWidget() is self.docs_panel:
                self._refresh_docs_panel()
            if getattr(self, "_props_tab", None) is not None and \
                    self.review_tabs.currentWidget() is self._props_tab:
                self._refresh_props_panel()

        # ---- aba Propriedades (R2) ----------------------------------------
        def _build_props_panel(self) -> QWidget:
            """Propriedades da entrevista aberta (R4: edicao inline).

            Os 6 campos informativos seguem QLabels; lingua, falantes,
            rotulos e contexto sao EDITAVEIS aqui, com salvar explicito
            (auto-apply e inviavel: gravar rotulos implica confirmar
            vozes, e a oferta de remontagem abre dialogo). O
            MetadataDialog sobrevive como porta do LOTE."""
            self._props_tab = QWidget()
            raiz = QVBoxLayout(self._props_tab)
            raiz.setContentsMargins(ui_tokens.SP_3, ui_tokens.SP_3,
                                    ui_tokens.SP_3, ui_tokens.SP_3)
            self._props_empty = QLabel(
                "Abra uma entrevista para ver as propriedades dela.")
            self._props_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._props_empty.setStyleSheet(
                f"color: {ui_tokens.TEXT_MUTED}; font-size: {ui_tokens.FONT_TITLE}px;")
            raiz.addWidget(self._props_empty)
            self._props_grid_widget = QWidget()
            grid = QGridLayout(self._props_grid_widget)
            grid.setColumnStretch(1, 1)
            self._props_values: dict[str, QLabel] = {}
            campos = [
                ("rotulo", "Rótulo"),
                ("id", "Identificador"),
                ("arquivo", "Gravação original"),
                ("formato", "Formato"),
                ("duracao", "Duração"),
                ("situacao", "Situação"),
            ]
            for linha, (chave, rotulo) in enumerate(campos):
                nome = QLabel(rotulo + ":")
                nome.setStyleSheet(f"color: {ui_tokens.TEXT_MUTED};")
                valor = QLabel("—")
                valor.setWordWrap(True)
                valor.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(nome, linha, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(valor, linha, 1)
                self._props_values[chave] = valor

            def _nome(texto: str, linha: int) -> None:
                rotulo = QLabel(texto)
                rotulo.setStyleSheet(f"color: {ui_tokens.TEXT_MUTED};")
                grid.addWidget(rotulo, linha, 0, Qt.AlignmentFlag.AlignTop)

            base = len(campos)
            self._props_dirty_fields: set[str] = set()
            self._props_loaded_iid: str | None = None
            # Lingua: item "Padrão do projeto" (data "") permite voltar ao
            # default global — e e o estado de arquivo nunca configurado.
            _nome("Língua:", base)
            self._props_lang_combo = QComboBox()
            self._props_lang_combo.addItem("Padrão do projeto", "")
            from . import model_manager as _mm_lang
            _ordenados = sorted(_mm_lang.ALIGN_LANGUAGES.items(),
                                key=lambda kv: (kv[0] != "pt", kv[1]["label"]))
            self._props_lang_combo.addItem(_ordenados[0][1]["label"], "pt")
            self._props_lang_combo.addItem("Automático", "auto")
            for _code, _spec in _ordenados[1:]:
                self._props_lang_combo.addItem(str(_spec["label"]), _code)
            self._props_lang_combo.currentIndexChanged.connect(
                lambda _i: self._touch_props("language"))
            # Nota inline (2026-09-02): projeto no TAGARELA + idioma do arquivo
            # que nao e portugues — avisa e ensina, sem bloquear.
            self._props_lang_note = QLabel("")
            self._props_lang_note.setWordWrap(True)
            self._props_lang_note.setStyleSheet(f"color: {ui_tokens.WARN}; font-weight: 600;")
            self._props_lang_note.setVisible(False)
            self._props_lang_combo.currentIndexChanged.connect(lambda _i: self._refresh_props_lang_note())
            _lang_box = QVBoxLayout()
            _lang_box.setContentsMargins(0, 0, 0, 0)
            _lang_box.addWidget(self._props_lang_combo)
            _lang_box.addWidget(self._props_lang_note)
            grid.addLayout(_lang_box, base, 1)
            # Falantes: modo + spins (mesma semantica do dialogo de lote).
            _nome("Falantes esperados:", base + 1)
            falantes_row = QHBoxLayout()
            self._props_mode_combo = QComboBox()
            for value, label in [("exact", "Número exato"),
                                 ("auto", "Automático"),
                                 ("range", "Intervalo")]:
                self._props_mode_combo.addItem(label, value)
            self._props_count_spin = QSpinBox()
            self._props_count_spin.setRange(1, 20)
            self._props_count_spin.setValue(2)
            self._props_min_spin = QSpinBox()
            self._props_min_spin.setRange(1, 20)
            self._props_min_spin.setValue(2)
            self._props_max_spin = QSpinBox()
            self._props_max_spin.setRange(1, 20)
            self._props_max_spin.setValue(4)
            for w in (self._props_mode_combo, self._props_count_spin,
                      self._props_min_spin, self._props_max_spin):
                falantes_row.addWidget(w)
            falantes_row.addStretch(1)
            self._props_mode_combo.currentIndexChanged.connect(
                lambda _i: (self._touch_props("falantes"),
                            self._sync_props_speaker_widgets()))
            for spin in (self._props_count_spin, self._props_min_spin,
                         self._props_max_spin):
                spin.valueChanged.connect(lambda _v: self._touch_props("falantes"))
            falantes_w = QWidget()
            falantes_w.setLayout(falantes_row)
            grid.addWidget(falantes_w, base + 1, 1)
            # Rotulos e contexto.
            _nome("Rótulos dos falantes:", base + 2)
            self._props_labels_edit = QLineEdit()
            self._props_labels_edit.setPlaceholderText("Entrevistador | Entrevistado")
            self._props_labels_edit.textEdited.connect(
                lambda _t: self._touch_props("rotulos"))
            grid.addWidget(self._props_labels_edit, base + 2, 1)
            _nome("Contexto:", base + 3)
            self._props_context_edit = QTextEdit()
            self._props_context_edit.setPlaceholderText(
                "Use poucas frases com nomes, termos e assunto.")
            self._props_context_edit.setMaximumHeight(90)
            self._props_context_edit.textChanged.connect(
                lambda: self._touch_props("contexto"))
            grid.addWidget(self._props_context_edit, base + 3, 1)
            self._props_use_context = QCheckBox(
                "Usar este contexto como auxílio na transcrição")
            self._props_use_context.toggled.connect(
                lambda _c: self._touch_props("contexto"))
            grid.addWidget(self._props_use_context, base + 4, 1)
            raiz.addWidget(self._props_grid_widget)
            rodape = QHBoxLayout()
            self._props_save_button = QPushButton("Salvar propriedades")
            self._props_save_button.setEnabled(False)
            self._props_save_button.setToolTip(
                "Grava apenas o que você alterou nesta aba.")
            self._props_save_button.clicked.connect(self._save_props_from_tab)
            rodape.addWidget(self._props_save_button)
            rodape.addWidget(self.action_button(self.apply_metadata_action))
            dica = QLabel("Para editar várias entrevistas de uma vez, "
                          "selecione-as na lista.")
            dica.setStyleSheet(f"color: {ui_tokens.TEXT_MUTED};")
            rodape.addWidget(dica)
            rodape.addStretch(1)
            raiz.addLayout(rodape)
            raiz.addStretch(1)
            return self._props_tab

        def _refresh_props_lang_note(self) -> None:
            """Nota "TAGARELA só transcreve português" sob o idioma do arquivo."""
            note = getattr(self, "_props_lang_note", None)
            combo = getattr(self, "_props_lang_combo", None)
            if note is None or combo is None:
                return
            modelo = str(self.context.config.get("asr_model") or "") if self.context else ""
            texto = engine_language_note(modelo, str(combo.currentData() or ""))
            note.setText(texto)
            note.setVisible(bool(texto))

        def _touch_props(self, campo: str) -> None:
            """Gesto do usuario num campo editavel da aba (os populates
            programaticos rodam com blockSignals e nao chegam aqui)."""
            self._props_dirty_fields.add(campo)
            busy = bool(self.worker and self.worker.isRunning())
            self._props_save_button.setEnabled(not busy)

        def _sync_props_speaker_widgets(self) -> None:
            modo = str(self._props_mode_combo.currentData())
            self._props_count_spin.setVisible(modo == "exact")
            self._props_min_spin.setVisible(modo == "range")
            self._props_max_spin.setVisible(modo == "range")

        def _save_props_from_tab(self) -> None:
            iid = self.current_interview_id
            if not iid or self.context is None:
                return
            if self._props_loaded_iid != iid:
                # Defesa em profundidade (auditoria 2026-09-01): o form na
                # tela pertence a OUTRA entrevista (a cascata de refresh do
                # open_review normalmente impede isso, mas gravar o form de
                # A nos metadados de B seria corrupcao silenciosa — recusar
                # e re-sincronizar e sempre seguro).
                self._props_dirty_fields.clear()
                self._refresh_props_panel()
                self.progress_label.setText(
                    "As propriedades na tela estavam desatualizadas — "
                    "recarregadas. Confira e salve de novo.")
                return
            if self.worker and self.worker.isRunning():
                QMessageBox.information(
                    self, "Tarefa em andamento",
                    "Aguarde a tarefa atual terminar para salvar as propriedades.")
                return
            form = {
                "language": str(self._props_lang_combo.currentData() or ""),
                "speaker_mode": str(self._props_mode_combo.currentData() or ""),
                "speaker_count": self._props_count_spin.value(),
                "min_speakers": self._props_min_spin.value(),
                "max_speakers": self._props_max_spin.value(),
                "speaker_labels": "|".join(
                    parte.strip() for parte in
                    self._props_labels_edit.text().replace(",", "|").split("|")
                    if parte.strip()),
                "context_text": self._props_context_edit.toPlainText(),
                "use_context": self._props_use_context.isChecked(),
            }
            atual = self.context.metadata.get(iid, {})
            updates = props_metadata_updates(
                atual, form, set(self._props_dirty_fields))
            self._props_dirty_fields.clear()
            self._props_save_button.setEnabled(False)
            if not updates:
                self.progress_label.setText("Nada mudou nas propriedades.")
                return
            self._apply_metadata_updates([iid], updates)
            self.progress_label.setText("Propriedades salvas.")
            self._refresh_props_panel()

        def _refresh_props_panel(self) -> None:
            if getattr(self, "_props_values", None) is None:
                return
            iid = self.current_interview_id
            status = self.status_by_interview_id(iid) if iid else None
            tem = bool(iid and status is not None and self.context is not None)
            self._props_empty.setVisible(not tem)
            self._props_grid_widget.setVisible(tem)
            if not tem:
                self._props_loaded_iid = None
                self._props_dirty_fields.clear()
                self._props_save_button.setEnabled(False)
                return
            metadata = self.context.metadata.get(iid, {})
            job = self.context.jobs.get(iid, {})
            origem = str(getattr(status, "source_path", "") or "")
            if origem:
                existe = (self.context.paths.project_root / origem).exists() \
                    or Path(origem).exists()
                if not existe:
                    origem += "  (gravação não encontrada)"
            valores = {
                "rotulo": str(metadata.get("title") or "").strip() or iid,
                "id": iid,
                "arquivo": origem or "—",
                "formato": media_format_label(status),
                "duracao": format_clock(float(status.duration_sec)
                                        if status.duration_sec else 0),
                "situacao": self.friendly_state(status, job),
            }
            for chave, valor in valores.items():
                self._props_values[chave].setText(str(valor) or "—")
            # Form editavel: NAO sobrescrever o que o usuario esta digitando
            # (refresh roda de ~15 lugares, inclusive fim de worker). Trocar
            # de entrevista descarta o form nao salvo — com aviso.
            if self._props_dirty_fields and self._props_loaded_iid == iid:
                # Fim de job com edicao pendente: reavaliar o Salvar (o
                # _touch_props o desabilita com worker ativo e nada o
                # reabilitava depois — auditoria 2026-09-01).
                self._props_save_button.setEnabled(
                    not (self.worker and self.worker.isRunning()))
                return
            if self._props_dirty_fields and self._props_loaded_iid:
                self.progress_label.setText(
                    "As propriedades editadas de "
                    f"\"{self._props_loaded_iid}\" não foram salvas.")
            self._props_dirty_fields.clear()
            self._props_save_button.setEnabled(False)
            self._props_loaded_iid = iid
            widgets = (self._props_lang_combo, self._props_mode_combo,
                       self._props_count_spin, self._props_min_spin,
                       self._props_max_spin, self._props_labels_edit,
                       self._props_context_edit, self._props_use_context)
            for w in widgets:
                w.blockSignals(True)
            try:
                lingua = str(metadata.get("language") or "")
                indice = self._props_lang_combo.findData(lingua)
                self._props_lang_combo.setCurrentIndex(max(0, indice))
                self._refresh_props_lang_note()
                modo = str(metadata.get("speaker_mode") or "") or "exact"
                indice = self._props_mode_combo.findData(modo)
                self._props_mode_combo.setCurrentIndex(max(0, indice))
                self._props_count_spin.setValue(
                    int(metadata.get("speaker_count") or 2))
                self._props_min_spin.setValue(
                    int(metadata.get("min_speakers") or 2))
                self._props_max_spin.setValue(
                    int(metadata.get("max_speakers") or 4))
                self._props_labels_edit.setText(
                    str(metadata.get("speaker_labels") or "").replace("|", " | "))
                self._props_context_edit.setPlainText(
                    str(metadata.get("context_text") or ""))
                self._props_use_context.setChecked(
                    str(metadata.get("use_context_as_prompt") or "") == "true")
            except (TypeError, ValueError):
                pass  # metadado ilegivel: form fica no default
            finally:
                for w in widgets:
                    w.blockSignals(False)
            self._sync_props_speaker_widgets()

        def _show_path_in_folder(self, caminho: str) -> None:
            if sys.platform == "win32":
                # Linha de comando pronta, com aspas SO no caminho (ver o
                # outro /select, acima): virgula no nome abria Documentos.
                subprocess.Popen(f'explorer /select,"{caminho}"')
            else:
                open_folder_in_explorer(Path(caminho).parent)

        def _docs_action(self, chave: str) -> None:
            """Roteia os botoes da aba Documentos para os fluxos existentes
            (mesmos gates/confirmacoes de sempre — redundancia estrategica)."""
            rotas = {
                "exportar": self.export_reviews,
                "gerar_resumo": self.run_summarize_job,
                "gerar_glossario": self.run_glossario_job,
                "revisar_grafias": self.open_spelling_review,
                "verificar": self.run_qc_job,
                "abrir_resultados": self.open_export_folder,
                "abrir_nomes_conhecidos": self._open_nomes_conhecidos,
            }
            handler = rotas.get(chave)
            if handler is not None:
                handler()

        def _open_nomes_conhecidos(self) -> None:
            """Abre o contexto da pesquisa (seção "## Nomes conhecidos") —
            acesso que morava só no QMessageBox do glossário (morto na R4)."""
            if self.context is None:
                return
            from .research_context import context_path as _context_path
            caminho = _context_path(self.context.paths)
            if caminho.exists():
                open_folder_in_explorer(caminho)

        def _docs_data(self, caminho: Path) -> str:
            try:
                from datetime import datetime as _dt
                return _dt.fromtimestamp(caminho.stat().st_mtime).strftime("%d/%m/%Y")
            except OSError:
                return ""

        def _refresh_docs_panel(self) -> None:
            """Reconstroi a aba Documentos a partir do disco (fonte da
            verdade). Chamado ao abrir a aba, ao abrir/fechar entrevista e
            ao fim de jobs — nunca em update_action_states (globs custam)."""
            if getattr(self, "docs_panel", None) is None:
                return
            from .ui_docs_panel import DocEntry
            if self.context is None:
                self.docs_panel.set_sections(None, [], [])
                return

            desta: list[DocEntry] = []
            titulo = None
            iid = self.current_interview_id
            if iid:
                meta = (self.context.metadata.get(iid) or {})
                titulo = str(meta.get("title") or iid)
                alvo = self._results_folder_for_user()
                for chave, rotulo, ext, frase in [
                    ("export_docx", "Transcrição final (Word)", "docx",
                     "ainda não exportada"),
                    ("export_md", "Transcrição final (texto)", "md",
                     "ainda não exportada"),
                    ("export_srt", "Legendas (SRT)", "srt",
                     "ainda não exportadas"),
                ]:
                    achado: Path | None = None
                    try:
                        achado = next(iter(sorted(alvo.rglob(f"{glob.escape(iid)}*.{ext}"))), None)
                    except OSError:
                        achado = None
                    if achado is not None:
                        desta.append(DocEntry(
                            chave, rotulo, estado="existe",
                            detalhe=f"exportada em {self._docs_data(achado)}",
                            caminho=str(achado)))
                    else:
                        desta.append(DocEntry(
                            chave, rotulo, estado="ausente", detalhe=frase,
                            acao_rotulo="Exportar…", acao_chave="exportar"))
                from .summarize import resumo_path as _resumo_path
                resumo = _resumo_path(self.context.paths, iid)
                if resumo.exists():
                    desta.append(DocEntry(
                        "resumo", "Resumo com temas", ai=True, estado="existe",
                        detalhe=f"gerado em {self._docs_data(resumo)}",
                        caminho=str(resumo)))
                else:
                    desta.append(DocEntry(
                        "resumo", "Resumo com temas", ai=True, estado="ausente",
                        detalhe="ainda não gerado",
                        acao_rotulo="✨ Gerar", acao_chave="gerar_resumo"))
                backups_dir = self.context.paths.review_dir / "edits" / "backups"
                try:
                    n_backups = len(list(backups_dir.glob(f"{glob.escape(iid)}*")))
                except OSError:
                    n_backups = 0
                if n_backups:
                    desta.append(DocEntry(
                        "backups", f"Versões anteriores ({n_backups})",
                        estado="existe", caminho=str(backups_dir)))

            projeto: list[DocEntry] = []
            from .glossario import glossary_report_path as _glos_path
            glos = _glos_path(self.context.paths)
            if glos.exists():
                projeto.append(DocEntry(
                    "glossario", "Glossário de nomes", ai=True, estado="existe",
                    detalhe=f"gerado em {self._docs_data(glos)}",
                    caminho=str(glos),
                    extras=(("Revisar grafias…", "revisar_grafias"),)))
            else:
                projeto.append(DocEntry(
                    "glossario", "Glossário de nomes", ai=True,
                    estado="ausente", detalhe="ainda não gerado",
                    acao_rotulo="✨ Gerar", acao_chave="gerar_glossario"))
            qc_csv = self.context.paths.qc_dir / "qc_metrics.csv"
            if qc_csv.exists():
                projeto.append(DocEntry(
                    "verificacao", "Relatório de verificação", estado="existe",
                    detalhe=f"gerado em {self._docs_data(qc_csv)}",
                    caminho=str(qc_csv)))
            else:
                projeto.append(DocEntry(
                    "verificacao", "Relatório de verificação",
                    estado="ausente", detalhe="ainda não gerado",
                    acao_rotulo="Gerar", acao_chave="verificar"))

            self.docs_panel.set_sections(titulo, desta, projeto)

        def _show_summary_results(self, ids: list[str]) -> None:
            """Fim do resumo anunciado NA CASA (R4): faixa de sucesso na
            aba Documentos + troca para ela — a linha do resumo aparece
            atualizada logo abaixo. O QMessageBox modal da ponte morreu
            (interrompia; o banner e estritamente menos invasivo)."""
            from .summarize import resumo_path as _resumo_path
            if self.context is None:
                return
            produced = [
                _resumo_path(self.context.paths, iid)
                for iid in ids
                if _resumo_path(self.context.paths, iid).exists()
            ]
            if not produced:
                return
            n = len(produced)
            texto = ("Resumo com temas pronto." if n == 1
                     else f"{n} resumos com temas prontos.")
            self.docs_panel.show_success(texto, caminho=str(produced[0]))
            self.review_tabs.setCurrentWidget(self.docs_panel)

        def _ensure_optional_model(self, key: str, titulo: str, motivo: str,
                                   needs_llm_env: bool = False) -> bool:
            """Baixa um modelo opcional sob demanda, com progresso.

            Nunca clique-morto: ou o modelo esta pronto, ou o usuario ve o
            que falta, quanto pesa, qual requisito de hardware se aplica e
            quanto disco sobra — e decide. Vale para TODAS as acoes de
            AI — antes cada uma resolvia (ou nao) do seu jeito, e o modelo
            do resumo nao tinha baixador nenhum na interface.

            needs_llm_env: a capacidade tambem exige o ambiente de analise
            local (~3 GB de bibliotecas instaladas na primeira execucao) —
            declarar ANTES do aceite, nao surpreender durante o job."""
            from . import capabilities as _caps
            from . import llm_env as _llm_env
            from . import model_manager
            if getattr(self, "_model_download_busy", False):
                # Reentrancia: o download roda na thread da GUI com
                # processEvents — um segundo clique (gerenciador/Perguntar
                # sao janelas irmas, nao bloqueadas) iniciaria outro.
                QMessageBox.information(self, "Download em andamento",
                                        "Aguarde o download atual terminar.")
                return False
            asset = model_manager.asset_by_key(key)
            env_pendente = bool(needs_llm_env) and not _llm_env.llm_env_ready()
            # ~3 GB e o ambiente com torch CUDA; o conjunto CPU (glossario
            # em maquina sem placa) fica em ~1,5 GB — cobrar 3 barrava quem
            # cabia no disco.
            from . import runtime as _rt_env
            extra_gb = (3.0 if _rt_env.has_nvidia_gpu() else 1.5) if env_pendente else 0.0
            disk = model_manager.check_disk_space(float(asset.estimated_gb) + extra_gb)
            if not disk.get("ok"):
                QMessageBox.warning(self, "Espaço em disco insuficiente",
                                    str(disk.get("message") or ""))
                return False
            partes = [motivo]
            cap = _caps.capability_for_model(key)
            if cap is not None and cap.needs_gpu:
                cache = getattr(self, "_caps_cache", None)
                hw = cache[0] if cache else _caps.hardware_snapshot()
                linha = (f"Requer placa de vídeo NVIDIA com pelo menos "
                         f"{cap.min_vram_gb:.0f} GB de memória de vídeo")
                if hw.vram_gb:
                    linha += f" — a sua tem {hw.vram_gb:.0f} GB"
                partes.append(linha + ".")
                aviso_hw = _caps.hardware_warning(cap, hw)
                if aviso_hw:
                    partes.append(f"Atenção: {cap.label} {aviso_hw}. "
                                  "Baixar e usar é por sua conta e risco.")
            if env_pendente:
                partes.append("Na primeira utilização, o aplicativo também prepara "
                              f"um ambiente de análise local (~{extra_gb:.1f} GB "
                              "adicionais, baixados uma vez).")
            if getattr(asset, "license_notice", ""):
                partes.append(str(asset.license_notice))
            partes.append(f"Baixar agora (uma vez, ~{asset.estimated_gb:.1f} GB)?\n"
                          f"Espaço livre em disco: {disk.get('free_gb', 0):.1f} GB.")
            answer = QMessageBox.question(
                self, f"Baixar {titulo}?",
                "\n\n".join(partes),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            dialog = QProgressDialog(f"Baixando {titulo}...", "Cancelar", 0, 100, self)
            # So a primeira letra: .capitalize() rebaixava o resto e
            # produzia "Analise local (qwen3.5-4b)".
            dialog.setWindowTitle(titulo[:1].upper() + titulo[1:])
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setAutoClose(False)
            dialog.show()

            def on_progress(detail: dict) -> None:
                dialog.setValue(max(0, min(100, int(detail.get("progress") or 0))))
                if detail.get("message"):
                    dialog.setLabelText(str(detail["message"]))
                QApplication.processEvents()

            self._model_download_busy = True
            try:
                failures = model_manager.download_optional_model(
                    key, progress_callback=on_progress, should_cancel=dialog.wasCanceled)
            finally:
                self._model_download_busy = False
                dialog.close()
            if failures:
                QMessageBox.warning(
                    self, "Download não concluído",
                    f"Nao foi possivel baixar {titulo}. Verifique a conexao e tente de novo.")
                return False
            self._invalidate_capability_cache()  # o que estava indisponivel virou disponivel
            return True

        def _ensure_ner_model(self) -> bool:
            from .glossario import glossary_ready
            ready, reason = glossary_ready()
            if ready:
                return True
            return self._ensure_optional_model("ner_gliner", "o modelo de nomes",
                                               reason, needs_llm_env=True)

        def _ensure_llm_model(self) -> bool:
            """Modelo do resumo/perguntar. O criterio de aptidao e o registro
            de capacidades (VRAM minima incluida): sem maquina apta nao
            adianta baixar 8,7 GB — ai o limite e a maquina, nao o
            download. summarize_ready fica como guarda de segunda linha."""
            from .summarize import summarize_ready
            estado, motivo_cap, _gb = self._capability_state("resumo_perguntar")
            if estado == "incompativel":
                QMessageBox.information(self, "Analise local indisponivel", motivo_cap)
                return False
            ready, reason = summarize_ready()
            if ready:
                return True
            return self._ensure_optional_model("llm_qwen", "o modelo de análise",
                                               reason, needs_llm_env=True)

        def run_glossario_job(self) -> None:
            """Glossario de nomes do projeto (lote 6a) — varredura unica."""
            if self._explain_busy("Glossário de nomes com AI"):
                return
            if not self.save_current_turn():
                return
            if self.context is None:
                QMessageBox.information(self, "Abra um projeto", "Abra um projeto para montar o glossario.")
                return
            from . import search as _search
            transcritas = list(dict.fromkeys(
                r["interview_id"] for r in self.context.rows
                if _search.source_path_for(self.context.paths, r["interview_id"]) is not None
            ))
            if not transcritas:
                QMessageBox.information(
                    self, "Nenhuma transcrição",
                    "O glossário lê as transcrições, não o áudio — e nenhum arquivo "
                    "deste projeto foi transcrito ainda.")
                return
            if not self._ensure_ner_model():
                return
            total = len(transcritas)
            steps = [
                self.job_step(
                    f"lendo os nomes citados em {total} transcricao(oes)...",
                    transcritas[0], "glossario de nomes", 0, 100,
                    lambda progress, should_cancel: app_service.build_glossary_interviews(
                        self.context, ids=transcritas,
                        progress_callback=progress, should_cancel=should_cancel,
                    ),
                    accepts_progress=True,
                    optional=True,
                )
            ]
            self.start_worker(f"Glossario de nomes ({total} arquivo(s))", steps)
            # Depois do start_worker (que zera a flag): senao a limpeza de
            # higiene a apagaria antes do job comecar.
            self._pending_glossario = True

        def _show_glossario_results(self) -> None:
            """Fim do glossário anunciado NA CASA (R4): faixa de sucesso na
            aba Documentos. Os acessos do QMessageBox antigo sobrevivem
            como botões do banner (Revisar grafias… / Abrir nomes
            conhecidos); nada foi alterado nas transcrições — o glossário
            é só leitura."""
            from .glossario import glossary_report_path, load_glossary
            if self.context is None:
                return
            report = glossary_report_path(self.context.paths)
            if not report.exists():
                return
            glossary = load_glossary(self.context.paths)
            entradas = glossary.get("entradas") or []
            com_variantes = [e for e in entradas if e.get("variantes")]
            texto = (
                f"Glossário pronto — {len(entradas)} nomes encontrados"
                + (f", {len(com_variantes)} com grafias diferentes a conferir."
                   if com_variantes else ".")
            )
            extras = ([("Revisar grafias…", "revisar_grafias")]
                      if com_variantes else [])
            extras.append(("Abrir nomes conhecidos", "abrir_nomes_conhecidos"))
            self.docs_panel.show_success(texto, caminho=str(report), extras=extras)
            self.review_tabs.setCurrentWidget(self.docs_panel)

        def open_spelling_review(self) -> None:
            """Revisao de grafias (lote 6b): so aplica o que o usuario marcar."""
            from . import glossario as _gl
            if self._explain_busy("Revisar grafias de nomes"):
                return
            if self.context is None:
                QMessageBox.information(self, "Abra um projeto", "Abra um projeto para revisar as grafias.")
                return
            if not self.save_current_turn():
                return
            # Tres estados HONESTOS (teste real 2026-08-31: "nada a
            # corrigir" saia sem a analise nunca ter rodado — o usuario
            # concluiu que estava tudo certo): nunca analisado -> oferecer
            # analisar AGORA; analise defasada (entrevistas transcritas
            # depois) -> oferecer re-analisar; em dia -> verdade.
            from . import search as _search
            glossario = _gl.load_glossary(self.context.paths)
            # dict.fromkeys: linhas duplicadas do manifesto (mesmo id com
            # selected=false) inflavam a contagem de "defasadas" (auditoria
            # 2026-09-01; 4 duplicatas reais no acervo Infocitizen).
            transcritas = list(dict.fromkeys(
                r["interview_id"] for r in self.context.rows
                if _search.source_path_for(self.context.paths, r["interview_id"]) is not None
            ))
            if not glossario:
                answer = QMessageBox.question(
                    self, "Analisar os nomes primeiro",
                    "As entrevistas ainda não foram analisadas — a revisão de "
                    "grafias parte do glossário de nomes, que ainda não "
                    "existe neste projeto.\n\nAnalisar agora? (✨ AI local; ao "
                    "terminar, o aviso na aba Documentos traz o botão "
                    "\"Revisar grafias…\".)")
                if answer == QMessageBox.StandardButton.Yes:
                    self.run_glossario_job()
                return
            defasadas = _gl.glossary_coverage_gap(glossario, transcritas)
            if defasadas:
                n = len(defasadas)
                texto = (
                    f"{n} entrevistas foram transcritas depois da última "
                    "análise de nomes e ainda não entraram no glossário."
                    if n > 1 else
                    "1 entrevista foi transcrita depois da última análise "
                    "de nomes e ainda não entrou no glossário.")
                answer = QMessageBox.question(
                    self, "Analisar as novas também?",
                    texto + "\n\nAnalisar de novo agora? (Ao terminar, o "
                    "aviso na aba Documentos traz o botão \"Revisar "
                    "grafias…\". Responda Não para revisar só o que já foi "
                    "analisado.)")
                if answer == QMessageBox.StandardButton.Yes:
                    self.run_glossario_job()
                    return
            pendentes = _gl.pending_variants(self.context.paths)
            if not pendentes:
                QMessageBox.information(
                    self, "Nada a revisar",
                    "Os nomes já analisados não têm variações de grafia a "
                    "conferir.\n\nPara melhorar a detecção, declare os nomes "
                    "corretos na seção \"## Nomes conhecidos\" do contexto "
                    "da pesquisa e gere o glossário de novo (menu Analisar).")
                return
            ids = [r["interview_id"] for r in self.context.rows]
            grupos = []
            for pendente in pendentes:
                ocorrencias = _gl.collect_occurrences(
                    self.context.paths, ids, pendente["variante"], pendente["canonico"])
                if ocorrencias:
                    grupos.append({**pendente, "ocorrencias": ocorrencias})
            if not grupos:
                QMessageBox.information(
                    self, "Nada a revisar",
                    "As variações do glossário não aparecem mais nas transcrições "
                    "revisadas — provavelmente já foram corrigidas.")
                return
            dialog = SpellingReviewDialog(self, grupos)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            decisoes = dialog.selected()
            if not decisoes:
                self.progress_label.setText("Nenhuma grafia foi alterada.")
                return
            aberta = self.current_interview_id
            before = deepcopy(self.review) if self.review else None
            try:
                resultado = _gl.apply_corrections(self.context.paths, decisoes)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Não foi possível corrigir", sanitize_message(str(exc)))
                return
            if aberta and any(str(d["interview_id"]) == aberta for d in decisoes):
                # Recarrega do disco: apply_corrections gravou por fora do
                # objeto em memoria desta janela.
                try:
                    self.review = app_service.load_review(self.context, aberta, create=True)
                    self.turns = review_store.review_turns(self.review)
                    self.load_turn_table()
                    if before is not None:
                        self.undo_stack.push(ReviewSnapshotCommand(
                            self, "Corrigir grafias", before, self.review, self.current_turn_id))
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("Falha ao recarregar %s apos correcao: %s", aberta, exc)
            self.set_save_state(saved_status_message())
            QMessageBox.information(
                self, "Grafias corrigidas",
                f"{resultado['ocorrencias']} ocorrência(s) corrigida(s) em "
                f"{resultado['blocos']} bloco(s) de {resultado['arquivos']} arquivo(s).\n\n"
                "Cópias de segurança das revisões ficam em "
                "05_transcripts_review/edits/backups/.\n"
                "Gere o glossário de novo para atualizar a lista.")
            self.progress_label.setText(
                f"{resultado['ocorrencias']} grafia(s) corrigida(s).")

        def run_qc_job(self) -> None:
            if not self.save_current_turn():
                return
            ids = self.selected_ids_for_job()
            if not ids:
                answer = QMessageBox.question(
                    self,
                    "Verificar todas?",
                    "Nenhuma entrevista foi selecionada. Deseja verificar os arquivos gerados de todas as entrevistas?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.start_worker("Verificar exportações", [("Verificando exportações...", lambda: app_service.qc_interviews(self.context, ids=ids))])

        def start_worker(self, label: str, steps: list[tuple], weights: list[int] | None = None) -> None:
            if self.worker and self.worker.isRunning():
                # Ultima defesa para caminhos nao-AI (as acoes de AI explicam
                # na faixa via _explain_busy).
                QMessageBox.information(
                    self,
                    "Tarefa em andamento",
                    busy_click_text("Esta ação", self._busy_state_text())
                    + "\n\nO aplicativo não está travado.",
                )
                return
            self.current_job_label = label
            self._pending_summary_ids = None  # notificacao so do job que a setar
            # Mesma higiene para o glossario: a flag sobrevivia a uma falha e
            # a janela de resultados abria sozinha no fim de OUTRO job.
            self._pending_glossario = False
            # R4: banner de sucesso da aba Documentos anuncia SEMPRE o
            # ultimo resultado — job novo comecando o dispensa.
            if getattr(self, "docs_panel", None) is not None:
                self.docs_panel.clear_success()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            self.cancel_job_button.setVisible(True)
            self.progress_label.setText(f"{label} em andamento...")
            self.worker = PipelineWorker(label, steps, weights=weights)
            self.worker.progress.connect(self.on_worker_progress)
            self.worker.finished_ok.connect(self.on_worker_done)
            self.worker.failed.connect(self.on_worker_failed)
            self.worker.start()
            self.update_action_states()

        def cancel_current_job(self) -> None:
            if not self.worker or not self.worker.isRunning():
                return
            self.worker.request_cancel_after_step()
            self.progress_label.setText("Cancelamento solicitado.")
            self.cancel_job_action.setEnabled(False)

        def _reset_orphan_queue_jobs(self) -> None:
            """Com o worker encerrado, jobs 'Na fila'/'Rodando' remanescentes sao
            orfaos (cancelamento ou falha de lote) e bloqueariam rename/mover/
            lixeira para sempre; resetar para Pendente."""
            if self.context is None:
                return
            try:
                orphans = [
                    iid for iid, j in (self.context.jobs or {}).items()
                    if (j or {}).get("status") in ("Na fila", "Rodando")
                ]
                for iid in orphans:
                    self.context = app_service.update_job(self.context, iid, {
                        "status": "Pendente",
                        "stage": "",
                        "progress": 0,
                        "last_error": "",
                        "estimated_finish_at": "",
                    })
            except Exception as exc:
                _logger.warning("reset de jobs orfaos falhou: %s", exc)

        def on_worker_progress(self, message: str, percent: int) -> None:
            if percent < self.progress_bar.value():
                return  # Ignore stale signals — never regress text or bar
            self.progress_label.setText(message)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, percent)))
            self._refresh_running_rows()
            self._sync_busy_hints(True)

        def _refresh_running_rows(self, force: bool = False) -> None:
            """Coluna Transcrição por arquivo DURANTE o lote (2026-09-02).

            A thread do lote grava o avanco de cada arquivo em jobs.json
            (job_step/relay), mas a lista so era redesenhada no fim: todos
            ficavam em "Processando 0%". Aqui, no maximo a cada 1,5 s, o
            jobs.json e lido e so as CELULAS mudam — nada de reconstruir a
            tabela (selecao, rolagem e a transcricao aberta ficam intactas).
            Quem sai de "Rodando" tem a linha recalculada ("Transcrita",
            "(WAV pronto)").
            """
            if self.context is None or not hasattr(self, "interview_table"):
                return
            agora = time.monotonic()
            if not force and agora - getattr(self, "_rows_refresh_at", 0.0) < 1.5:
                return
            self._rows_refresh_at = agora
            from . import project_store as _ps_rows
            try:
                jobs = _ps_rows.read_json(_ps_rows.jobs_path(self.context.paths)) or {}
            except Exception:  # noqa: BLE001 - arquivo em escrita: tenta no proximo sinal
                return
            anteriores: set[str] = getattr(self, "_rows_running", set())
            em_andamento: set[str] = set()
            for row_idx in range(self.interview_table.rowCount()):
                id_item = self.interview_table.item(row_idx, COL_ARQUIVO)
                state_item = self.interview_table.item(row_idx, COL_TRANSCRICAO)
                if not id_item or not state_item:
                    continue
                iid = str(id_item.data(Qt.ItemDataRole.UserRole) or "")
                job = jobs.get(iid) or {}
                texto = row_progress_text(job)
                if texto is not None:
                    em_andamento.add(iid)
                    if state_item.text() != texto:
                        state_item.setText(texto)
                elif iid in anteriores:
                    try:
                        from .status import collect_status as _collect
                        estados = _collect(self.context.rows, self.context.paths, ids=[iid])
                    except Exception:  # noqa: BLE001
                        estados = []
                    if estados:
                        state_item.setText(self.friendly_state(estados[0], job))
                        fmt_item = self.interview_table.item(row_idx, COL_FORMATO)
                        if fmt_item:
                            fmt_item.setText(media_format_label(estados[0]))
            self._rows_running = em_andamento

        def on_worker_done(self, message: str) -> None:
            finished_label = self.current_job_label
            self._stop_diarize_server()
            self._release_batch_models()
            # Qualquer job pode ter mexido em modelos (Preparar modelos,
            # downloads no meio de acoes): invalidar o retrato de
            # capacidades evita tooltips/notas mentindo apos o download.
            self._invalidate_capability_cache()
            # Jobs produzem documentos (exports, resumo, glossario, QC):
            # se a aba Documentos esta a vista, refletir na hora.
            self._on_review_tab_changed(-1)
            self.progress_bar.setRange(0, 100)
            # "cancelado" e interrupcao tanto quanto "interrompido": tratar
            # igual (sem barra em 100%, sem dialogos de resultado).
            interrompido = ("interrompido" in message) or ("cancelado" in message)
            if interrompido:
                self.progress_bar.setValue(max(0, min(100, self.progress_bar.value())))
            else:
                self.progress_bar.setValue(100)
            self.current_job_label = ""
            self._reset_orphan_queue_jobs()
            self.refresh_interviews()
            self._sync_busy_hints(False)
            # DEPOIS do refresh: ele termina sobrescrevendo o progress_label
            # com "N entrevista(s) na lista" — a mensagem de conclusao
            # ("concluído com N falhas", "Modelos prontos") nunca ficava
            # visivel.
            self.progress_label.setText(message)
            # Retomada da acao que esperava modelos (F2): o download e
            # assincrono, entao e AQUI que "a acao original segue".
            retry = getattr(self, "_retry_after_models", None)
            if retry is not None and finished_label == "Preparar modelos":
                self._retry_after_models = None
                if not interrompido:
                    QTimer.singleShot(0, retry)
            if self.current_interview_id:
                current_id = self.current_interview_id
                status = self.status_by_interview_id(current_id)
                try:
                    if status and (status.review_exists or status.canonical_exists):
                        self.review = app_service.load_review(self.context, current_id, create=True)
                        self.turns = review_store.review_turns(self.review)
                        self.set_editor_enabled(True)
                        self.review_title.setText(self._review_title_text(current_id))
                        # Retranscrever muda tambem as palavras: recarregar o
                        # word_index junto com a review.
                        from . import words as words_mod
                        try:
                            self.word_index = words_mod.load_word_index(self.context.paths, current_id)
                        except Exception:  # noqa: BLE001 - palavras sao opcionais
                            self.word_index = []
                        self._word_uncertain_cutoff = words_mod.uncertain_threshold(self.word_index)
                        self.load_turn_table()
                        if self.turns:
                            self.select_turn_by_index(0, seek=False)
                        # A transcricao pode ter acabado de criar o WAV
                        # preparado: renovar a lista de midias e apontar o
                        # player para ele. Sem isto, quem abriu o arquivo
                        # ANTES de transcrever fica preso ao original (MP3
                        # VBR = seek com desvio crescente) no player E no
                        # dialogo de vozes — causa raiz da dessincronia
                        # vista em uso real (2026-08-25, D05R).
                        try:
                            self.media_candidates = app_service.get_media_candidates(self.context, current_id)
                            if self.media_candidates:
                                preferred_index = preferred_media_index(self.media_candidates)
                                preferred = self.media_candidates[preferred_index]
                                current_source = Path(self.player.source().toLocalFile()) if self.player.source().isLocalFile() else None
                                if current_source != preferred:
                                    self.set_media_source(preferred_index)
                                    self.load_waveform()
                        except Exception as exc:  # noqa: BLE001 - midia e acessoria aqui
                            _logger.warning("Falha ao renovar midia de %s: %s", current_id, exc)
                        # As marcas de palavra SO DEPOIS do load_waveform:
                        # set_waveform zera os ticks, e a ordem antiga
                        # apagava as marcas recem-calculadas.
                        cutoff = self._word_uncertain_cutoff
                        self.waveform_widget.set_words([
                            (word["start"],
                             cutoff is not None and word["score"] is not None and word["score"] <= cutoff)
                            for word in self.word_index])
                except Exception as exc:
                    _logger.warning("Falha ao recarregar review de %s: %s", current_id, exc)
            self.update_action_states()
            self._update_voice_banner()
            if interrompido:
                self._voice_batch_ids = []
            self._update_voice_batch_banner()
            pending_summaries = getattr(self, "_pending_summary_ids", None)
            self._pending_summary_ids = None
            if pending_summaries and not interrompido:
                self._show_summary_results(pending_summaries)
            if getattr(self, "_pending_glossario", False):
                self._pending_glossario = False
                if not interrompido:
                    self._show_glossario_results()
            # R4: a verificacao terminava SEM anuncio nenhum — ganha o
            # mesmo banner, mas SEM roubar a aba (relatorio e secundario).
            if (self.current_job_label == "Verificar exportações"
                    and not interrompido and self.context is not None):
                relatorio = self.context.paths.qc_dir / "qc_metrics.csv"
                if relatorio.exists():
                    self.docs_panel.show_success(
                        "Relatório de verificação pronto.",
                        caminho=str(relatorio))
            if self._close_after_worker:
                self._close_after_worker = False
                self.close()
                return
            if self.current_interview_id and self.review and not interrompido:
                # Fluxo "transcrever o arquivo aberto": a transcricao aparece
                # aqui SEM passar por open_review — a pergunta "De quem e esta
                # voz?" precisa disparar tambem neste caminho (buraco pego no
                # 1o uso real, 2026-08-23). QTimer: o QThread do job ainda
                # esta finalizando e o guard de worker ocioso barraria agora.
                QTimer.singleShot(
                    200,
                    lambda item=self.current_interview_id: self._offer_voice_naming_after_job(item),
                )

        def on_worker_failed(self, message: str) -> None:
            self._stop_diarize_server()
            self._release_batch_models()
            self._voice_batch_ids = []
            self.progress_bar.setRange(0, 100)
            self.progress_label.setText("Falha.")
            self.progress_bar.setValue(0)
            self.current_job_label = ""
            self._retry_after_models = None  # download falhou: nao retomar
            self._invalidate_capability_cache()
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle("Não foi possível concluir a tarefa")
            dialog.setText("A tarefa terminou com erro.")
            dialog.setInformativeText("Verifique a entrevista selecionada, o token/modelo quando houver separação de falantes, e tente novamente.")
            dialog.setDetailedText(message)
            dialog.exec()
            self._reset_orphan_queue_jobs()
            self.refresh_interviews()
            # Depois do refresh (que sobrescreve o progress_label).
            self.progress_label.setText("A tarefa terminou com erro.")
            # O job pode ter congelado o editor do arquivo aberto; com a
            # review ainda valida no disco, devolver a edicao.
            if self.current_interview_id and self.review:
                self.set_editor_enabled(True)
            self.update_action_states()
            if self._close_after_worker:
                self._close_after_worker = False
                self.close()

        def closeEvent(self, event: Any) -> None:
            if self.worker and self.worker.isRunning():
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Icon.Question)
                msg.setWindowTitle("Tarefa em andamento")
                msg.setText(f"{self.current_job_label or 'Uma tarefa'} ainda esta em andamento.")
                wait_btn = msg.addButton("Aguardar", QMessageBox.ButtonRole.RejectRole)
                msg.addButton("Fechar mesmo assim", QMessageBox.ButtonRole.AcceptRole)
                msg.setDefaultButton(wait_btn)
                msg.exec()
                if msg.clickedButton() == wait_btn:
                    event.ignore()
                    return
                # Force close: sinaliza cancel e aguarda graciosamente.
                # NAO chamar terminate() — corrompe copy/CUDA/tokenizer in-flight (bug 3).
                self.worker.cancel_after_step = True
                if not self.worker.wait(15000):
                    _logger.warning(
                        "Transcription worker did not stop gracefully within 15s; "
                        "detaching thread (QThread vivo nunca pode ser destruido — "
                        "crash na saida + risco de truncar jobs.json/review)."
                    )
                    # Manter referencia global impede o GC de destruir o QThread
                    # em execucao quando a janela morre.
                    _ABANDONED_WORKERS.append(self.worker)
                    self.worker = None
            # Servidor de separacao do lote (B1): nao sobreviver a janela.
            self._stop_diarize_server()
            self._release_batch_models()
            # Trash worker: NUNCA terminate() (pode corromper copy in-flight)
            if getattr(self, "_trash_worker", None) is not None and self._trash_worker.isRunning():
                self._trash_worker.request_cancel()
                self._trash_worker.wait()  # bloqueante — sem timeout
            # Purga interativa da lixeira da sessao
            try:
                self._maybe_purge_session_trash()
            except Exception as exc:
                _logger.warning("_maybe_purge_session_trash falhou: %s", exc)
            if not self.save_current_turn():
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle("Erro ao salvar")
                msg.setText(
                    "Não foi possível salvar as alterações pendentes da transcrição.\n"
                    "Se fechar agora, as edições não salvas serão perdidas."
                )
                keep_btn = msg.addButton("Cancelar fechamento", QMessageBox.ButtonRole.RejectRole)
                msg.addButton("Fechar mesmo assim", QMessageBox.ButtonRole.AcceptRole)
                msg.setDefaultButton(keep_btn)
                msg.exec()
                if msg.clickedButton() == keep_btn:
                    event.ignore()
                    return
            self.player.stop()
            event.accept()


# Workers que nao pararam a tempo no closeEvent: manter vivos ate o exit do
# processo em vez de deixar o GC destruir um QThread em execucao (crash).
_ABANDONED_WORKERS: list[Any] = []


def _apply_dark_theme(app) -> None:
    """Aplica tema escuro global (Fusion + QPalette dark).

    Fusion ignora o tema do SO (evita variacao claro/escuro conforme o Windows);
    QPalette forca cores escuras consistentes. Desabilitados com cor apagada."""
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtWidgets import QStyleFactory
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    # Paleta do Programa R (ui_tokens; dossie RD aprovado 2026-08-31):
    # Window/Button = BG_RAISED, Base = BG_BASE, selecao = INFO.
    def tok(color: str) -> QColor:
        return QColor(*ui_tokens.hex_to_rgb(color))

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, tok(ui_tokens.BG_RAISED))
    pal.setColor(QPalette.ColorRole.WindowText, tok(ui_tokens.TEXT))
    pal.setColor(QPalette.ColorRole.Base, tok(ui_tokens.BG_BASE))
    pal.setColor(QPalette.ColorRole.AlternateBase, tok(ui_tokens.BG_RAISED))
    pal.setColor(QPalette.ColorRole.ToolTipBase, tok(ui_tokens.BG_OVERLAY))
    pal.setColor(QPalette.ColorRole.ToolTipText, tok(ui_tokens.TEXT))
    pal.setColor(QPalette.ColorRole.Text, tok(ui_tokens.TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, tok(ui_tokens.TEXT_MUTED))
    pal.setColor(QPalette.ColorRole.Button, tok(ui_tokens.BG_RAISED))
    pal.setColor(QPalette.ColorRole.ButtonText, tok(ui_tokens.TEXT))
    pal.setColor(QPalette.ColorRole.BrightText, tok(ui_tokens.DANGER))
    pal.setColor(QPalette.ColorRole.Link, tok(ui_tokens.INFO))
    pal.setColor(QPalette.ColorRole.Highlight, tok(ui_tokens.INFO))
    pal.setColor(QPalette.ColorRole.HighlightedText, tok(ui_tokens.ON_ACCENT))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(127, 127, 127))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 80))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(200, 200, 200))
    app.setPalette(pal)
    # Tooltip explicito (em alguns estilos o ToolTipBase do palette nao cobre tudo)
    app.setStyleSheet(
        f"QToolTip {{ color: {ui_tokens.TEXT}; "
        f"background-color: {ui_tokens.BG_OVERLAY}; "
        f"border: 1px solid {ui_tokens.BORDER}; }}"
    )


def main(splash: Any = None, single_instance_server: Any = None) -> int:
    # Startup diagnostics: env snapshot + faulthandler + symlink probe.
    # Idempotente. Cada sessao comeca gravando ambiente no log.
    try:
        from . import diagnostics
        diagnostics.startup_init()
    except Exception:
        pass
    # Canal uv/PyPI (v0.2): cria o atalho da area de trabalho uma unica vez
    # no primeiro run. Nunca levanta.
    try:
        from . import install_tools
        install_tools.ensure_first_run_setup()
    except Exception:
        pass
    if QT_IMPORT_ERROR is not None:
        print(
            "PySide6 não está instalado no ambiente Python atual. "
            "Instale PySide6 no ambiente do app para abrir o Transcritório.",
            file=sys.stderr,
        )
        print(f"Erro original: {QT_IMPORT_ERROR}", file=sys.stderr)
        return 2
    import argparse
    from .project_store import PROJECT_EXTENSION
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--project", type=Path, default=None, help="Project root directory or .transcritorio file.")
    parser.add_argument("project_file", nargs="?", type=Path, default=None, help=argparse.SUPPRESS)
    args, _remaining = parser.parse_known_args()
    # Support: Transcritorio.exe path/to/projeto.transcritorio (double-click)
    project_root = args.project
    if project_root is None and args.project_file is not None:
        pf = args.project_file
        if pf.suffix == PROJECT_EXTENSION:
            project_root = pf.parent
        else:
            project_root = pf
    # Top-level crash logger: catches every uncaught exception and writes
    # a full traceback to %LOCALAPPDATA%\Transcritorio\download_diagnostic.log
    # (or platform equivalent). Without this, Qt can eat Python exceptions
    # silently — the app just disappears, no dialog, no log.
    import traceback as _traceback
    from . import model_manager as _mm_for_excepthook
    def _crash_excepthook(exc_type, exc_value, exc_tb):
        try:
            tb_str = "".join(_traceback.format_exception(exc_type, exc_value, exc_tb))
            _mm_for_excepthook._download_diag_log(
                f"[uncaught] {exc_type.__name__}: {exc_value}\n{tb_str}"
            )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _crash_excepthook

    app = QApplication.instance() or QApplication(sys.argv)
    _apply_dark_theme(app)
    window = ReviewStudioWindow(project_root=project_root)
    window.show()
    if splash is not None:
        try:
            splash.finish(window)
        except Exception:
            pass
    if single_instance_server is not None:
        # Segundo clique no icone: em vez de outra janela, trazer esta para
        # frente (gui_launcher ja barrou o processo novo). Desde a R4 o
        # ping carrega a identidade da instalacao ("activate <ver>+<build>")
        # e o servidor responde a propria: se o processo novo e de um wheel
        # DIFERENTE desta janela, avisar aqui — o usuario clicou no
        # icone esperando a versao recem-instalada e recebeu a antiga.
        # Congelar a identidade DESTE processo agora: apos um upgrade em
        # disco, recomputa-la no ping devolveria a versao nova e anularia
        # o aviso (auditoria 2026-09-01).
        from .gui_launcher import _instance_identity as _frozen_identity
        _frozen_identity()
        def _on_second_instance_ping() -> None:
            payload = b""
            try:
                connection = single_instance_server.nextPendingConnection()
                if connection is not None:
                    try:
                        if connection.waitForReadyRead(200):
                            payload = bytes(connection.readAll())
                        from .gui_launcher import _instance_identity
                        connection.write(_instance_identity().encode("utf-8"))
                        connection.flush()
                        connection.waitForBytesWritten(200)
                    except Exception:  # noqa: BLE001 - protocolo nunca
                        pass           # impede o raise da janela
                    connection.close()
            except Exception:
                pass
            window.showNormal()
            window.raise_()
            window.activateWindow()
            try:
                texto = payload.decode("utf-8", "replace").strip()
                if texto.startswith("activate "):
                    from .gui_launcher import _instance_identity
                    outra = texto.split(" ", 1)[1].strip()
                    if (outra and outra != _instance_identity()
                            and not getattr(window, "_stale_build_warned", False)):
                        window._stale_build_warned = True  # 1x por sessao
                        # Texto sem direcao: a divergencia tambem ocorre num
                        # downgrade — afirmar "mais nova" seria chute
                        # (auditoria 2026-09-01).
                        QTimer.singleShot(0, lambda: QMessageBox.information(
                            window,
                            "Outra versão instalada",
                            "Uma versão diferente do Transcritório foi "
                            "instalada neste computador.\n\nEsta janela ainda "
                            "roda a versão anterior à troca — feche-a e abra "
                            "o aplicativo de novo para usar a instalada."))
            except Exception:  # noqa: BLE001 - payload legado/parcial
                pass
        single_instance_server.newConnection.connect(_on_second_instance_ping)
        window._single_instance_server = single_instance_server
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        QTimer.singleShot(0, window.show_startup_dialog)

    # Aviso de versao nova (canal uv/PyPI, v0.2): checagem leve do JSON do
    # PyPI em thread propria. Silencio absoluto em erro/offline — jamais
    # bloqueia ou atrapalha o startup. Sem auto-update: so um aviso discreto.
    update_thread = None
    try:
        from . import install_tools as _install_check
        if not _install_check.is_frozen() and os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
            class _UpdateCheck(QThread):
                found = Signal(str)

                def run(self) -> None:
                    try:
                        import json as _json
                        from urllib.request import urlopen
                        with urlopen("https://pypi.org/pypi/transcritorio/json", timeout=6) as resp:
                            data = _json.load(resp)
                        latest = str((data.get("info") or {}).get("version") or "")

                        def _t(v: str) -> tuple[int, ...]:
                            return tuple(int(p) for p in v.split(".") if p.isdigit())

                        from . import __version__ as _cur
                        if latest and _t(latest) > _t(_cur):
                            self.found.emit(latest)
                    except Exception:
                        pass

            update_thread = _UpdateCheck()
            update_thread.found.connect(
                lambda v: window.progress_label.setText(
                    f"Nova versão {v} disponível — menu Ajuda → Verificar atualizações."
                )
            )
            update_thread.start()
    except Exception:
        update_thread = None

    rc = app.exec()
    if update_thread is not None and update_thread.isRunning():
        update_thread.wait(8000)  # nunca destruir QThread vivo
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
