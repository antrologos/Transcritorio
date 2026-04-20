from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from array import array
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import os
import subprocess
import sys
import time
import wave

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

from . import app_service, project_store, review_store
from .runtime import resolve_executable
from .utils import sanitize_message

try:
    from PySide6.QtCore import QPointF, QThread, QTimer, Qt, QUrl, Signal
    from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QUndoCommand, QUndoStack
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
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
        QSlider,
        QSpinBox,
        QSplitter,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
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
APP_NAME = "Transcrit\u00f3rio"
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


from .project_store import _reorder_move, _merge_interview_order  # re-export for tests


# Helpers de cor para tema escuro (Fusion dark bg #2d2d2d).
# Cores escolhidas com contrast ratio WCAG AA (>=4.5:1) contra #2d2d2d.
def _style_ok() -> str:
    return "color: #81c784; font-weight: 700;"


def _style_warn() -> str:
    return "color: #ffb74d;"


def _style_err() -> str:
    return "color: #ff6b6b; font-weight: 700;"


def _style_muted() -> str:
    return "color: #9e9e9e;"


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
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / filename


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


def turn_preview(turn: dict[str, Any], max_chars: int = 120) -> str:
    text = " ".join(str(turn.get("text", "")).split())
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."


def display_flags(turn: dict[str, Any]) -> str:
    flags = turn.get("flags", [])
    if not isinstance(flags, list):
        return ""
    return ", ".join(FLAG_LABELS.get(str(flag), str(flag)) for flag in flags)


def saved_status_message() -> str:
    return "Todas as alteracoes foram salvas"


def saved_status_tooltip() -> str:
    return f"Ultimo salvamento: {datetime.now().strftime('%H:%M:%S')}"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "estimando..."
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


