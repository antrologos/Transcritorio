from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from array import array
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
import time
import wave

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
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QProgressBar,
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
    return f"Salvo às {datetime.now().strftime('%H:%M')}."


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
    if percent < 3:
        return "estimando..."
    elapsed = time.monotonic() - started_monotonic
    if elapsed < 8:
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
                    self.progress.emit(f"Etapa {index} de {len(self.steps)}: {message} - {start_percent}% - {eta_from_progress(self.started_monotonic, start_percent)}", start_percent)
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
                    self.progress.emit(f"Etapa {index} de {len(self.steps)} concluida: {message} - {end_percent}% - {eta_from_progress(self.started_monotonic, end_percent)}", end_percent)
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
                elif event == "asr_progress":
                    label = f"{message} {inner_percent}%"
                else:
                    label = message
                eta = eta_from_progress(self.started_monotonic, percent)
                self.progress.emit(f"Etapa {index} de {total}: {label} - {percent}% - {eta}", percent)

            return callback


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
        def __init__(self, default_scope: str = "current", parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Exportar")
            layout = QVBoxLayout(self)

            layout.addWidget(QLabel("O que exportar:"))
            self.scope_combo = QComboBox()
            for value, label in [
                ("current", "Arquivo aberto"),
                ("selected", "Arquivos selecionados"),
                ("all", "Todas as transcricoes do projeto"),
            ]:
                self.scope_combo.addItem(label, value)
            self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(default_scope)))
            layout.addWidget(self.scope_combo)

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
            hint.setStyleSheet("color: #555;")
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def selected_scope(self) -> str:
            return str(self.scope_combo.currentData())

        def selected_formats(self) -> list[str]:
            return [fmt for fmt, checkbox in self.checkboxes.items() if checkbox.isChecked()]


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
            hint.setStyleSheet("color: #555;")
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
            self.model_edit = QLineEdit(str(config.get("asr_model") or "large-v3"))
            grid.addWidget(QLabel("Modelo Whisper:"), 0, 0)
            grid.addWidget(self.model_edit, 0, 1)

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

            self.default_speakers_spin = QSpinBox()
            self.default_speakers_spin.setRange(1, 20)
            self.default_speakers_spin.setValue(int(config.get("diarization_num_speakers") or config.get("min_speakers") or 2))
            grid.addWidget(QLabel("Falantes padrao:"), 4, 0)
            grid.addWidget(self.default_speakers_spin, 4, 1)

            self.batch_spin = QSpinBox()
            self.batch_spin.setRange(1, 32)
            self.batch_spin.setValue(int(config.get("asr_batch_size") or 4))

            layout.addLayout(grid)
            advanced_group = QGroupBox("Avancado")
            advanced_layout = QGridLayout(advanced_group)
            advanced_layout.addWidget(QLabel("Batch:"), 0, 0)
            advanced_layout.addWidget(self.batch_spin, 0, 1)
            layout.addWidget(advanced_group)

            hint = QLabel("Batch controla quantos trechos o Whisper processa por vez. Aumentar pode acelerar em GPU com memoria sobrando; reduzir evita falta de memoria. Para computador sem GPU NVIDIA, use CPU com int8 ou float32.")
            hint.setStyleSheet("color: #555;")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def updates(self) -> dict[str, Any]:
            device = str(self.device_combo.currentData())
            compute_type = str(self.compute_combo.currentData())
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"
            speaker_count = int(self.default_speakers_spin.value())
            language = str(self.language_combo.currentData())
            return {
                "asr_model": self.model_edit.text().strip() or "large-v3",
                "asr_device": device,
                "asr_compute_type": compute_type,
                "asr_batch_size": int(self.batch_spin.value()),
                "asr_language": None if language == "auto" else language,
                "diarization_num_speakers": speaker_count,
                "min_speakers": speaker_count,
                "max_speakers": speaker_count,
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
        PAGE_TOKEN = 3
        PAGE_DOWNLOAD = 4
        PAGE_DONE = 5

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.download_completed = False
            self.setWindowTitle(f"{APP_NAME} — Configuração inicial")
            self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
            self.setFixedWidth(680)
            self.setMinimumHeight(520)
            self.setOption(QWizard.WizardOption.NoCancelButton, False)
            self.setButtonText(QWizard.WizardButton.NextButton, "Próximo →")
            self.setButtonText(QWizard.WizardButton.BackButton, "← Voltar")
            self.setButtonText(QWizard.WizardButton.CancelButton, "Pular por agora")
            self.setButtonText(QWizard.WizardButton.FinishButton, "Começar a usar")

            self.setPage(self.PAGE_WELCOME, self._make_welcome_page())
            self.setPage(self.PAGE_ACCOUNT, self._make_account_page())
            self.setPage(self.PAGE_TERMS, self._make_terms_page())
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
            faq_layout.addWidget(QLabel(
                "São arquivos que ensinam o computador a reconhecer fala em português. "
                "Funcionam como um dicionário muito sofisticado. "
                "Depois de baixados, tudo funciona sem internet."
            ))
            layout.addWidget(faq)
            layout.addStretch()
            return page

        def _make_account_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle("Passo 1 de 4: Criar uma conta gratuita")
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel(
                "Os componentes de transcrição ficam em um site chamado Hugging Face — "
                "uma biblioteca pública de inteligência artificial. É gratuito e seguro, "
                "como se fosse um \"Google Acadêmico\" de modelos de IA.\n\n"
                "Você precisa criar uma conta lá para poder baixar os componentes. "
                "Use qualquer e-mail (pode ser o institucional)."
            ))
            btn = QPushButton("Abrir site para criar minha conta →")
            btn.setStyleSheet("font-weight: 700; padding: 8px; color: #2e7d32;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/join")))
            layout.addWidget(btn)
            layout.addWidget(QLabel(
                "\nDepois de criar sua conta no site (no navegador), "
                "volte aqui e clique em \"Próximo\".\n\n"
                "Já tem conta? Pode pular direto para o próximo passo."
            ))
            faq = QGroupBox("Dúvidas frequentes")
            faq_l = QVBoxLayout(faq)
            faq_l.addWidget(QLabel(
                "\"É seguro criar conta?\" — Sim. Hugging Face é reconhecido pela comunidade científica.\n\n"
                "\"Vou pagar alguma coisa?\" — Não. A conta gratuita é suficiente.\n\n"
                "\"Posso usar conta do Google?\" — Sim, o site permite login com Google."
            ))
            layout.addWidget(faq)
            layout.addStretch()
            return page

        def _make_terms_page(self) -> QWizardPage:
            page = QWizardPage()
            page.setTitle("Passo 2 de 4: Autorizar o modelo de identificação de falantes")
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel(
                "Além do modelo de transcrição (que é livre), usamos um segundo modelo "
                "que identifica quem está falando em cada trecho — ou seja, separa a fala "
                "do entrevistador da fala do entrevistado.\n\n"
                "Esse modelo exige que você aceite os termos de uso no site. "
                "É só fazer login e clicar em \"Agree and access repository\" (Concordar).\n\n"
                "Se o site estiver em inglês, procure o botão azul \"Agree\"."
            ))
            btn = QPushButton("Abrir página do modelo para aceitar os termos →")
            btn.setStyleSheet("font-weight: 700; padding: 8px; color: #2e7d32;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/pyannote/speaker-diarization-community-1")))
            layout.addWidget(btn)
            faq = QGroupBox("O que estou aceitando?")
            faq_l = QVBoxLayout(faq)
            faq_l.addWidget(QLabel(
                "Você está aceitando os termos de uso do modelo \"pyannote\", criado por "
                "pesquisadores franceses. Os termos dizem basicamente que você usará o modelo "
                "para fins legítimos. Não há custo e não há coleta de dados."
            ))
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

    class _TokenWizardPage(QWizardPage):
        """Page 3: token entry with pre-validation."""

        def __init__(self) -> None:
            super().__init__()
            self.setTitle("Passo 3 de 4: Criar e colar a chave de acesso")
            self._validated = False
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel(
                "Agora você precisa criar uma \"chave de acesso\" no Hugging Face. "
                "É como uma senha temporária que permite ao Transcritório baixar os componentes.\n\n"
                "Como criar (3 cliques):\n"
                "  1. Clique no botão abaixo para abrir a página de chaves.\n"
                "  2. Clique em \"Create new token\".\n"
                "     • Em \"Token name\", escreva: Transcritorio\n"
                "     • Em \"Type\", selecione: Read\n"
                "     • Clique em \"Create token\"\n"
                "  3. Copie a chave gerada e cole no campo abaixo."
            ))
            btn = QPushButton("Abrir página de chaves no Hugging Face →")
            btn.setStyleSheet("font-weight: 700; padding: 8px; color: #2e7d32;")
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/settings/tokens")))
            layout.addWidget(btn)
            layout.addSpacing(12)
            layout.addWidget(QLabel("Cole sua chave aqui:"))
            self.token_edit = QLineEdit()
            self.token_edit.setPlaceholderText("Cole aqui a chave (começa com hf_...)")
            # Plain text field so user can see what they pasted
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
            privacy.setStyleSheet("color: #555; font-size: 11px;")
            privacy.setWordWrap(True)
            layout.addWidget(privacy)

        def _on_token_changed(self) -> None:
            self._validated = False
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.completeChanged.emit()

        def isComplete(self) -> bool:
            return self._validated

        def validatePage(self) -> bool:
            from . import model_manager
            token = self.token_edit.text().strip()
            if not token:
                self.status_label.setText("Cole a chave de acesso no campo acima.")
                self.status_label.setStyleSheet("color: #c00;")
                return False
            self.status_label.setText("Verificando sua chave...")
            self.status_label.setStyleSheet("color: #555;")
            # Force UI repaint before blocking call
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            # Validate token
            result = model_manager.validate_token(token)
            if not result["valid"]:
                self.status_label.setText(result["message"])
                self.status_label.setStyleSheet("color: #c00;")
                return False
            # Check gated model access
            gated = model_manager.check_gated_access(token)
            if not gated["access"]:
                self.status_label.setText(gated["message"])
                self.status_label.setStyleSheet("color: #e65100;")
                return False
            self.status_label.setText(f"✓ {result['message']} {gated['message']}")
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: 700;")
            self._validated = True
            self.completeChanged.emit()
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
            self.setTitle("Passo 4 de 4: Baixar os componentes")
            self.setFinalPage(False)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel(
                "Tudo pronto! Agora vamos baixar os componentes de inteligência artificial.\n\n"
                "Isso pode levar de 5 a 30 minutos, dependendo da velocidade da sua internet. "
                "Você pode continuar usando o computador normalmente."
            ))
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
                self.progress_label.setStyleSheet("color: #c00;")
                return
            self._download_started = True
            token_page = self._wizard.page(FirstRunWizard.PAGE_TOKEN)
            token = token_page.token() if hasattr(token_page, "token") else ""
            self.progress_label.setText("Iniciando download...")
            self.progress_bar.setValue(0)
            self._worker = _SetupDownloadThread(token)
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
            self.progress_label.setStyleSheet("color: #2e7d32; font-weight: 700;")
            self.completeChanged.emit()

        def _on_failed(self, message: str) -> None:
            self.progress_label.setText(f"Erro: {message}\n\nVerifique sua conexão e tente novamente.")
            self.progress_label.setStyleSheet("color: #c00;")
            self._download_started = False  # allow retry via Back + Next

    class _SetupDownloadThread(QThread):
        progress = Signal(str, int)
        finished_ok = Signal()
        failed = Signal(str)

        def __init__(self, token: str) -> None:
            super().__init__()
            self.token = token

        def run(self) -> None:
            try:
                def on_progress(detail: dict) -> None:
                    msg = detail.get("message", "")
                    pct = int(detail.get("progress", 0))
                    self.progress.emit(msg, pct)
                result = app_service.download_models(
                    token=self.token,
                    progress_callback=on_progress,
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
                current_label.setStyleSheet("color: #555;")
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
                ("open", "Abrir projeto existente", "Selecionar uma pasta de projeto já existente."),
            ]:
                button = QPushButton(label)
                button.setToolTip(help_text)
                button.clicked.connect(lambda _checked=False, selected=choice: self.select_choice(selected))
                layout.addWidget(button)

            layout.addStretch()
            status_label = QLabel("✓ Componentes de IA instalados")
            status_label.setStyleSheet("color: #2e7d32; font-size: 11px;")
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
            layout.addWidget(self.token_edit)

            self.remember_checkbox = QCheckBox("Lembrar neste computador usando cofre seguro")
            self.remember_checkbox.setEnabled(False)
            self.remember_checkbox.setToolTip("Este build usa o token apenas para esta sessao de download; armazenamento seguro multiplataforma sera ligado no instalador.")
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
            self.new_project_action.setToolTip("Criar uma nova pasta de projeto de transcricoes.")
            self.new_project_action.triggered.connect(self.new_project)

            self.open_project_action = QAction("Abrir projeto...", self)
            self.open_project_action.setToolTip("Abrir uma pasta de projeto de transcricoes existente.")
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

            self.open_transcript_action = QAction("Abrir arquivo", self)
            self.open_transcript_action.setShortcut(QKeySequence.StandardKey.Open)
            self.open_transcript_action.setToolTip("Abrir a transcrição do arquivo selecionado para edição. (Ctrl+O)\nSelecione um arquivo na lista.")
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
            self.generate_files_action.setShortcut(QKeySequence.StandardKey.SaveAs)
            self.generate_files_action.setToolTip("Exportar a transcricao aberta, os arquivos selecionados ou todas as transcricoes.")
            self.generate_files_action.triggered.connect(self.export_current_review)

            self.export_selected_action = QAction("Exportar selecionados...", self)
            self.export_selected_action.setToolTip("Exportar as transcricoes dos arquivos selecionados.")
            self.export_selected_action.triggered.connect(self.export_selected_reviews)

            self.export_current_action = QAction("Exportar este arquivo...", self)
            self.export_current_action.setToolTip("Exportar apenas a transcricao aberta.")
            self.export_current_action.triggered.connect(self.export_current_review)

            self.close_open_file_action = QAction("Fechar arquivo aberto", self)
            self.close_open_file_action.setToolTip("Fechar o arquivo aberto e voltar à lista de entrevistas.")
            self.close_open_file_action.triggered.connect(self.close_open_file)

            self.open_export_folder_action = QAction("Abrir pasta de arquivos gerados", self)
            self.open_export_folder_action.setToolTip("Abrir no Explorador a pasta onde ficam os arquivos exportados.")
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

            self.cancel_job_action = QAction("Cancelar", self)
            self.cancel_job_action.setToolTip("Cancela o processamento atual. O WhisperX e interrompido; outras etapas param no proximo ponto seguro.")
            self.cancel_job_action.triggered.connect(self.cancel_current_job)

            self.undo_action = self.undo_stack.createUndoAction(self, "Desfazer")
            self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
            self.redo_action = self.undo_stack.createRedoAction(self, "Refazer")
            self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)

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
            project_menu = self.menuBar().addMenu("Projeto")
            project_menu.addAction(self.new_project_action)
            project_menu.addAction(self.open_project_action)
            recent_menu = project_menu.addMenu("Projetos recentes")
            from . import recent_projects
            for rp in recent_projects.load_recent()[:5]:
                recent_menu.addAction(str(rp), lambda p=rp: self._open_project_path(p))
            if self.context is not None:
                recent_menu.addSeparator()
                recent_menu.addAction(str(self.context.paths.project_root), self.refresh_interviews)
            project_menu.addSeparator()
            project_menu.addAction(self.open_project_folder_action)
            project_menu.addSeparator()
            project_menu.addAction(self.exit_action)

            files_menu = self.menuBar().addMenu("Arquivos")
            add_media_menu = files_menu.addMenu("Adicionar midia...")
            add_media_menu.addAction(self.add_files_action)
            add_media_menu.addAction(self.add_folder_action)
            files_menu.addAction(self.apply_metadata_action)
            files_menu.addSeparator()
            files_menu.addAction(self.transcribe_action)
            files_menu.addAction(self.transcribe_pending_action)
            files_menu.addSeparator()
            files_menu.addAction(self.export_selected_action)

            open_file_menu = self.menuBar().addMenu("Arquivo aberto")
            open_file_menu.addAction(self.open_transcript_action)
            open_file_menu.addAction(self.save_action)
            open_file_menu.addAction(self.export_current_action)
            open_file_menu.addAction(self.improve_speakers_action)
            open_file_menu.addAction(self.close_open_file_action)
            open_file_menu.addSeparator()
            open_file_menu.addAction(self.open_export_folder_action)
            open_file_menu.addSeparator()
            open_file_menu.addAction(self.undo_action)
            open_file_menu.addAction(self.redo_action)

            settings_menu = self.menuBar().addMenu("Configuracoes")
            settings_menu.addAction(self.model_setup_action)
            settings_menu.addAction(self.model_status_action)
            settings_menu.addSeparator()
            settings_menu.addAction(self.engine_settings_action)
            settings_menu.addAction(self.queue_action)

            help_menu = self.menuBar().addMenu("Ajuda")
            help_menu.addAction(self.documentation_action)
            help_menu.addAction(self.credits_action)
            help_menu.addAction(self.about_action)

        def show_workflow_help(self) -> None:
            QMessageBox.information(
                self,
                "Fluxo de trabalho",
                "Use: Arquivos > Adicionar midia -> Transcrever selecionados -> Abrir arquivo -> Editar -> Salvar transcricao -> Exportar.",
            )

        def show_about(self) -> None:
            QMessageBox.information(
                self,
                f"Sobre {APP_NAME}",
                f"{APP_NAME}\n\nCreditos: {APP_CREDITS}\n\nTranscricao local com WhisperX e pyannote.",
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
            self.progress_label.setText("Configuracao de transcricao atualizada.")

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
                        self.progress_label.setStyleSheet("color: #c00; font-weight: 700;")
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
            self.project_label.setStyleSheet("color: #555;")
            header.addWidget(self.project_label)
            root_layout.addLayout(header)

            action_bar = QHBoxLayout()
            action_bar.addWidget(self.media_button())
            action_bar.addWidget(self.transcribe_menu_button())
            action_bar.addWidget(self.action_button(self.save_action))
            action_bar.addWidget(self.action_button(self.generate_files_action))
            action_bar.addStretch()
            root_layout.addLayout(action_bar)

            progress_row = QHBoxLayout()
            self.progress_label = QLabel("Pronto.")
            self.save_status_label = QLabel("Sem transcrição aberta.")
            self.save_status_label.setStyleSheet("color: #555;")
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
            # Interview table (9 columns)
            self.interview_table = QTableWidget(0, 9)
            self.interview_table.setAccessibleName("Arquivos do projeto")
            self.interview_table.setHorizontalHeaderLabels([
                "Arquivo", "Formato", "Transcrição", "Duração",
                "Língua", "Falantes", "Rótulos", "Contexto", "Avisos",
            ])
            for col in range(8):
                self.interview_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            self.interview_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
            self.interview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.interview_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
            self.interview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.interview_table.setSortingEnabled(True)
            self.interview_table.cellDoubleClicked.connect(self.open_review_from_row)
            self.interview_table.itemSelectionChanged.connect(self.update_action_states)
            layout.addWidget(self.interview_table, stretch=1)
            self._empty_table_label = QLabel("Nenhuma entrevista.\nUse Arquivos \u203a Adicionar m\u00eddia.")
            self._empty_table_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_table_label.setStyleSheet("color: #888; font-size: 13px; padding: 24px;")
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
            return f"Projeto: {name}  |  {self.context.paths.project_root}"

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
            hint.setStyleSheet("color: #555;")
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
            self.interview_table.setSortingEnabled(False)
            self.interview_table.blockSignals(True)
            self.interview_table.setRowCount(0)
            for status in self.statuses:
                row = self.interview_table.rowCount()
                self.interview_table.insertRow(row)
                metadata = self.context.metadata.get(status.interview_id, {})
                metadata_display = project_store.metadata_display(metadata)
                job = self.context.jobs.get(status.interview_id, {})
                values = [
                    status.interview_id,
                    media_format_label(status),
                    self.friendly_state(status, job),
                    format_clock(float(status.duration_sec) if status.duration_sec else 0),
                    metadata_display["language"],
                    metadata_display["speakers"],
                    metadata_display["speaker_labels"],
                    metadata_display["context"],
                    status.qc_notes,
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, status.interview_id)
                    self.interview_table.setItem(row, column, item)
            self.interview_table.blockSignals(False)
            self.interview_table.setSortingEnabled(True)
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
                id_item = self.interview_table.item(row_idx, 0)
                state_item = self.interview_table.item(row_idx, 2)  # column 2 = Transcrição
                if not id_item or not state_item:
                    continue
                interview_id = id_item.text().lower()
                state_text = state_item.text()
                show_by_status = True
                if status_filter == "Transcritas":
                    show_by_status = state_text == "Transcrita"
                elif status_filter == "Pendentes":
                    show_by_status = state_text == "Não transcrita"
                elif status_filter == "Processando":
                    show_by_status = state_text.startswith("Processando")
                show_by_text = text_filter in interview_id if text_filter else True
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
            rows = self.interview_table.selectionModel().selectedRows()
            if not rows:
                return []
            ids: list[str] = []
            for model_index in rows:
                item = self.interview_table.item(model_index.row(), 0)
                if item:
                    ids.append(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
            return ids

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
            folder = QFileDialog.getExistingDirectory(self, "Escolha a pasta do projeto", self._browse_dir())
            if not folder:
                return
            try:
                context = app_service.open_project(Path(folder))
            except Exception as exc:
                QMessageBox.critical(self, "Não foi possível abrir o projeto", sanitize_message(str(exc)))
                return
            self.switch_project_context(context)
            self.progress_label.setText("Projeto aberto.")

        def switch_project_context(self, context: app_service.ProjectContext) -> None:
            self.player.stop()
            self.context = context
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

        def open_review_from_row(self, row: int, _column: int) -> None:
            item = self.interview_table.item(row, 0)
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
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                peaks, duration = load_media_waveform_peaks(source_path)
                if peaks:
                    save_waveform_cache(cache_path, source_path, peaks, duration)
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

        def set_save_state(self, message: str, error: bool = False) -> None:
            if not hasattr(self, "save_status_label"):
                return
            self.save_status_label.setText(message)
            self.save_status_label.setStyleSheet("color: #b00020;" if error else "color: #555;")

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
            has_table_selection = bool(self.selected_interview_ids())
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
            self.export_reviews(default_scope="current" if self.current_interview_id else "selected")

        def export_selected_reviews(self, *_args: Any) -> None:
            self.export_reviews(default_scope="selected")

        def export_reviews(self, default_scope: str = "current") -> None:
            if not self.save_current_turn(force=bool(self.review and self.current_turn_id)):
                return
            if default_scope == "selected" and not self.selected_interview_ids():
                default_scope = "current" if self.current_interview_id else "all"
            dialog = ExportDialog(default_scope=default_scope, parent=self)
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
            message = QMessageBox(self)
            message.setIcon(QMessageBox.Icon.Information)
            message.setWindowTitle("Exportacao concluida")
            message.setText(f"{len(exported)} arquivo(s) exportado(s).")
            message.setInformativeText(str(self.context.paths.review_dir / "final"))
            details = [str(path) for path in exported]
            if skipped:
                details.append("")
                details.append("Sem transcricao exportavel:")
                details.extend(skipped)
            message.setDetailedText("\n".join(details))
            message.exec()

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
            folder = self.context.paths.review_dir / "final"
            folder.mkdir(parents=True, exist_ok=True)
            open_folder_in_explorer(folder)

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
            for interview_id in ids:
                self.context = app_service.update_job(
                    self.context,
                    interview_id,
                    {"status": "Na fila", "stage": "aguardando", "progress": 0, "queued_at": datetime.now().isoformat(timespec="seconds"), "last_error": ""},
                )
            for index, interview_id in enumerate(ids, start=1):
                prefix = f"{index}/{len(ids)} {interview_id}"
                steps.extend(
                    [
                        self.job_step(f"{prefix}: preparando audio...", interview_id, "preparar audio", 0, 10, lambda item=interview_id: app_service.prepare_interviews(self.context, ids=[item])),
                        self.job_step(
                            f"{prefix}: transcrevendo fala...",
                            interview_id,
                            "transcrever",
                            10,
                            70,
                            lambda progress, should_cancel, item=interview_id: app_service.transcribe_interviews(
                                self.context,
                                ids=[item],
                                overrides={"diarize": False},
                                progress_callback=progress,
                                should_cancel=should_cancel,
                            ),
                            accepts_progress=True,
                        ),
                        self.job_step(f"{prefix}: identificando falantes...", interview_id, "identificar falantes", 70, 88, lambda item=interview_id: app_service.diarize_interviews(self.context, ids=[item])),
                        self.job_step(f"{prefix}: montando transcricao editavel...", interview_id, "montar transcricao", 88, 96, lambda item=interview_id: app_service.render_interviews(self.context, ids=[item], overrides={"diarization_source": "pyannote_exclusive"})),
                        self.job_step(f"{prefix}: verificando arquivos gerados...", interview_id, "verificar arquivos", 96, 100, lambda item=interview_id: app_service.qc_interviews(self.context, ids=[item])),
                    ]
                )
                weights.extend([10, 60, 18, 8, 4])
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
                    lambda item=interview_id: app_service.diarize_interviews(self.context, ids=[item]),
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
                except Exception:
                    pass
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
                # Force close: terminate the worker thread
                self.worker.cancel_after_step = True
                self.worker.terminate()
                self.worker.wait(3000)
            self.save_current_turn()
            self.player.stop()
            event.accept()


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
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--project", type=Path, default=None, help="Project root directory.")
    args, _remaining = parser.parse_known_args()
    app = QApplication(sys.argv)
    window = ReviewStudioWindow(project_root=args.project)
    window.show()
    if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
        QTimer.singleShot(0, window.show_startup_dialog)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