def eta_from_progress(started_monotonic: float, percent: int) -> str:
    if percent >= 100:
        return "concluindo..."
    if percent < 8:
        return "estimando..."
    elapsed = time.monotonic() - started_monotonic
    if elapsed < 15:
        return "estimando..."
    remaining = elapsed * ((100 - percent) / max(1, percent))
    return format_eta(remaining)


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

    class WaveformWidget(QWidget):
        seek_requested = Signal(float)

        def __init__(self) -> None:
            super().__init__()
            self.peaks: list[float] = []
            self.duration = 0.0
            self.position = 0.0
            self.edit_cursor: float | None = None
            self.selected_range: tuple[float, float] | None = None
            self.active_range: tuple[float, float] | None = None
            self.zoom = 1.0
            self.visible_start = 0.0
            self._drag_start_x: float | None = None
            self._drag_start_visible_start = 0.0
            self._drag_moved = False
            self.setMinimumHeight(96)
            self.setAccessibleName("Onda sonora")
            self.setAccessibleDescription(
                "Linha do tempo do audio. Clique para mover o audio, arraste para navegar e use a roda do mouse para aproximar."
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
            self.selected_range = None
            self.active_range = None
            self.zoom = 1.0
            self.visible_start = 0.0
            self._drag_start_x = None
            self._drag_start_visible_start = 0.0
            self._drag_moved = False
            self.update()

        def set_position(self, seconds: float) -> None:
            self.position = seconds
            if self.zoom > 1.0 and self.duration > 0:
                visible_end = self.visible_start + self.visible_duration()
                if seconds < self.visible_start or seconds > visible_end:
                    self.center_on(seconds)
            self.update()

        def set_edit_cursor(self, seconds: float | None) -> None:
            if seconds is None or self.duration <= 0:
                self.edit_cursor = None
            else:
                self.edit_cursor = max(0.0, min(self.duration, float(seconds)))
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
            painter.fillRect(rect, QColor("#0f1720"))
            if not self.peaks:
                painter.setPen(QColor("#9aa4ad"))
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

            painter.setPen(QPen(QColor("#56616d"), 1))
            painter.drawLine(0, ruler_height - 1, width, ruler_height - 1)
            tick_count = 6 if width >= 420 else 4
            for index in range(tick_count + 1):
                fraction = index / max(1, tick_count)
                x = int(fraction * width)
                seconds = visible_start + (fraction * visible_duration)
                painter.drawLine(x, ruler_height - 7, x, ruler_height - 1)
                painter.setPen(QColor("#c7d0d9"))
                painter.drawText(x + 3, 14, format_clock(seconds))
                painter.setPen(QPen(QColor("#56616d"), 1))

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
            painter.setPen(QPen(QColor("#5cb7ee"), 1))
            painter.setBrush(QBrush(QColor("#2f9bd3")))
            painter.drawPath(waveform_path)
            if self.duration > 0:
                if self.edit_cursor is not None and visible_start <= self.edit_cursor <= visible_end:
                    cursor_x = seconds_to_x(self.edit_cursor)
                    painter.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(cursor_x, ruler_height, cursor_x, height)
                play_x = seconds_to_x(self.position)
                painter.setPen(QPen(QColor("#ffcc33"), 2))
                painter.drawLine(play_x, ruler_height, play_x, height)
                painter.setPen(QColor("#d8dee9"))
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
                    self.set_edit_cursor(seconds)
                    self.seek_requested.emit(seconds)
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
        _CUDA: dict[str, list[int]] = {
            "tiny":           [5, 38, 56, 1, 0],
            "base":           [5, 38, 56, 1, 0],
            "small":          [4, 45, 50, 1, 0],
            "medium":         [4, 50, 45, 1, 0],
            "large-v3-turbo": [4, 48, 47, 1, 0],
            "large-v3":       [3, 63, 33, 1, 0],
        }
        _CPU: dict[str, list[int]] = {
            "tiny":           [2, 50, 47, 1, 0],
            "base":           [2, 50, 47, 1, 0],
            "small":          [1, 55, 43, 1, 0],
            "medium":         [1, 55, 43, 1, 0],
            "large-v3-turbo": [2, 59, 39, 0, 0],
            "large-v3":       [1, 65, 33, 1, 0],
        }
        table = _CUDA if device == "cuda" else _CPU
        return table.get(model, _CUDA.get("large-v3-turbo", [4, 48, 47, 1, 0]))

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

        def request_cancel_after_step(self) -> None:
            self.cancel_after_step = True

        def is_cancel_requested(self) -> bool:
            return self.cancel_after_step

        def run(self) -> None:
            try:
                total_weight = max(1, sum(max(1, weight) for weight in self.weights))
                completed_weight = 0
                for index, step in enumerate(self.steps, start=1):
                    if self.cancel_after_step:
                        self.finished_ok.emit(f"{self.label} cancelado.")
                        return
                    message, func, accepts_progress = self.unpack_step(step)
                    weight = max(1, self.weights[index - 1] if index - 1 < len(self.weights) else 1)
                    start_percent = int((completed_weight / total_weight) * 100)
                    end_percent = int(((completed_weight + weight) / total_weight) * 100)
                    self.progress.emit(f"Etapa {index} de {len(self.steps)}: {message}", start_percent)
                    if accepts_progress:
                        result = func(
                            self.step_progress_callback(index, len(self.steps), message, start_percent, end_percent),
                            self.is_cancel_requested,
                        )
                    else:
                        result = func()
                    failures = getattr(result, "failures", 0)
                    if failures:
                        if self.cancel_after_step:
                            self.finished_ok.emit(f"{self.label} cancelado.")
                            return
                        raise RuntimeError(f"{message}: {failures} falha(s).")
                    completed_weight += weight
                    self.progress.emit(f"Etapa {index} de {len(self.steps)} concluida: {message}", end_percent)
                    if self.cancel_after_step and index < len(self.steps):
                        self.finished_ok.emit(f"{self.label} interrompido apos a etapa atual.")
                        return
                self.progress.emit(f"{self.label} concluido.", 100)
                self.finished_ok.emit(f"{self.label} concluido.")
            except Exception as exc:  # GUI boundary
                self.failed.emit(str(exc))

        def unpack_step(self, step: tuple) -> tuple[str, Callable, bool]:
            if len(step) >= 3:
                return str(step[0]), step[1], bool(step[2])
            return str(step[0]), step[1], False

        def step_progress_callback(self, index: int, total: int, message: str, start_percent: int, end_percent: int) -> Callable[[dict[str, Any]], None]:
            def callback(detail: dict[str, Any]) -> None:
                progress_value = detail.get("progress")
                try:
                    inner_percent = max(0, min(100, int(progress_value)))
                except (TypeError, ValueError):
                    inner_percent = 0
                percent = start_percent + int(((end_percent - start_percent) * inner_percent) / 100)
                event = detail.get("event", "")
                detail_message = detail.get("message")
                if detail_message and event in ("model_download_bytes", "model_download_start", "model_download_done", "model_download_error"):
                    label = str(detail_message)
                elif detail_message and event == "diarize_progress":
                    label = str(detail_message)
                else:
                    label = message
                self.progress.emit(f"Etapa {index} de {total}: {label}", percent)

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
                # Rename staging -> files (atomico, mesmo dir)
                files_dir = trash_dir / "files"
                staging.rename(files_dir)
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
                title = f"Exportar: {open_title}" if open_title else "Exportar transcricao aberta"
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
                ("docx", "DOCX", True, "Documento para leitura e revisao fora do app."),
                ("md", "Markdown", True, "Texto simples com marcacao leve."),
                ("srt", "SRT", False, "Legenda com tempos por bloco."),
                ("vtt", "VTT", False, "Legenda web com tempos por bloco."),
                ("csv", "CSV", False, "Planilha com turnos e metadados."),
                ("tsv", "TSV", False, "Planilha tabulada com turnos e metadados."),
                ("nvivo", "NVivo TSV", False, "Tabela tabulada para importacao no NVivo."),
            ]:
                checkbox = QCheckBox(label)
                checkbox.setChecked(checked)
                checkbox.setToolTip(help_text)
                self.checkboxes[fmt] = checkbox
                layout.addWidget(checkbox)
            hint = QLabel("DOCX e Markdown sao os padroes de leitura. Legendas e planilhas ficam desmarcadas para evitar arquivos que voce nao pediu.")
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
                    "Exportar todas as transcricoes",
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
            self.setWindowTitle("Exportacao concluida")
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
                subprocess.Popen(["explorer", "/select,", str(target)])

        def _copy_path(self) -> None:
            target = self._selected_path() or self.results_folder
            QApplication.clipboard().setText(str(target))


    class MetadataDialog(QDialog):
        def __init__(self, selected_count: int, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Editar propriedades")
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel(f"Editar propriedades de {selected_count} arquivo(s) selecionado(s)."))

            grid = QGridLayout()
            self.apply_language = QCheckBox("Aplicar língua")
            self.language_combo = QComboBox()
            for code, label in [
                ("pt", "Português"),
                ("auto", "Automático"),
                ("en", "Inglês"),
                ("es", "Espanhol"),
                ("fr", "Francês"),
                ("de", "Alemão"),
                ("it", "Italiano"),
            ]:
                self.language_combo.addItem(label, code)
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
            self.min_speakers_spin.setValue(1)
            self.max_speakers_spin = QSpinBox()
            self.max_speakers_spin.setRange(1, 20)
            self.max_speakers_spin.setValue(4)
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
            hint = QLabel("Campos não marcados não serão alterados. O contexto é opcional e pode ficar vazio.")
            hint.setStyleSheet(_style_muted())
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

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


    class EngineSettingsDialog(QDialog):
        def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Configuracoes de transcricao local")
            layout = QVBoxLayout(self)
            description = QLabel("Motor local de transcricao. Use GPU NVIDIA quando disponivel; CPU funciona, mas tende a ser bem mais lenta.")
            description.setWordWrap(True)
            layout.addWidget(description)

            grid = QGridLayout()
            from . import model_manager
            self.model_combo = QComboBox()
            installed = model_manager.installed_asr_variants()
            current = str(config.get("asr_model") or "large-v3-turbo")
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
            self.install_models_btn = QPushButton("Instalar modelos...")
            self.install_models_btn.setToolTip("Baixar modelos adicionais do Hugging Face.")
            self.install_models_btn.clicked.connect(self._open_model_setup)
            model_row.addWidget(self.install_models_btn)
            grid.addWidget(QLabel("Modelo Whisper:"), 0, 0)
            grid.addLayout(model_row, 0, 1)

            self.device_combo = QComboBox()
            for value, label in [("cuda", "GPU NVIDIA (CUDA)"), ("cpu", "CPU")]:
                self.device_combo.addItem(label, value)
            self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(str(config.get("asr_device") or "cuda"))))
            grid.addWidget(QLabel("Dispositivo:"), 1, 0)
            grid.addWidget(self.device_combo, 1, 1)

            self.compute_combo = QComboBox()
            for value, label in [("float16", "float16 (GPU)"), ("int8", "int8 (menor memoria)"), ("float32", "float32 (CPU/GPU, mais pesado)")]:
                self.compute_combo.addItem(label, value)
            self.compute_combo.setCurrentIndex(max(0, self.compute_combo.findData(str(config.get("asr_compute_type") or "float16"))))
            grid.addWidget(QLabel("Precisao:"), 2, 0)
            grid.addWidget(self.compute_combo, 2, 1)

            self.language_combo = QComboBox()
            for value, label in [
                ("pt", "Portugues"),
                ("auto", "Automatico"),
                ("en", "Ingles"),
                ("es", "Espanhol"),
                ("fr", "Frances"),
                ("de", "Alemao"),
                ("it", "Italiano"),
            ]:
                self.language_combo.addItem(label, value)
            language = str(config.get("asr_language") or "auto")
            self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(language)))
            grid.addWidget(QLabel("Idioma padrao:"), 3, 0)
            grid.addWidget(self.language_combo, 3, 1)

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
            self.batch_spin.setRange(1, 32)
            self.batch_spin.setValue(int(config.get("asr_batch_size") or 4))

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
            advanced_layout.addWidget(QLabel("Segmento minimo:"), 2, 0)
            advanced_layout.addWidget(self.min_segment_spin, 2, 1)

            layout.addWidget(advanced_group)

            hint = QLabel("Batch controla quantos trechos o Whisper processa por vez. Aumentar pode acelerar em GPU com memoria sobrando; reduzir evita falta de memoria. Para computador sem GPU NVIDIA, use CPU com int8 ou float32.")
            hint.setStyleSheet(_style_muted())
            hint.setWordWrap(True)
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _open_model_setup(self) -> None:
            self.reject()
            parent = self.parent()
            if parent and hasattr(parent, "show_model_setup"):
                parent.show_model_setup()

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
                "asr_model": str(self.model_combo.currentData() or self.model_combo.currentText() or "large-v3-turbo"),
                "asr_device": device,
                "asr_compute_type": compute_type,
                "asr_batch_size": int(self.batch_spin.value()),
                "asr_language": None if language == "auto" else language,
                "diarization_num_speakers": min_spk if min_spk == max_spk else None,
                "min_speakers": min_spk,
                "max_speakers": max_spk,
                "diarization_min_duration_off": min_pause if min_pause > 0 else None,
                "diarization_min_segment": min_segment if min_segment > 0 else None,
            }


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
            self.populate(context)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def populate(self, context: app_service.ProjectContext) -> None:
            self.table.setRowCount(0)
            for file_id in sorted(context.jobs):
                job = context.jobs[file_id]
                row = self.table.rowCount()
                self.table.insertRow(row)
                values = [
                    file_id,
                    job.get("status", ""),
                    job.get("stage", ""),
                    f"{job.get('progress', 0)}%",
                    job.get("started_at", ""),
                    job.get("estimated_finish_at", ""),
                    job.get("finished_at", ""),
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
        PAGE_MODEL_SELECT = 3
        PAGE_TOKEN = 4
        PAGE_DOWNLOAD = 5
        PAGE_DONE = 6

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            from . import model_manager
            self.download_completed = False
            self.selected_asr_variants: list[str] = [model_manager.DEFAULT_ASR_VARIANT]
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
            self.setPage(self.PAGE_ACCOUNT, self._make_account_page())
            self.setPage(self.PAGE_TERMS, self._make_terms_page())
            self.setPage(self.PAGE_MODEL_SELECT, _ModelSelectWizardPage(self))
            self.setPage(self.PAGE_TOKEN, self._make_token_page())
            self.setPage(self.PAGE_DOWNLOAD, self._make_download_page())
            self.setPage(self.PAGE_DONE, self._make_done_page())

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
                "inteligência artificial (arquivos grandes, cerca de 7 GB). "
                "Isso é feito uma única vez.\n\n"
                "Vamos guiá-lo passo a passo. O processo leva uns 10 minutos "
                "e você só precisa fazer isso na primeira vez."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)
            faq = QGroupBox("O que são \"componentes de IA\"?")
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
            page.setTitle("Passo 1 de 5: Criar uma conta gratuita")
            layout = QVBoxLayout(page)
            account_intro = QLabel(
                "Os componentes de transcrição ficam em um site chamado Hugging Face — "
                "uma biblioteca pública de inteligência artificial. É gratuito e seguro, "
                "como se fosse um \"Google Acadêmico\" de modelos de IA.\n\n"
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
            page.setTitle("Passo 2 de 5: Autorizar o modelo de identificação de falantes")
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
                "  1. Crie ou abra um projeto (pasta com gravações)\n"
                "  2. O programa vai listar as entrevistas encontradas\n"
                "  3. Selecione quais deseja transcrever"
            )
            done_label.setWordWrap(True)
            layout.addWidget(done_label)
            layout.addStretch()
            return page

    class _ModelSelectWizardPage(QWizardPage):
        """Page 3: choose which ASR model(s) to download."""

        RECOMMENDED = ["large-v3-turbo", "large-v3"]
        OTHERS = ["medium", "small", "base", "tiny"]
        FIXED_GB = 6.9 + 0.07  # alignment + diarization (always downloaded)

        def __init__(self, wizard: "FirstRunWizard") -> None:
            super().__init__()
            from . import model_manager
            self._model_manager = model_manager
            self._wizard = wizard
            self.setTitle("Passo 3 de 5: Escolha o modelo de transcrição")
            layout = QVBoxLayout(self)
            intro = QLabel(
                "Selecione quais modelos de transcrição deseja baixar. "
                "Modelos maiores produzem transcrições melhores, mas "
                "demoram mais e ocupam mais espaço.\n\n"
                "Você pode instalar modelos adicionais depois nas configurações."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            self._checkboxes: dict[str, QCheckBox] = {}

            # Recommended models (visible by default)
            for key in self.RECOMMENDED:
                info = self._model_manager.ASR_VARIANTS[key]
                suffix = "  ★ Recomendado" if key == self._model_manager.DEFAULT_ASR_VARIANT else ""
                cb = QCheckBox(f"{info['label']}  ({self._fmt(info['estimated_gb'])}){suffix}")
                cb.setChecked(key == self._model_manager.DEFAULT_ASR_VARIANT)
                cb.setToolTip(info["desc"])
                cb.stateChanged.connect(self._on_changed)
                layout.addWidget(cb)
                self._checkboxes[key] = cb

            # Other models (in collapsible group)
            others_group = QGroupBox("Outros modelos (menores, menor qualidade)")
            others_group.setCheckable(False)
            others_layout = QVBoxLayout(others_group)
            for key in self.OTHERS:
                info = self._model_manager.ASR_VARIANTS[key]
                cb = QCheckBox(f"{info['label']}  ({self._fmt(info['estimated_gb'])})")
                cb.setChecked(False)
                cb.setToolTip(info["desc"])
                cb.stateChanged.connect(self._on_changed)
                others_layout.addWidget(cb)
                self._checkboxes[key] = cb
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

        def _update_total(self) -> None:
            asr_gb = sum(
                self._model_manager.ASR_VARIANTS[k]["estimated_gb"]
                for k, cb in self._checkboxes.items()
                if cb.isChecked()
            )
            total = asr_gb + self.FIXED_GB
            self.total_label.setText(
                f"Download total: ~{self._fmt(total)} "
                f"(inclui componentes obrigatórios de alinhamento e identificação de falantes)"
            )

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
            self.setTitle("Passo 4 de 5: Criar e colar a chave de acesso")
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
            self.token_edit.setPlaceholderText("Cole aqui a chave (começa com hf_...)")
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
            token = self.token_edit.text().strip()
            if not token:
                self.status_label.setText("Cole a chave de acesso no campo acima.")
                self.status_label.setStyleSheet(_style_err())
                return False
            self.status_label.setText("Verificando sua chave...")
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

    class _DownloadWizardPage(QWizardPage):
        """Page 4: model download with progress."""

        def __init__(self, wizard: "FirstRunWizard") -> None:
            super().__init__()
            self._wizard = wizard
            self._download_started = False
            self._download_done = False
            self.setTitle("Passo 5 de 5: Baixar os componentes")
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
            self.progress_bar.setRange(0, 100)
            layout.addWidget(self.progress_bar)
            layout.addStretch()

        def initializePage(self) -> None:
            if self._download_started:
                return
            # Check disk space before starting download
            from . import model_manager
            disk = model_manager.check_disk_space()
            if not disk["ok"]:
                self.progress_label.setText(disk["message"])
                self.progress_label.setStyleSheet(_style_err())
                return
            self._download_started = True
            token_page = self._wizard.page(FirstRunWizard.PAGE_TOKEN)
            token = token_page.token() if hasattr(token_page, "token") else ""
            asr_variants = getattr(self._wizard, "selected_asr_variants", None)
            self.progress_label.setText("Iniciando download...")
            self.progress_bar.setValue(0)
            self._worker = _SetupDownloadThread(token, asr_variants=asr_variants)
            self._worker.progress.connect(self._on_progress)
            self._worker.finished_ok.connect(self._on_done)
            self._worker.failed.connect(self._on_failed)
            self._worker.start()

        def isComplete(self) -> bool:
            return self._download_done

        def _on_progress(self, message: str, percent: int) -> None:
            self.progress_label.setText(message)
            self.progress_bar.setValue(max(0, min(100, percent)))

        def _on_done(self) -> None:
            self._download_done = True
            self._wizard.download_completed = True
            self.progress_bar.setValue(100)
            self.progress_label.setText("Componentes baixados e verificados com sucesso!")
            self.progress_label.setStyleSheet(_style_ok())
            self.completeChanged.emit()

        def _on_failed(self, message: str) -> None:
            self.progress_label.setText(f"Erro: {message}\n\nVerifique sua conexão e tente novamente.")
            self.progress_label.setStyleSheet(_style_err())
            self._download_started = False  # allow retry via Back + Next

    class _SetupDownloadThread(QThread):
        progress = Signal(str, int)
        finished_ok = Signal()
        failed = Signal(str)

        def __init__(self, token: str, asr_variants: list[str] | None = None) -> None:
            super().__init__()
            self.token = token
            self.asr_variants = asr_variants

        def run(self) -> None:
            try:
                def on_progress(detail: dict) -> None:
                    msg = detail.get("message", "")
                    pct = int(detail.get("progress", 0))
                    self.progress.emit(msg, pct)
                result = app_service.download_models(
                    token=self.token,
                    progress_callback=on_progress,
                    asr_variants=self.asr_variants,
                )
                if getattr(result, "failures", 0):
                    self.failed.emit(str(getattr(result, "message", "Falha ao baixar um ou mais componentes.")))
                else:
                    self.finished_ok.emit()
            except Exception as exc:
                from .utils import sanitize_message
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
            status_label = QLabel("✓ Componentes de IA instalados")
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


    class ModelSetupDialog(QDialog):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Preparar modelos locais")
            self.resize(720, 640)
            layout = QVBoxLayout(self)

            title = QLabel("Preparar modelos locais")
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            layout.addWidget(title)

            intro = QTextEdit()
            intro.setReadOnly(True)
            intro.setPlainText(
                "O token Hugging Face e usado apenas para baixar modelos. "
                "Audios, videos e transcricoes continuam neste computador.\n\n"
                "Passo a passo:\n"
                "1. Crie ou entre na sua conta do Hugging Face.\n"
                "2. Abra o modelo pyannote/speaker-diarization-community-1 e aceite os termos.\n"
                "3. Crie um token de leitura no Hugging Face.\n"
                "4. Cole o token abaixo e baixe os modelos.\n"
                "5. Depois do download, o Transcritorio verifica o carregamento local/offline.\n\n"
                "Para preparar outro computador, repita estes mesmos passos com o token do usuario daquele computador. "
                "Nunca use nem compartilhe o token de outra pessoa."
            )
            intro.setMinimumHeight(180)
            layout.addWidget(intro)

            links = QHBoxLayout()
            for label, url in [
                ("Criar conta", "https://huggingface.co/join"),
                ("Aceitar pyannote", "https://huggingface.co/pyannote/speaker-diarization-community-1"),
                ("Criar token", "https://huggingface.co/settings/tokens"),
            ]:
                button = QPushButton(label)
                button.clicked.connect(lambda _checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
                links.addWidget(button)
            links.addStretch()
            layout.addLayout(links)

            layout.addWidget(QLabel("Token Hugging Face deste usuario:"))
            self.token_edit = QLineEdit()
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.token_edit.setPlaceholderText("hf_...")
            # Pre-fill from secure vault if available
            from . import token_vault
            saved = token_vault.retrieve()
            if saved:
                self.token_edit.setText(saved)
            layout.addWidget(self.token_edit)

            self.remember_checkbox = QCheckBox("Lembrar neste computador usando cofre seguro")
            self.remember_checkbox.setChecked(bool(saved))
            self.remember_checkbox.setToolTip("Armazena o token criptografado com suas credenciais do Windows (DPAPI). So voce neste computador pode acessar.")
            layout.addWidget(self.remember_checkbox)

            status = QTextEdit()
            status.setReadOnly(True)
            status.setPlainText(app_service.models_status_text())
            status.setMinimumHeight(120)
            layout.addWidget(status)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Baixar modelos")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def token(self) -> str:
            return self.token_edit.text().strip()

        def accept(self) -> None:
            if not self.token():
                QMessageBox.warning(
                    self,
                    "Token necessario",
                    "Cole o token de leitura do Hugging Face deste usuario para baixar o modelo de separacao de falantes.",
                )
                return
            # Persist token if "remember" is checked
            from . import token_vault
            if self.remember_checkbox.isChecked():
                token_vault.store(self.token())
            else:
                token_vault.clear()
            super().accept()


    class ReviewStudioWindow(QMainWindow):
        def __init__(self, project_root: Path | None = None) -> None:
            super().__init__()
            self.setWindowTitle(APP_NAME)
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
            self.current_turn_id: str | None = None
            self.current_play_row: int | None = None
            self.media_candidates: list[Path] = []
            self.media_candidate_index = 0
            self.worker: PipelineWorker | None = None
            self.current_job_label = ""
            self._loading_editor = False
            self._editor_dirty = False
            self._save_failed = False
            self._slider_dragging = False
            self._changing_selection = False
            self._fallback_media_attempted = False
            self._close_after_worker = False
            self.undo_stack = QUndoStack(self)

            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.autosave_timer = QTimer(self)
            self.autosave_timer.setInterval(1200)
            self.autosave_timer.setSingleShot(True)
            self.autosave_timer.timeout.connect(self.save_current_turn)

            self._build_actions()
            self._build_ui()
            self.set_editor_enabled(False)
            self._connect_player()
            self.refresh_interviews()

        def _build_actions(self) -> None:
            self.add_folder_action = QAction("Adicionar pasta...", self)
            self.add_folder_action.setToolTip("Escolher uma pasta com audios ou videos.")
            self.add_folder_action.triggered.connect(self.add_audio_folder)

            self.new_project_action = QAction("Novo projeto...", self)
            self.new_project_action.setShortcut(QKeySequence("Ctrl+N"))
            self.new_project_action.setToolTip("Criar uma nova pasta de projeto de transcricoes. (Ctrl+N)")
            self.new_project_action.triggered.connect(self.new_project)

            self.open_project_action = QAction("Abrir projeto...", self)
            self.open_project_action.setShortcut(QKeySequence("Ctrl+O"))
            self.open_project_action.setToolTip("Abrir uma pasta de projeto de transcricoes existente. (Ctrl+O)")
            self.open_project_action.triggered.connect(self.open_project)

            self.add_files_action = QAction("Adicionar arquivos...", self)
            self.add_files_action.setToolTip("Escolher arquivos individuais de audio ou video.")
            self.add_files_action.triggered.connect(self.add_audio_files)

            self.save_project_action = QAction("Salvar projeto", self)
            self.save_project_action.setToolTip("Atualizar o arquivo de projeto e os metadados.")
            self.save_project_action.triggered.connect(self.save_project_metadata)

            self.open_project_folder_action = QAction("Abrir pasta do projeto", self)
            self.open_project_folder_action.setToolTip("Abrir a pasta do projeto no Explorador de Arquivos.\nDesativado sem projeto aberto.")
            self.open_project_folder_action.triggered.connect(self.open_project_folder)

            self.startup_action = QAction("Comecar", self)
            self.startup_action.setToolTip("Mostrar opcoes iniciais do projeto.")
            self.startup_action.triggered.connect(self.show_startup_dialog)

            self.exit_action = QAction("Sair", self)
            self.exit_action.setToolTip("Fechar o Transcritório.")
            self.exit_action.triggered.connect(self.close)

            self.apply_metadata_action = QAction("Editar propriedades...", self)
            self.apply_metadata_action.setToolTip("Aplicar língua, falantes, rótulos ou contexto aos arquivos selecionados.")
            self.apply_metadata_action.triggered.connect(self.apply_metadata_to_selected)

            self.queue_action = QAction("Ver fila de processamento", self)
            self.queue_action.setToolTip("Ver o estado das transcricoes em lote.")
            self.queue_action.triggered.connect(self.show_queue)

            self.engine_settings_action = QAction("Configurar transcricao...", self)
            self.engine_settings_action.setToolTip("Escolher GPU/CPU, modelo, precisao e batch.")
            self.engine_settings_action.triggered.connect(self.configure_engine)

            self.model_setup_action = QAction("Configurar modelos...", self)
            self.model_setup_action.setToolTip("Baixar e verificar modelos locais com o token Hugging Face do usuario.")
            self.model_setup_action.triggered.connect(self.show_model_setup)

            self.model_status_action = QAction("Status dos modelos", self)
            self.model_status_action.setToolTip("Mostrar quais modelos locais ja foram baixados.")
            self.model_status_action.triggered.connect(self.show_model_status)

            self.refresh_library_action = QAction("Atualizar biblioteca", self)
            self.refresh_library_action.setToolTip("Procurar gravacoes nas pastas cadastradas.")
            self.refresh_library_action.triggered.connect(self.run_manifest_job)

            self.reload_list_action = QAction("Recarregar lista", self)
            self.reload_list_action.setShortcut(QKeySequence("F5"))
            self.reload_list_action.setToolTip("Recarregar a lista de entrevistas a partir dos arquivos do projeto. (F5)")
            self.reload_list_action.triggered.connect(self.refresh_interviews)

            self.open_transcript_action = QAction("Abrir transcricao", self)
            self.open_transcript_action.setToolTip("Abrir a transcricao do arquivo selecionado (duplo-clique ou Enter na linha). Selecione um arquivo na lista.")
            self.open_transcript_action.triggered.connect(self.open_selected_review)

            self.transcribe_action = QAction("Transcrever selecionados", self)
            self.transcribe_action.setToolTip("Transcrever os arquivos selecionados na lista do projeto.")
            self.transcribe_action.triggered.connect(self.run_full_transcription_job)

            self.transcribe_pending_action = QAction("Transcrever todos nao transcritos", self)
            self.transcribe_pending_action.setToolTip("Transcrever todos os arquivos do projeto que ainda nao tem transcricao.")
            self.transcribe_pending_action.triggered.connect(self.run_pending_transcription_job)

            self.transcribe_current_action = QAction("Transcrever este arquivo", self)
            self.transcribe_current_action.setToolTip("Transcrever a midia aberta agora.")
            self.transcribe_current_action.triggered.connect(self.run_current_file_transcription_job)

            self.save_action = QAction("Salvar transcrição", self)
            self.save_action.setShortcut(QKeySequence.StandardKey.Save)
            self.save_action.setToolTip("Salvar a transcricao editavel desta entrevista.")
            self.save_action.triggered.connect(lambda _checked=False: self.save_current_turn(force=True))

            self.generate_files_action = QAction("Exportar...", self)
            self.generate_files_action.setShortcut(QKeySequence("Ctrl+E"))
            self.generate_files_action.setToolTip("Exportar a transcricao aberta, os arquivos selecionados ou todas as transcricoes. (Ctrl+E)")
            self.generate_files_action.triggered.connect(self.export_current_review)

            self.export_selected_action = QAction("Exportar selecionados...", self)
            self.export_selected_action.setToolTip("Exportar as transcricoes dos arquivos selecionados.")
            self.export_selected_action.triggered.connect(self.export_selected_reviews)

            self.delete_transcription_action = QAction("Limpar transcricao gerada...", self)
            self.delete_transcription_action.setToolTip("Apagar apenas os arquivos de transcricao gerados. O audio original e mantido no projeto.")
            self.delete_transcription_action.triggered.connect(self.delete_selected_transcriptions)

            self.rename_interview_action = QAction("Renomear rotulo...", self)
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

            self.trash_selected_action = QAction("Enviar para Lixeira...", self)
            self.trash_selected_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
            # ApplicationShortcut: Del dispara de qualquer lugar; effective_target_ids trata selecao.
            self.trash_selected_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_selected_action.setToolTip("Enviar os arquivos selecionados (audio original + transcricao) para a Lixeira do projeto. Reversivel com Ctrl+Z nesta sessao. (Del)")
            self.trash_selected_action.triggered.connect(self.trash_selected_interviews)

            self.trash_undo_action = QAction("Desfazer exclusao", self)
            self.trash_undo_action.setShortcut(QKeySequence("Ctrl+Z"))
            # ApplicationShortcut + guard em undo_last_trash delega ao editor quando foco e QTextEdit
            self.trash_undo_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_undo_action.setToolTip("Desfaz a ultima exclusao desta sessao (Ctrl+Z).")
            self.trash_undo_action.triggered.connect(self.undo_last_trash)

            self.trash_redo_action = QAction("Refazer exclusao", self)
            self.trash_redo_action.setShortcut(QKeySequence("Ctrl+Shift+Z"))
            self.trash_redo_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.trash_redo_action.setToolTip("Refaz a ultima exclusao desfeita (Ctrl+Shift+Z).")
            self.trash_redo_action.triggered.connect(self.redo_last_trash)

            for _trash_action in (self.trash_selected_action, self.trash_undo_action, self.trash_redo_action):
                _trash_action.setShortcutVisibleInContextMenu(True)

            self.export_current_action = QAction("Exportar este arquivo...", self)
            self.export_current_action.setToolTip("Exportar apenas a transcricao aberta.")
            self.export_current_action.triggered.connect(self.export_current_review)

            self.close_open_file_action = QAction("Fechar arquivo aberto", self)
            self.close_open_file_action.setToolTip("Fechar o arquivo aberto e voltar à lista de entrevistas.")
            self.close_open_file_action.triggered.connect(self.close_open_file)

            self.open_export_folder_action = QAction("Abrir pasta Resultados", self)
            self.open_export_folder_action.setToolTip("Abrir a pasta Resultados do projeto (DOCX, Markdown, legendas) no Explorador.")
            self.open_export_folder_action.triggered.connect(self.open_export_folder)

            self.diarize_action = QAction("Reprocessar falantes", self)
            self.diarize_action.setToolTip("Reprocessar a identificação de falantes para os arquivos selecionados.\nSelecione ao menos um arquivo.")
            self.diarize_action.triggered.connect(self.run_diarization_job)

            self.improve_speakers_action = QAction("Melhorar falantes deste arquivo", self)
            self.improve_speakers_action.setToolTip("Refazer a diarizacao local deste arquivo e remontar a transcricao editavel.")
            self.improve_speakers_action.triggered.connect(self.improve_speakers_current_file)

            self.render_action = QAction("Atualizar transcricao editavel", self)
            self.render_action.setToolTip("Remontar a transcrição editável a partir dos dados brutos (ASR + diarização).\nSelecione ao menos um arquivo.")
            self.render_action.triggered.connect(self.run_render_job)

            self.qc_action = QAction("Verificar exportacoes", self)
            self.qc_action.setToolTip("Verificar a qualidade das transcrições geradas (integridade e consistência).")
            self.qc_action.triggered.connect(self.run_qc_job)

            self.about_action = QAction("Sobre", self)
            self.about_action.setToolTip("Informações sobre o Transcritório: versão e créditos.")
            self.about_action.triggered.connect(self.show_about)

            self.credits_action = QAction("Creditos", self)
            self.credits_action.setToolTip("Ver os créditos do Transcritório.")
            self.credits_action.triggered.connect(self.show_about)

            self.documentation_action = QAction("Documentacao", self)
            self.documentation_action.setToolTip("Abrir a documentação do projeto, se disponível.")
            self.documentation_action.triggered.connect(self.show_documentation)

            self.workflow_help_action = QAction("Fluxo de trabalho", self)
            self.workflow_help_action.setToolTip("Ver o passo-a-passo basico do Transcritorio.")
            self.workflow_help_action.triggered.connect(self.show_workflow_help)

            self.cancel_job_action = QAction("Cancelar", self)
            self.cancel_job_action.setToolTip("Cancela o processamento atual. O WhisperX e interrompido; outras etapas param no proximo ponto seguro.")
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

            self.seek_back_action = QAction("Voltar 5s", self)
            self.seek_back_action.setShortcut(QKeySequence("Ctrl+Left"))
            self.seek_back_action.triggered.connect(lambda: self.seek_relative(-5))
            self.addAction(self.seek_back_action)

            self.seek_forward_action = QAction("Avancar 5s", self)
            self.seek_forward_action.setShortcut(QKeySequence("Ctrl+Right"))
            self.seek_forward_action.triggered.connect(lambda: self.seek_relative(5))
            self.addAction(self.seek_forward_action)

        def action_button(self, action: QAction, primary: bool = False) -> QPushButton:
            button = QPushButton(action.text())
            button.setToolTip(action.toolTip())
            button.clicked.connect(lambda _checked=False, item=action: item.trigger())
            if primary:
                button.setDefault(True)
                button.setStyleSheet("font-weight: 700;")
            return button

        def media_button(self) -> QPushButton:
            button = QPushButton("Adicionar mídia...")
            button.setToolTip("Adicionar arquivos individuais ou uma pasta de mídia ao projeto.")
            menu = QMenu(button)
            menu.addAction(self.add_files_action)
            menu.addAction(self.add_folder_action)
            button.setMenu(menu)
            return button

        def transcribe_menu_button(self) -> QPushButton:
            button = QPushButton(self.transcribe_action.text())
            button.setToolTip(self.transcribe_action.toolTip())
            button.setDefault(True)
            button.setStyleSheet("font-weight: 700;")
            menu = QMenu(button)
            menu.addAction(self.transcribe_action)
            menu.addAction(self.transcribe_pending_action)
            button.setMenu(menu)
            return button

        def _build_menus(self) -> None:
            # --- Arquivo: I/O (projeto e transcricao aberta) ---
            arquivo_menu = self.menuBar().addMenu("Arquivo")
            arquivo_menu.addAction(self.new_project_action)
            arquivo_menu.addAction(self.open_project_action)
            recent_menu = arquivo_menu.addMenu("Projetos recentes")
            from . import recent_projects
            for rp in recent_projects.load_recent()[:5]:
                recent_menu.addAction(str(rp), lambda p=rp: self._open_project_path(p))
            if self.context is not None:
                recent_menu.addSeparator()
                recent_menu.addAction(str(self.context.paths.project_root), self.refresh_interviews)
            arquivo_menu.addSeparator()
            add_media_menu = arquivo_menu.addMenu("Adicionar midia...")
            add_media_menu.addAction(self.add_files_action)
            add_media_menu.addAction(self.add_folder_action)
            arquivo_menu.addAction(self.reload_list_action)
            arquivo_menu.addSeparator()
            arquivo_menu.addAction(self.open_transcript_action)
            arquivo_menu.addAction(self.save_action)
            arquivo_menu.addAction(self.close_open_file_action)
            arquivo_menu.addSeparator()
            arquivo_menu.addAction(self.generate_files_action)
            arquivo_menu.addAction(self.export_current_action)
            arquivo_menu.addAction(self.export_selected_action)
            arquivo_menu.addSeparator()
            arquivo_menu.addAction(self.open_project_folder_action)
            arquivo_menu.addAction(self.open_export_folder_action)
            arquivo_menu.addSeparator()
            arquivo_menu.addAction(self.exit_action)

            # --- Editar: lista e edicao ---
            editar_menu = self.menuBar().addMenu("Editar")
            editar_menu.addAction(self.undo_action)
            editar_menu.addAction(self.redo_action)
            editar_menu.addSeparator()
            editar_menu.addAction(self.apply_metadata_action)
            editar_menu.addAction(self.rename_interview_action)
            editar_menu.addAction(self.move_up_action)
            editar_menu.addAction(self.move_down_action)
            editar_menu.addSeparator()
            editar_menu.addAction(self.delete_transcription_action)
            editar_menu.addAction(self.trash_selected_action)
            editar_menu.addAction(self.trash_undo_action)
            editar_menu.addAction(self.trash_redo_action)

            # --- Transcrever: pipeline e configuracao ---
            transcrever_menu = self.menuBar().addMenu("Transcrever")
            transcrever_menu.addAction(self.transcribe_action)
            transcrever_menu.addAction(self.transcribe_current_action)
            transcrever_menu.addAction(self.transcribe_pending_action)
            transcrever_menu.addSeparator()
            transcrever_menu.addAction(self.diarize_action)
            transcrever_menu.addAction(self.improve_speakers_action)
            transcrever_menu.addAction(self.render_action)
            transcrever_menu.addSeparator()
            transcrever_menu.addAction(self.qc_action)
            transcrever_menu.addAction(self.queue_action)
            transcrever_menu.addAction(self.refresh_library_action)
            transcrever_menu.addSeparator()
            transcrever_menu.addAction(self.engine_settings_action)
            transcrever_menu.addAction(self.model_setup_action)
            transcrever_menu.addAction(self.model_status_action)
            transcrever_menu.addSeparator()
            transcrever_menu.addAction(self.cancel_job_action)

            # --- Ajuda ---
            ajuda_menu = self.menuBar().addMenu("Ajuda")
            ajuda_menu.addAction(self.documentation_action)
            ajuda_menu.addAction(self.workflow_help_action)
            ajuda_menu.addSeparator()
            ajuda_menu.addAction(self.credits_action)
            ajuda_menu.addAction(self.about_action)

        def show_workflow_help(self) -> None:
            QMessageBox.information(
                self,
                "Fluxo de trabalho",
                "Use: Arquivos > Adicionar midia -> Transcrever selecionados -> Abrir arquivo -> Editar -> Salvar transcricao -> Exportar.",
            )

        def show_about(self) -> None:
            from . import __version__, __build__
            build_info = f"Build: {__build__}" if __build__ != "dev" else "Versão de desenvolvimento (fonte)"
            QMessageBox.information(
                self,
                f"Sobre {APP_NAME}",
                f"{APP_NAME} v{__version__}\n\n{build_info}\n\nCréditos: {APP_CREDITS}\n\nTranscrição local com WhisperX e pyannote.",
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
            if not self._require_project("Fila de tarefas"):
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
            self._sync_diarize_checkbox()
            self.progress_label.setText("Configuracao de transcricao atualizada.")

        def _on_diarize_toggled(self, checked: bool) -> None:
            if self.context is None:
                return
            try:
                self.context = app_service.update_engine_config(self.context, {"diarize": checked})
            except Exception as exc:
                _logger.warning("update_engine_config(diarize) falhou: %s", exc)

        def _sync_diarize_checkbox(self) -> None:
            if not hasattr(self, "diarize_checkbox"):
                return
            self.diarize_checkbox.blockSignals(True)
            self.diarize_checkbox.setChecked(bool(self.context.config.get("diarize", True)) if self.context else True)
            self.diarize_checkbox.blockSignals(False)

        def show_startup_dialog(self) -> None:
            # Tela A: Setup wizard when AI components are missing
            if not app_service.required_models_ready():
                wizard = FirstRunWizard(self)
                result = wizard.exec()
                if result == QDialog.DialogCode.Accepted and wizard.download_completed:
                    # Components installed — show project chooser
                    self.progress_label.setText("Componentes de IA instalados.")
                else:
                    # Skipped or cancelled — show warning
                    if not app_service.required_models_ready():
                        self.progress_label.setText(
                            "⚠ Componentes de IA não instalados. "
                            "Use Configurações > Configurar modelos."
                        )
                        self.progress_label.setStyleSheet(_style_err())
                        self.refresh_interviews()
                        return
                # Fall through to project chooser if models are now ready
                if not app_service.required_models_ready():
                    self.refresh_interviews()
                    return

            # Tela B: Project chooser when everything is ready
            dialog = ProjectChooserDialog(self.context, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                if self.context is None:
                    return
                self.refresh_interviews()
                return
            if dialog.choice == "new":
                self.new_project()
            elif dialog.choice == "open":
                self.open_project()
            elif dialog.choice == "recent" and dialog.selected_recent is not None:
                self._open_project_path(dialog.selected_recent)
            else:
                self.refresh_interviews()

        def show_model_status(self) -> None:
            QMessageBox.information(self, "Status dos modelos", app_service.models_status_text())

        def show_model_setup(self) -> None:
            if self.worker and self.worker.isRunning():
                QMessageBox.information(self, "Tarefa em andamento", "Aguarde a tarefa atual terminar antes de preparar modelos.")
                return
            dialog = ModelSetupDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            token = dialog.token()
            self.start_worker(
                "Preparar modelos",
                [
                    (
                        "Baixando e verificando modelos locais...",
                        lambda progress, should_cancel, hf_token=token: app_service.download_models(
                            token=hf_token,
                            progress_callback=progress,
                            should_cancel=should_cancel,
                        ),
                        True,
                    )
                ],
            )

        def ensure_models_ready(self) -> bool:
            if app_service.required_models_ready():
                return True
            answer = QMessageBox.question(
                self,
                "Modelos locais pendentes",
                "Os modelos de transcricao e separacao de falantes ainda nao foram verificados neste computador. Deseja preparar os modelos agora?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.show_model_setup()
            return False

        def _open_project_path(self, project_path: Path) -> None:
            try:
                context = app_service.open_project(project_path)
                self.context = context
                from . import recent_projects
                recent_projects.save_recent(context.paths.project_root)
                self.project_label.setText(self.project_header_text())
                self.refresh_interviews()
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Erro ao abrir projeto:\n{exc}")

        def _build_ui(self) -> None:
            self._build_menus()
            root = QWidget()
            root_layout = QVBoxLayout(root)
            header = QHBoxLayout()
            title = QLabel(APP_NAME)
            title.setStyleSheet("font-size: 18px; font-weight: 700;")
            header.addWidget(title)
            header.addStretch()
            self.project_label = QLabel(self.project_header_text())
            self.project_label.setStyleSheet(_style_muted())
            self.project_label.setTextFormat(Qt.TextFormat.RichText)
            self.project_label.linkActivated.connect(lambda _link: self.configure_engine())
            header.addWidget(self.project_label)
            root_layout.addLayout(header)

            action_bar = QHBoxLayout()
            action_bar.addWidget(self.media_button())
            action_bar.addWidget(self.transcribe_menu_button())
            self.diarize_checkbox = QCheckBox("Separar falantes")
            self.diarize_checkbox.setToolTip(
                "Identifica automaticamente quem esta falando (Entrevistador/Entrevistado).\n"
                "Desative para audios com um unico falante ou para transcrever mais rapido."
            )
            self.diarize_checkbox.setChecked(True)
            self.diarize_checkbox.toggled.connect(self._on_diarize_toggled)
            action_bar.addWidget(self.diarize_checkbox)
            action_bar.addWidget(self.action_button(self.save_action))
            action_bar.addWidget(self.action_button(self.generate_files_action))
            action_bar.addStretch()
            root_layout.addLayout(action_bar)

            progress_row = QHBoxLayout()
            self.progress_label = QLabel("Pronto.")
            self.save_status_label = QLabel("Sem transcrição aberta.")
            self.save_status_label.setStyleSheet(_style_muted())
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setVisible(False)
            self.cancel_job_button = self.action_button(self.cancel_job_action)
            self.cancel_job_button.setVisible(False)
            progress_row.addWidget(self.progress_label, stretch=2)
            progress_row.addWidget(self.save_status_label, stretch=1)
            progress_row.addWidget(self.progress_bar, stretch=3)
            progress_row.addWidget(self.cancel_job_button)
            root_layout.addLayout(progress_row)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(self._build_interview_panel())
            splitter.addWidget(self._build_review_panel())
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 4)
            root_layout.addWidget(splitter, stretch=1)
            self.setCentralWidget(root)

        def _build_interview_panel(self) -> QWidget:
            panel = QWidget()
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
            self.filter_text_edit.setPlaceholderText("ID da entrevista...")
            self.filter_text_edit.setClearButtonEnabled(True)
            self.filter_text_edit.setToolTip("Filtrar por ID (busca parcial).")
            self.filter_text_edit.textChanged.connect(self._apply_interview_filter)
            filter_row.addWidget(self.filter_text_edit)
            layout.addLayout(filter_row)
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
            self._empty_table_label = QLabel("Nenhuma entrevista.\nUse Arquivos \u203a Adicionar m\u00eddia.")
            self._empty_table_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_table_label.setStyleSheet(f"{_style_muted()} font-size: 13px; padding: 24px;")
            self._empty_table_label.setVisible(False)
            layout.addWidget(self._empty_table_label)
            metadata_button = self.action_button(self.apply_metadata_action)
            layout.addWidget(metadata_button)
            open_button = self.action_button(self.open_transcript_action)
            layout.addWidget(open_button)
            return panel

        def _has_project(self) -> bool:
            return self.context is not None

        def _require_project(self, action_label: str = "Esta acao") -> bool:
            """Show a message and return False if no project is loaded."""
            if self.context is not None:
                return True
            QMessageBox.information(
                self,
                "Nenhum projeto aberto",
                f"{action_label} requer um projeto aberto.\n\n"
                "Use Projeto > Novo projeto ou Projeto > Abrir projeto.",
            )
            return False

        def _browse_dir(self) -> str:
            if self.context is not None:
                return str(self.context.paths.project_root)
            return str(Path.home())

        def project_header_text(self) -> str:
            if self.context is None:
                return "Nenhum projeto aberto"
            name = str(self.context.project.get("project_name") or self.context.paths.project_root.name)
            from . import model_manager
            model = model_manager.resolve_asr_model(str(self.context.config.get("asr_model", "?")))
            return (f"Projeto: {name}  |  "
                    f'<a href="engine-settings" style="color:#888;text-decoration:underline;">Modelo: {model}</a>'
                    f"  |  {self.context.paths.project_root}")

        def _build_review_panel(self) -> QWidget:
            panel = QWidget()
            layout = QVBoxLayout(panel)
            self.review_title = QLabel("Abra uma entrevista para editar a transcrição.")
            self.review_title.setStyleSheet("font-size: 16px; font-weight: 700;")
            layout.addWidget(self.review_title)

            self.open_file_action_row = QHBoxLayout()
            self.transcribe_current_button = self.action_button(self.transcribe_current_action, primary=True)
            self.transcribe_current_button.setVisible(False)
            self.improve_speakers_button = self.action_button(self.improve_speakers_action)
            self.improve_speakers_button.setVisible(False)
            self.open_file_action_row.addWidget(self.transcribe_current_button)
            self.open_file_action_row.addWidget(self.improve_speakers_button)
            self.open_file_action_row.addStretch()
            layout.addLayout(self.open_file_action_row)

            self.review_splitter = QSplitter(Qt.Orientation.Vertical)
            self.review_splitter.setHandleWidth(8)
            layout.addWidget(self.review_splitter, stretch=1)

            media_panel = QWidget()
            media_layout = QVBoxLayout(media_panel)
            media_layout.setContentsMargins(0, 0, 0, 0)

            self.video_widget = QVideoWidget()
            self.video_widget.setMinimumHeight(170)
            self.video_widget.setStyleSheet("background: #111;")
            self.video_widget.setVisible(False)
            self.player.setVideoOutput(self.video_widget)
            media_layout.addWidget(self.video_widget)

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
            self.play_button.setToolTip("Reproduzir ou pausar o áudio da entrevista. (Espaço)")
            self.play_button.clicked.connect(self.toggle_playback)
            media_controls.addWidget(self.play_button)
            stop_button = QPushButton("Parar")
            stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
            stop_button.setToolTip("Parar a reprodução e voltar ao início.")
            stop_button.clicked.connect(self.stop_playback)
            media_controls.addWidget(stop_button)
            back_button = QPushButton("-5s")
            back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
            back_button.setToolTip("Voltar 5 segundos no áudio. (Ctrl+Esquerda)")
            back_button.clicked.connect(lambda: self.seek_relative(-5))
            media_controls.addWidget(back_button)
            forward_button = QPushButton("+5s")
            forward_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
            forward_button.setToolTip("Avançar 5 segundos no áudio. (Ctrl+Direita)")
            forward_button.clicked.connect(lambda: self.seek_relative(5))
            media_controls.addWidget(forward_button)
            repeat_button = QPushButton("Repetir bloco")
            repeat_button.setToolTip("Reproduzir novamente o trecho do bloco selecionado na tabela.")
            repeat_button.clicked.connect(self.repeat_current_turn)
            media_controls.addWidget(repeat_button)
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
            self.speed_combo.setToolTip("Velocidade de reprodução do áudio (0.75x a 2.0x).")
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
            turn_header = QHBoxLayout()
            turn_header.addWidget(QLabel("Blocos da transcricao"))
            turn_header.addStretch()
            self.wrap_turns_checkbox = QCheckBox("Quebrar linhas")
            self.wrap_turns_checkbox.setChecked(True)
            self.wrap_turns_checkbox.setToolTip("Liga ou desliga a quebra de linhas na tabela de blocos sem alterar a navegacao.")
            self.wrap_turns_checkbox.stateChanged.connect(self.toggle_turn_word_wrap)
            turn_header.addWidget(self.wrap_turns_checkbox)
            turn_layout.addLayout(turn_header)
            turn_layout.addWidget(self.turn_table)
            self.review_splitter.addWidget(media_panel)
            self.review_splitter.addWidget(turn_panel)
            self.review_splitter.addWidget(self._build_editor_panel())
            self.review_splitter.setCollapsible(0, False)
            self.review_splitter.setCollapsible(1, False)
            self.review_splitter.setCollapsible(2, False)
            self.review_splitter.setStretchFactor(0, 1)
            self.review_splitter.setStretchFactor(1, 3)
            self.review_splitter.setStretchFactor(2, 2)
            self.review_splitter.setSizes([240, 420, 260])
            return panel

        def _build_editor_panel(self) -> QWidget:
            group = QGroupBox("Editar bloco selecionado")
            grid = QGridLayout(group)
            grid.addWidget(QLabel("Falante:"), 0, 0)
            self.speaker_combo = QComboBox()
            self.speaker_combo.addItems(list(SPEAKER_LABELS))
            self.speaker_combo.currentIndexChanged.connect(self.editor_changed)
            grid.addWidget(self.speaker_combo, 0, 1)

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

            self.text_edit = QTextEdit()
            self.text_edit.setMinimumHeight(120)
            self.text_edit.setAccessibleName("Texto do bloco selecionado")
            self.text_edit.textChanged.connect(self.editor_changed)
            # Ancorar undo/redo do editor no text_edit (WidgetWithChildrenShortcut).
            # Com foco no editor, Ctrl+Z aciona QUndoStack; fora dele, trash_undo_action (ApplicationShortcut).
            self.text_edit.addAction(self.undo_action)
            self.text_edit.addAction(self.redo_action)
            grid.addWidget(self.text_edit, 2, 0, 1, 4)

            button_row = QHBoxLayout()
            self.save_block_button = QPushButton("Salvar bloco")
            self.save_block_button.setToolTip("Salva as alteracoes do bloco atual. Trocar de bloco tambem salva automaticamente.")
            self.save_block_button.clicked.connect(lambda _checked=False: self.save_current_turn(force=True))
            button_row.addWidget(self.save_block_button)
            self.merge_button = QPushButton("Juntar com próximo")
            self.merge_button.setToolTip("Junta este bloco ao bloco seguinte quando os falantes forem iguais.")
            self.merge_button.clicked.connect(self.merge_current_turn)
            button_row.addWidget(self.merge_button)
            self.split_button = QPushButton("Dividir bloco")
            self.split_button.setToolTip("Divide o bloco pelo cursor de edição na onda ou pelo cursor do texto.")
            self.split_button.clicked.connect(self.split_current_turn)
            button_row.addWidget(self.split_button)
            button_row.addStretch()
            grid.addLayout(button_row, 3, 0, 1, 4)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            grid.addWidget(line, 4, 0, 1, 4)
            hint = QLabel("Dica: clique no texto para editar; clique no tempo ou de duplo clique na linha para ir ao audio.")
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
                return
            self.context = app_service.load_project(config_path=self.context.config_path)
            self.statuses = app_service.list_interviews(self.context)
            self._status_map = {s.interview_id: s for s in self.statuses}
            if hasattr(self, "project_label"):
                self.project_label.setText(self.project_header_text())
            self._sync_diarize_checkbox()
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
                    self.interview_table.setItem(row, column, item)
            self.interview_table.blockSignals(False)
            self.interview_table.setSortingEnabled(not manual_order_active)
            if manual_order_active:
                self.interview_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            has_rows = len(self.statuses) > 0
            self.interview_table.setVisible(has_rows)
            if hasattr(self, "_empty_table_label"):
                self._empty_table_label.setVisible(not has_rows)
            self._apply_interview_filter()
            self.update_action_states()

        def _apply_interview_filter(self) -> None:
            """Hide/show table rows based on status combo and text search."""
            if not hasattr(self, "filter_status_combo"):
                return
            status_filter = self.filter_status_combo.currentText()
            text_filter = self.filter_text_edit.text().strip().lower()
            visible_count = 0
            for row_idx in range(self.interview_table.rowCount()):
                id_item = self.interview_table.item(row_idx, COL_ARQUIVO)
                state_item = self.interview_table.item(row_idx, COL_TRANSCRICAO)
                if not id_item or not state_item:
                    continue
                real_id = str(id_item.data(Qt.ItemDataRole.UserRole) or "").lower()
                displayed_text = id_item.text().lower()
                state_text = state_item.text()
                show_by_status = True
                if status_filter == "Transcritas":
                    show_by_status = state_text == "Transcrita"
                elif status_filter == "Pendentes":
                    show_by_status = state_text == "Não transcrita"
                elif status_filter == "Processando":
                    show_by_status = state_text.startswith("Processando")
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
            if job.get("status") in {"Na fila", "Rodando"}:
                return f"Processando {job.get('progress', 0)}%"
            if status.review_exists or status.canonical_exists:
                return "Transcrita"
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

        def new_project(self) -> None:
            if not self.save_current_turn():
                return
            base_folder = QFileDialog.getExistingDirectory(self, "Escolha onde criar o projeto", self._browse_dir())
            if not base_folder:
                return
            name, ok = QInputDialog.getText(self, "Novo projeto", "Nome do projeto:")
            name = str(name).strip()
            if not ok or not name:
                return
            folder_name = safe_project_folder_name(name)
            project_root = Path(base_folder) / folder_name
            if project_root.exists():
                QMessageBox.warning(self, "Projeto já existe", f"Já existe uma pasta com este nome:\n{project_root}")
                return
            try:
                context = app_service.create_project(project_root, project_name=name)
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível criar o projeto", sanitize_message(str(exc)))
                return
            self.switch_project_context(context)
            self.progress_label.setText("Projeto criado. Use Arquivos > Adicionar midia para comecar.")

        def open_project(self) -> None:
            if not self.save_current_turn():
                return
            from .project_store import PROJECT_EXTENSION
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Abrir projeto",
                self._browse_dir(),
                f"Projetos Transcritorio (*{PROJECT_EXTENSION});;Todos os arquivos (*)",
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
            self.context = context
            from . import recent_projects
            recent_projects.save_recent(context.paths.project_root)
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
            try:
                self.context = app_service.add_audio_files(self.context, [Path(path) for path in files])
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível adicionar os arquivos", sanitize_message(str(exc)))
                return
            self.refresh_interviews()
            QMessageBox.information(self, "Arquivos adicionados", f"{len(files)} arquivo(s) foram adicionados ao projeto.")

        def save_project_metadata(self) -> None:
            if not self._require_project("Salvar projeto"):
                return
            self.context = app_service.save_project_metadata(self.context)
            self.set_save_state("Projeto salvo.")
            self.refresh_interviews()

        def open_project_folder(self) -> None:
            if not self._require_project("Abrir pasta do projeto"):
                return
            open_folder_in_explorer(self.context.paths.project_root)

        def apply_metadata_to_selected(self) -> None:
            ids = self.selected_interview_ids()
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
            self.context = app_service.update_file_metadata(self.context, ids, updates)
            self.refresh_interviews()
            self.progress_label.setText(f"Propriedades atualizadas em {len(ids)} arquivo(s).")

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

        def open_review_from_row(self, row: int, _column: int) -> None:
            item = self.interview_table.item(row, COL_ARQUIVO)
            if item:
                self.open_review(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))

        def open_selected_review(self) -> None:
            interview_id = self.selected_interview_id()
            if not interview_id:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista na lista.")
                return
            self.open_review(interview_id)

        def open_review(self, interview_id: str) -> None:
            if not self.save_current_turn():
                return
            status = self.status_by_interview_id(interview_id)
            if status and not status.review_exists and not status.canonical_exists:
                self.open_media_only(interview_id)
                return
            try:
                self.review = app_service.load_review(self.context, interview_id, create=True)
                self.current_interview_id = interview_id
                self.turns = review_store.review_turns(self.review)
                self.media_candidates = app_service.get_media_candidates(self.context, interview_id)
                self.undo_stack.clear()
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível abrir", sanitize_message(str(exc)))
                return
            if not self.media_candidates:
                QMessageBox.critical(self, "Mídia não encontrada", "Não encontrei o áudio/vídeo desta entrevista.")
                return
            self.review_title.setText(f"Transcrição: {interview_id}")
            self.set_editor_enabled(True)
            self.set_media_source(0)
            self.load_waveform()
            self.load_turn_table()
            if self.turns:
                self.select_turn_by_index(0, seek=False)
            self.set_save_state(saved_status_message())
            self.update_action_states()

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
            self.turn_table.setRowCount(0)
            self.text_edit.clear()
            self.undo_stack.clear()
            self.review_title.setText(f"Midia: {interview_id} - ainda sem transcricao")
            self.set_editor_enabled(False)
            self.set_media_source(0)
            self.load_waveform()
            self.set_save_state("Arquivo sem transcricao. Use Transcrever este arquivo para gerar a transcricao editavel.")
            self.progress_label.setText("Arquivo aberto como midia. Use Transcrever este arquivo para criar a transcricao.")
            self.update_action_states()

        def close_open_file(self, *_args: Any) -> None:
            if not self.save_current_turn():
                return
            self.player.stop()
            self.player.setSource(QUrl())
            self.review = None
            self.current_interview_id = None
            self.current_turn_id = None
            self.current_play_row = None
            self.media_candidates = []
            self.media_candidate_index = 0
            self.turns = []
            self.review_title.setText("Abra um arquivo para editar a transcricao.")
            self.turn_table.setRowCount(0)
            self.waveform_widget.set_waveform([], 0)
            self.text_edit.clear()
            self.set_editor_enabled(False)
            self.undo_stack.clear()
            self.set_save_state("Sem transcricao aberta.")
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
            self.video_widget.setVisible(self.media_has_video(media_path))
            self.player.setSource(QUrl.fromLocalFile(str(media_path)))

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
                self.progress_label.setText("Gerando onda sonora da midia original...")
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

        def seek_waveform(self, seconds: float) -> None:
            self.waveform_widget.set_edit_cursor(seconds)
            self.player.setPosition(int(seconds * 1000))

        def load_turn_table(self) -> None:
            self.current_play_row = None
            self.turn_table.setRowCount(0)
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
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, turn.get("id"))
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
                self.player.setPosition(int(start * 1000))
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

        def _set_action(self, action: QAction, enabled: bool, disabled_reason: str = "") -> None:
            """Enable/disable an action and update its tooltip with the reason."""
            action.setEnabled(enabled)
            if not enabled and disabled_reason:
                base = action.toolTip().split("\n")[0] if action.toolTip() else action.text()
                action.setToolTip(f"{base}\n({disabled_reason})")

        def update_action_states(self) -> None:
            if not hasattr(self, "save_action"):
                return
            busy = bool(self.worker and self.worker.isRunning())
            has_project = self._has_project()
            has_selected = bool(self.selected_interview_id() or self.current_interview_id)
            has_table_selection = bool(self.effective_target_ids())
            has_review = bool(self.current_interview_id and self.review)
            has_open_file = bool(self.current_interview_id)
            has_untranscribed_open_file = bool(self.current_interview_id and not self.review)
            has_turn = bool(has_review and self.current_turn_id)
            reason_busy = "Aguarde a tarefa atual terminar."
            reason_project = "Abra ou crie um projeto primeiro."
            reason_select = "Selecione ao menos um arquivo na lista."
            reason_open = "Abra uma transcrição primeiro."
            reason_turn = "Selecione um bloco na transcrição."
            self._set_action(self.new_project_action, not busy, reason_busy)
            self._set_action(self.open_project_action, not busy, reason_busy)
            self._set_action(self.add_folder_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.add_files_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.save_project_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.open_project_folder_action, not busy and has_project, reason_busy if busy else reason_project)
            self.startup_action.setEnabled(not busy)
            self.exit_action.setEnabled(True)
            self.apply_metadata_action.setEnabled(not busy and has_project and has_table_selection)
            self.queue_action.setEnabled(has_project)
            self._set_action(self.model_setup_action, not busy, reason_busy)
            self.model_status_action.setEnabled(True)
            self._set_action(self.engine_settings_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.refresh_library_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.reload_list_action, not busy and has_project, reason_busy if busy else reason_project)
            self._set_action(self.open_transcript_action, not busy and has_table_selection, reason_busy if busy else reason_select)
            self._set_action(self.transcribe_action, not busy and has_table_selection, reason_busy if busy else reason_select)
            self._set_action(self.transcribe_pending_action, not busy and bool(self.pending_transcription_ids()), reason_busy if busy else "Não há arquivos pendentes.")
            self._set_action(self.transcribe_current_action, not busy and has_untranscribed_open_file, reason_busy if busy else reason_open)
            self._set_action(self.save_action, not busy and has_turn, reason_busy if busy else reason_turn)
            self._set_action(self.generate_files_action, not busy and (has_review or has_table_selection or any(status.review_exists or status.canonical_exists for status in self.statuses)), reason_busy if busy else "Nenhuma transcrição disponível.")
            self._set_action(self.export_selected_action, not busy and has_table_selection, reason_busy if busy else reason_select)
            self._set_action(self.delete_transcription_action, not busy and has_table_selection, reason_busy if busy else reason_select)
            # Rename e reorder exigem UM unico alvo
            single_target = bool(has_project and len(self.effective_target_ids()) == 1)
            single_target_busy = False
            if single_target and self.context:
                only = self.effective_target_ids()[0]
                single_target_busy = (self.context.jobs.get(only) or {}).get("status") in ("Executando", "Na fila")
            rename_reason = "Selecione um unico arquivo para renomear." if not single_target else "Aguarde a transcricao terminar."
            reorder_reason = "Selecione um unico arquivo para reordenar." if not single_target else "Aguarde a transcricao terminar."
            self._set_action(self.rename_interview_action, not busy and single_target and not single_target_busy, reason_busy if busy else rename_reason)
            self._set_action(self.move_up_action, not busy and single_target and not single_target_busy, reason_busy if busy else reorder_reason)
            self._set_action(self.move_down_action, not busy and single_target and not single_target_busy, reason_busy if busy else reorder_reason)
            # Trash actions
            trash_busy = bool(getattr(self, "_trash_busy", False))
            any_busy = busy or trash_busy
            self._set_action(
                self.trash_selected_action,
                not any_busy and has_project and has_table_selection,
                reason_busy if any_busy else reason_select,
            )
            can_undo = bool(getattr(self, "_trash_undo", []))
            can_redo = bool(getattr(self, "_trash_redo", []))
            self._set_action(
                self.trash_undo_action,
                not any_busy and can_undo,
                reason_busy if any_busy else "Nada a desfazer nesta sessao.",
            )
            self._set_action(
                self.trash_redo_action,
                not any_busy and can_redo,
                reason_busy if any_busy else "Nada a refazer nesta sessao.",
            )
            self._set_action(self.export_current_action, not busy and has_review, reason_busy if busy else reason_open)
            self._set_action(self.close_open_file_action, not busy and has_open_file, reason_busy if busy else "Nenhum arquivo aberto.")
            self._set_action(self.open_export_folder_action, not busy, reason_busy)
            self._set_action(self.diarize_action, not busy and has_selected, reason_busy if busy else reason_select)
            self._set_action(self.improve_speakers_action, not busy and has_review, reason_busy if busy else reason_open)
            self._set_action(self.render_action, not busy and has_selected, reason_busy if busy else reason_select)
            self._set_action(self.qc_action, not busy, reason_busy)
            self.cancel_job_action.setEnabled(busy)
            if hasattr(self, "progress_bar"):
                self.progress_bar.setVisible(busy)
            if hasattr(self, "cancel_job_button"):
                self.cancel_job_button.setVisible(busy)
            if hasattr(self, "diarize_checkbox"):
                self.diarize_checkbox.setEnabled(not busy and has_project)
            if hasattr(self, "save_block_button"):
                self.save_block_button.setEnabled(not busy and has_turn)
            if hasattr(self, "merge_button"):
                self.merge_button.setEnabled(not busy and has_turn)
            if hasattr(self, "split_button"):
                self.split_button.setEnabled(not busy and has_turn)
            if hasattr(self, "transcribe_current_button"):
                self.transcribe_current_button.setVisible(has_untranscribed_open_file)
                self.transcribe_current_button.setEnabled(not busy and has_untranscribed_open_file)
            if hasattr(self, "improve_speakers_button"):
                self.improve_speakers_button.setVisible(has_review)
                self.improve_speakers_button.setEnabled(not busy and has_review)

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

        def merge_current_turn(self) -> None:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                return
            reply = QMessageBox.question(
                self, "Juntar blocos",
                "Isso vai juntar este bloco com o próximo, removendo a divisão entre eles.\n\nDeseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            if not self.save_current_turn():
                return
            before = deepcopy(self.review)
            try:
                merged_id = review_store.merge_turn_with_next(self.review, self.current_turn_id)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                self.select_turn_by_index(review_store.find_turn_index(self.review, merged_id), seek=False)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Juntar blocos", before, self.review, merged_id))
                self.set_save_state(saved_status_message())
            except Exception as exc:
                QMessageBox.warning(self, "Não foi possível juntar", sanitize_message(str(exc)))

        def split_current_turn(self) -> None:
            if not self.review or not self.current_interview_id or not self.current_turn_id:
                return
            reply = QMessageBox.question(
                self, "Dividir bloco",
                "Isso vai dividir este bloco em dois na posição atual do cursor.\n\nDeseja continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            if not self.save_current_turn():
                return
            try:
                current_index = review_store.find_turn_index(self.review, self.current_turn_id)
            except KeyError as exc:
                QMessageBox.warning(self, "Não foi possível dividir", sanitize_message(str(exc)))
                return
            before = deepcopy(self.review)
            current_turn = self.turns[current_index]
            turn_start = float(current_turn.get("start", 0) or 0)
            turn_end = float(current_turn.get("end", turn_start) or turn_start)
            cursor_pos = self.text_edit.textCursor().position()
            split_char = review_store.choose_split_char(self.text_edit.toPlainText().strip(), cursor_pos)
            player_time = self.player.position() / 1000 if self.player.position() else None
            edit_cursor = self.waveform_widget.edit_cursor
            if edit_cursor is not None and turn_start < edit_cursor < turn_end:
                split_time = edit_cursor
                split_note = "tempo definido pelo cursor de edição na onda sonora"
            elif player_time is not None and turn_start < player_time < turn_end:
                split_time = player_time
                split_note = "tempo definido pela posição do player"
            else:
                text_length = max(1, len(self.text_edit.toPlainText().strip()))
                ratio = max(0.01, min(0.99, split_char / text_length))
                split_time = turn_start + ((turn_end - turn_start) * ratio)
                split_note = "tempo estimado pela posição do cursor no texto"
            try:
                new_id = review_store.split_turn(self.review, self.current_turn_id, split_time=split_time, split_char=split_char)
                app_service.save_review(self.context, self.current_interview_id, self.review)
                self.turns = review_store.review_turns(self.review)
                self.load_turn_table()
                self.select_turn_by_index(review_store.find_turn_index(self.review, new_id), seek=False)
                self.waveform_widget.set_edit_cursor(split_time)
                self.undo_stack.push(ReviewSnapshotCommand(self, "Dividir bloco", before, self.review, new_id))
                self.set_save_state(saved_status_message())
                self.progress_label.setText(f"Bloco dividido; {split_note}. Ajuste Início/Fim se necessário.")
            except Exception as exc:
                QMessageBox.warning(self, "Não foi possível dividir", sanitize_message(str(exc)))

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

        def export_current_review(self, *_args: Any) -> None:
            self.export_reviews()

        def export_selected_reviews(self, *_args: Any) -> None:
            self.export_reviews()

        def delete_selected_transcriptions(self, *_args: Any) -> None:
            _logger.info("delete_selected_transcriptions triggered: context=%s", self.context is not None)
            if self.context is None:
                return
            ids = self.effective_target_ids()
            _logger.info("  effective_target_ids: %s | checked=%s visual=%s current_iid=%s",
                         ids, sorted(self._checked_ids),
                         sorted(self._visually_selected_interview_ids()),
                         self.current_interview_id)
            if not ids:
                QMessageBox.information(self, "Selecione arquivos", "Selecione ao menos um arquivo para limpar a transcricao.")
                return
            n = len(ids)
            if n == 1:
                msg = "Limpar a transcricao gerada deste arquivo?"
            else:
                msg = f"Limpar a transcricao gerada de {n} arquivos?"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Limpar transcricao gerada")
            box.setText(msg)
            box.setInformativeText("Os arquivos gerados (ASR, identificacao de falantes, transcricao editavel, metricas) serao apagados. O audio original e mantido no projeto — voce pode gerar a transcricao de novo depois.\n\nEsta acao nao pode ser desfeita.")
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
                QMessageBox.critical(self, "Erro ao apagar", str(exc)[:2000])
                return
            self.refresh_interviews()
            self.progress_label.setText(f"{deleted} arquivo(s) apagado(s) de {n} entrevista(s).")

        def rename_selected_interview(self, *_args: Any, cursor_row: int | None = None) -> None:
            if self.context is None:
                return
            ids = self.effective_target_ids(cursor_row)
            if len(ids) != 1:
                QMessageBox.information(self, "Selecione um arquivo", "Selecione um unico arquivo para renomear.")
                return
            interview_id = ids[0]
            busy = [iid for iid in ids if (self.context.jobs.get(iid) or {}).get("status") in ("Executando", "Na fila")]
            if busy:
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcricao terminar ou cancele o job na fila de processamento.")
                return
            metadata = self.context.metadata.get(interview_id, {})
            current_title = str(metadata.get("title") or "").strip() or interview_id
            raw, ok = QInputDialog.getText(
                self,
                "Renomear rotulo",
                "Novo rotulo para exibicao (deixe vazio para usar o nome do arquivo):",
                text=current_title,
            )
            if not ok:
                return
            new_title, truncated = _sanitize_rename_title(raw)
            title_to_store = new_title if new_title and new_title != interview_id else ""
            try:
                self.context = app_service.rename_interview(self.context, interview_id, title_to_store)
            except app_service.InterviewBusyError:
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcricao terminar ou cancele o job na fila de processamento.")
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
                QMessageBox.information(self, "Selecione um arquivo", "Selecione um unico arquivo para reordenar.")
                return
            moving_id = ids[0]
            if (self.context.jobs.get(moving_id) or {}).get("status") in ("Executando", "Na fila"):
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcricao terminar ou cancele o job na fila de processamento.")
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
                QMessageBox.information(self, "Acao bloqueada", "Aguarde a transcricao terminar ou cancele o job na fila de processamento.")
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
            _logger.info("trash_selected_interviews triggered: context=%s busy=%s cursor_row=%s",
                         self.context is not None, self._trash_busy, cursor_row)
            if self.context is None or self._trash_busy:
                _logger.info("  return: context None or busy")
                return
            ids = self.effective_target_ids(cursor_row)
            _logger.info("  effective_target_ids: %s | checked=%s visual=%s",
                         ids, sorted(self._checked_ids), sorted(self._visually_selected_interview_ids()))
            if not ids:
                QMessageBox.information(self, "Selecione arquivos", "Selecione ao menos um arquivo para enviar a Lixeira.")
                return
            busy_ids = [iid for iid in ids if (self.context.jobs.get(iid) or {}).get("status") in ("Executando", "Na fila")]
            if busy_ids:
                QMessageBox.information(self, "Acao bloqueada", "Nao e possivel enviar arquivos com transcricao em andamento. Aguarde ou cancele o job na fila de processamento.")
                return
            n = len(ids)
            if n == 1:
                text = "Enviar este arquivo para a Lixeira do projeto?"
            else:
                text = f"Enviar {n} arquivos para a Lixeira do projeto?"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Enviar para Lixeira")
            box.setText(text)
            box.setInformativeText("O audio original, a transcricao e os metadados serao movidos para a Lixeira do projeto (00_project/.trash/). Voce pode desfazer com Ctrl+Z enquanto esta sessao estiver aberta.")
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
                QMessageBox.information(self, "Acao bloqueada", "Nao e possivel mover arquivos com transcricao em andamento. Aguarde ou cancele o job na fila de processamento.")
                return
            except Exception as exc:
                QMessageBox.critical(self, "Erro ao preparar exclusao", str(exc)[:2000])
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
                staging.rename(files_dir)
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
                self._on_trash_worker_finished(entry_dict, "", n, async_mode=False)
            except Exception as exc:
                shutil.rmtree(_Path(trash_entry["trash_dir"]), ignore_errors=True)
                self._trash_busy = False
                self.update_action_states()
                QMessageBox.critical(self, "Erro ao mover para lixeira", str(exc)[:2000])

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
                QMessageBox.critical(self, "Erro ao finalizar exclusao", str(exc)[:2000])
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

        def undo_last_trash(self, *_args: Any) -> None:
            if self._trash_busy or self.context is None or not self._trash_undo:
                return
            # Guard: se foco esta em QTextEdit editavel, delegar para o undo_action do editor
            focus = QApplication.focusWidget()
            if isinstance(focus, QTextEdit) and not focus.isReadOnly():
                # Delegar ao undo nativo do editor (ou QUndoStack via undo_action)
                if hasattr(self, "undo_action") and self.undo_action.isEnabled():
                    self.undo_action.trigger()
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
                box.setInformativeText("Restaurar vai sobrescrever os arquivos atuais. Essa acao nao pode ser desfeita.")
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
                    "Nao e possivel refazer",
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
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Lixeira do projeto")
            box.setText(f"Ha {n} item(ns) na lixeira desta sessao ({size_mb:.1f} MB). Manter em .trash/ ou apagar definitivamente?")
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
            busy_single = job_status in ("Executando", "Na fila")
            menu = QMenu(self)
            self.rename_interview_action.setEnabled(single and not busy_single)
            self.move_up_action.setEnabled(single and not busy_single)
            self.move_down_action.setEnabled(single and not busy_single)
            menu.addAction(self.rename_interview_action)
            menu.addSeparator()
            menu.addAction(self.move_up_action)
            menu.addAction(self.move_down_action)
            menu.addSeparator()
            menu.addAction(self.delete_transcription_action)
            menu.addAction(self.trash_selected_action)
            menu.exec(viewport.mapToGlobal(pos))

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
                QMessageBox.information(self, "Nada para exportar", "Nao encontrei transcricoes para o escopo escolhido.")
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
                    "Nenhum arquivo foi gerado. Verifique se as transcricoes estao prontas." + (
                        "\n\nSem transcricao exportavel:\n" + "\n".join(skipped) if skipped else ""
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
            if not self._require_project("Abrir pasta de exportacao"):
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
            self.player.setPosition(target)

        def repeat_current_turn(self) -> None:
            if not self.review or not self.current_turn_id:
                return
            index = review_store.find_turn_index(self.review, self.current_turn_id)
            start = float(self.turns[index].get("start", 0) or 0)
            self.player.setPosition(int(start * 1000))
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
            self.player.setPosition(self.position_slider.value())

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
                    item.setBackground(QColor("#fff3bf"))
                    item.setForeground(QColor("#1f2933"))
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
                QMessageBox.information(self, "Abra um arquivo", "Abra uma midia antes de transcrever este arquivo.")
                return
            self.run_full_transcription_job(ids=[self.current_interview_id])

        def run_pending_transcription_job(self, *_args: Any) -> None:
            ids = self.pending_transcription_ids()
            if not ids:
                QMessageBox.information(self, "Nada pendente", "Todos os arquivos do projeto ja tem transcricao editavel.")
                return
            self.run_full_transcription_job(ids=ids)

        def run_manifest_job(self) -> None:
            if not self.save_current_turn():
                return
            self.start_worker("Atualizar biblioteca", [("Procurando gravações...", lambda: app_service.refresh_manifest(self.context))])

        def run_full_transcription_job(self, ids: list[str] | None = None) -> None:
            if not self.save_current_turn():
                return
            if not self.ensure_models_ready():
                return
            ids = ids or self.selected_ids_for_job(fallback_current=True)
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista para transcrever.")
                return
            steps: list[tuple] = []
            weights: list[int] = []
            # Dynamic weights from benchmark data (tests/benchmark_exhaustive_2026-04-19.csv)
            asr_model = str(self.context.config.get("asr_model", "large-v3-turbo"))
            asr_device = str(self.context.config.get("asr_device", "cuda"))
            do_diarize = bool(self.context.config.get("diarize", True))
            w = _pipeline_weights(asr_model, asr_device)
            if not do_diarize:
                w = [w[0], w[1], 0, w[3], w[4]]  # zero weight for skipped diarize
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
            for index, interview_id in enumerate(ids, start=1):
                prefix = f"{index}/{len(ids)} {interview_id}"
                file_steps = [
                    self.job_step(f"{prefix}: preparando audio...", interview_id, "preparar audio", r[0], r[1], lambda item=interview_id: app_service.prepare_interviews(self.context, ids=[item])),
                    self.job_step(
                        f"{prefix}: transcrevendo fala...",
                        interview_id,
                        "transcrever",
                        r[1],
                        r[2],
                        lambda progress, should_cancel, item=interview_id: app_service.transcribe_interviews(
                            self.context,
                            ids=[item],
                            overrides={"diarize": False},
                            progress_callback=progress,
                            should_cancel=should_cancel,
                        ),
                        accepts_progress=True,
                    ),
                ]
                if do_diarize:
                    file_steps.append(
                        self.job_step(
                            f"{prefix}: identificando falantes...",
                            interview_id,
                            "identificar falantes",
                            r[2],
                            r[3],
                            lambda progress, should_cancel, item=interview_id: app_service.diarize_interviews(
                                self.context,
                                ids=[item],
                                progress_callback=progress,
                                should_cancel=should_cancel,
                            ),
                            accepts_progress=True,
                        ),
                    )
                file_steps.extend([
                    self.job_step(f"{prefix}: montando transcricao editavel...", interview_id, "montar transcricao", r[3], r[4], lambda item=interview_id: app_service.render_interviews(self.context, ids=[item], overrides=render_overrides)),
                    self.job_step(f"{prefix}: verificando arquivos gerados...", interview_id, "verificar arquivos", r[4], r[5], lambda item=interview_id: app_service.qc_interviews(self.context, ids=[item])),
                ])
                steps.extend(file_steps)
                weights.extend(w)
            self.refresh_interviews()
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
        ) -> tuple:
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

                try:
                    result = func(relay, should_cancel or (lambda: False)) if accepts_progress else func()
                    failures = getattr(result, "failures", 0)
                    if failures:
                        app_service.update_job(
                            self.context,
                            interview_id,
                            {
                                "status": "Falha",
                                "stage": stage,
                                "progress": start_progress,
                                "last_error": f"{failures} falha(s).",
                                "finished_at": datetime.now().isoformat(timespec="seconds"),
                                "estimated_finish_at": "",
                            },
                        )
                    else:
                        updates = {"status": "Rodando", "stage": stage, "progress": end_progress, "last_error": "", "estimated_finish_at": ""}
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

            return (message, run, accepts_progress)

        def run_diarization_job(self) -> None:
            if not self.save_current_turn():
                return
            if not self.ensure_models_ready():
                return
            ids = self.selected_ids_for_job()
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista para identificar falantes.")
                return
            self.start_worker("Identificar falantes", [("Identificando falantes...", lambda: app_service.diarize_interviews(self.context, ids=ids))])

        def improve_speakers_current_file(self, *_args: Any) -> None:
            if not self.current_interview_id:
                QMessageBox.information(self, "Abra um arquivo", "Abra uma transcricao antes de melhorar os falantes.")
                return
            if not self.save_current_turn(force=True):
                return
            if not self.ensure_models_ready():
                return
            interview_id = self.current_interview_id
            answer = QMessageBox.question(
                self,
                "Melhorar falantes deste arquivo",
                "Esta acao refaz a diarizacao local e recria a transcricao editavel deste arquivo. Edicoes manuais ja feitas nesta transcricao podem ser substituidas. Continuar?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            steps = [
                self.job_step(
                    f"{interview_id}: identificando falantes...",
                    interview_id,
                    "identificar falantes",
                    0,
                    70,
                    lambda progress, should_cancel, item=interview_id: app_service.diarize_interviews(
                        self.context, ids=[item], progress_callback=progress, should_cancel=should_cancel,
                    ),
                    accepts_progress=True,
                ),
                self.job_step(
                    f"{interview_id}: remontando transcricao editavel...",
                    interview_id,
                    "montar transcricao",
                    70,
                    95,
                    lambda item=interview_id: app_service.render_interviews(self.context, ids=[item], overrides={"diarization_source": "pyannote_exclusive"}),
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
            self.start_worker(f"Melhorar falantes de {interview_id}", steps, weights=[70, 25, 5])

        def run_render_job(self) -> None:
            if not self.save_current_turn():
                return
            ids = self.selected_ids_for_job()
            if not ids:
                QMessageBox.information(self, "Selecione uma entrevista", "Selecione uma entrevista para montar a transcrição editável.")
                return
            self.start_worker("Montar transcrição editável", [("Montando transcrição editável...", lambda: app_service.render_interviews(self.context, ids=ids, overrides={"diarization_source": "pyannote_exclusive"}))])

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
            self.start_worker("Verificar exportacoes", [("Verificando exportacoes...", lambda: app_service.qc_interviews(self.context, ids=ids))])

        def start_worker(self, label: str, steps: list[tuple], weights: list[int] | None = None) -> None:
            if self.worker and self.worker.isRunning():
                QMessageBox.information(
                    self,
                    "Tarefa em andamento",
                    f"{self.current_job_label or 'Uma tarefa'} ainda esta em andamento. O aplicativo nao esta travado.",
                )
                return
            self.current_job_label = label
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

        def on_worker_progress(self, message: str, percent: int) -> None:
            if percent < self.progress_bar.value():
                return  # Ignore stale signals — never regress text or bar
            self.progress_label.setText(message)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, percent)))

        def on_worker_done(self, message: str) -> None:
            self.progress_bar.setRange(0, 100)
            self.progress_label.setText(message)
            if "interrompido" in message:
                self.progress_bar.setValue(max(0, min(100, self.progress_bar.value())))
            else:
                self.progress_bar.setValue(100)
            self.current_job_label = ""
            self.refresh_interviews()
            if self.current_interview_id:
                current_id = self.current_interview_id
                status = self.status_by_interview_id(current_id)
                try:
                    if status and (status.review_exists or status.canonical_exists):
                        self.review = app_service.load_review(self.context, current_id, create=True)
                        self.turns = review_store.review_turns(self.review)
                        self.set_editor_enabled(True)
                        self.review_title.setText(f"Transcricao: {current_id}")
                        self.load_turn_table()
                        if self.turns:
                            self.select_turn_by_index(0, seek=False)
                except Exception as exc:
                    _logger.warning("Falha ao recarregar review de %s: %s", current_id, exc)
            self.update_action_states()
            if self._close_after_worker:
                self._close_after_worker = False
                self.close()

        def on_worker_failed(self, message: str) -> None:
            self.progress_bar.setRange(0, 100)
            self.progress_label.setText("Falha.")
            self.progress_bar.setValue(0)
            self.current_job_label = ""
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle("Não foi possível concluir a tarefa")
            dialog.setText("A tarefa terminou com erro.")
            dialog.setInformativeText("Verifique a entrevista selecionada, o token/modelo quando houver separação de falantes, e tente novamente.")
            dialog.setDetailedText(message)
            dialog.exec()
            self.refresh_interviews()
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
                if not self.worker.wait(5000):
                    _logger.warning(
                        "Transcription worker did not stop gracefully within 5s; abandoning thread."
                    )
            # Trash worker: NUNCA terminate() (pode corromper copy in-flight)
            if getattr(self, "_trash_worker", None) is not None and self._trash_worker.isRunning():
                self._trash_worker.request_cancel()
                self._trash_worker.wait()  # bloqueante — sem timeout
            # Purga interativa da lixeira da sessao
            try:
                self._maybe_purge_session_trash()
            except Exception as exc:
                _logger.warning("_maybe_purge_session_trash falhou: %s", exc)
            self.save_current_turn()
            self.player.stop()
            event.accept()


def _apply_dark_theme(app) -> None:
    """Aplica tema escuro global (Fusion + QPalette dark).

    Fusion ignora o tema do SO (evita variacao claro/escuro conforme o Windows);
    QPalette forca cores escuras consistentes. Desabilitados com cor apagada."""
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtWidgets import QStyleFactory
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    pal.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.ColorRole.Link, QColor(64, 160, 232))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(127, 127, 127))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 80))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(200, 200, 200))
    app.setPalette(pal)
    # Tooltip explicito (em alguns estilos o ToolTipBase do palette nao cobre tudo)
    app.setStyleSheet(
        "QToolTip { color: #dcdcdc; background-color: #2d2d2d; border: 1px solid #555; }"
    )


def main() -> int:
    if QT_IMPORT_ERROR is not None:
        print(
            "PySide6 nao esta instalado no ambiente Python atual. "
            "Instale PySide6 no venv de transcricao para abrir o Estudio de Revisao.",
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
    app = QApplication(sys.argv)
    _apply_dark_theme(app)
    window = ReviewStudioWindow(project_root=project_root)
    window.show()
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        QTimer.singleShot(0, window.show_startup_dialog)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
