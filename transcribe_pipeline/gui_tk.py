from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import app_service


YES = "sim"
NO = "nao"


class TranscriptionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Transcritorio")
        self.geometry("1180x720")
        self.minsize(980, 560)
        self.context = app_service.load_project()
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._build_ui()
        self.refresh_status()
        self.after(250, self._poll_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Projeto local de transcricao", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text=str(Path.cwd()), foreground="#555").pack(side=tk.RIGHT)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(10, 8))
        self._button(actions, "Atualizar", self.refresh_status)
        self._button(actions, "Gerar manifesto", lambda: self.run_job("manifest", self._job_manifest))
        self._button(actions, "Preparar WAV", lambda: self.run_selected_job("prepare-audio", app_service.prepare_interviews))
        self._button(actions, "Transcrever", lambda: self.run_selected_job("transcribe", app_service.transcribe_interviews))
        self._button(actions, "Diarizar", lambda: self.run_selected_job("diarize", app_service.diarize_interviews))
        self._button(actions, "Renderizar", lambda: self.run_selected_job("render", app_service.render_interviews))
        self._button(actions, "QC", self.run_qc_job)
        self._button(actions, "Abrir MD", self.open_markdown)
        self._button(actions, "Criar revisao", self.create_review)
        self._button(actions, "Abrir pasta", self.open_output_folder)

        columns = (
            "id",
            "folder",
            "ext",
            "duration",
            "channels",
            "wav",
            "asr",
            "diar",
            "canonical",
            "review",
            "md",
            "qc",
        )
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="extended")
        headings = {
            "id": "ID",
            "folder": "Pasta",
            "ext": "Fonte",
            "duration": "Duracao",
            "channels": "Canais",
            "wav": "WAV",
            "asr": "ASR",
            "diar": "Diarizacao",
            "canonical": "Canonico",
            "review": "Revisao",
            "md": "MD",
            "qc": "QC",
        }
        widths = {
            "id": 110,
            "folder": 160,
            "ext": 70,
            "duration": 90,
            "channels": 70,
            "wav": 60,
            "asr": 60,
            "diar": 85,
            "canonical": 80,
            "review": 70,
            "md": 60,
            "qc": 220,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.W)

        yscroll = ttk.Scrollbar(root, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        footer = ttk.Frame(self, padding=(0, 8, 0, 0))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(footer, textvariable=self.status_var).pack(side=tk.LEFT)

    def _button(self, parent: ttk.Frame, text: str, command) -> None:
        ttk.Button(parent, text=text, command=command).pack(side=tk.LEFT, padx=(0, 6))

    def selected_ids(self) -> list[str]:
        return [self.tree.item(item, "values")[0] for item in self.tree.selection()]

    def require_selection(self) -> list[str]:
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Selecione entrevistas", "Selecione uma ou mais entrevistas na lista.")
        return ids

    def refresh_status(self) -> None:
        self.context = app_service.load_project(self.context.config_path)
        for item in self.tree.get_children():
            self.tree.delete(item)
        statuses = app_service.list_interviews(self.context)
        for status in statuses:
            diar = status.diarization_exclusive_exists or status.diarization_regular_exists
            self.tree.insert(
                "",
                tk.END,
                values=(
                    status.interview_id,
                    status.person_folder,
                    status.source_ext,
                    format_duration(status.duration_sec),
                    status.source_audio_channels,
                    flag(status.wav_exists),
                    flag(status.asr_exists),
                    flag(diar),
                    flag(status.canonical_exists),
                    flag(status.review_exists),
                    flag(status.markdown_exists),
                    status.qc_notes,
                ),
            )
        self.status_var.set(f"{len(statuses)} entrevistas selecionadas no manifesto.")

    def run_job(self, label: str, target) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Processamento em andamento", "Aguarde o processamento atual terminar.")
            return
        self.status_var.set(f"Executando: {label}...")
        self.worker = threading.Thread(target=self._run_worker, args=(label, target), daemon=True)
        self.worker.start()

    def _run_worker(self, label: str, target) -> None:
        try:
            result = target()
            self.events.put(("ok", f"{label} concluido. Falhas: {getattr(result, 'failures', 0)}"))
        except Exception as exc:  # GUI boundary
            self.events.put(("error", f"{label} falhou: {exc}"))

    def _poll_events(self) -> None:
        try:
            kind, message = self.events.get_nowait()
        except queue.Empty:
            self.after(250, self._poll_events)
            return
        self.status_var.set(message)
        if kind == "error":
            messagebox.showerror("Erro", message)
        self.refresh_status()
        self.after(250, self._poll_events)

    def run_selected_job(self, label: str, service_func) -> None:
        ids = self.require_selection()
        if not ids:
            return
        self.run_job(label, lambda: service_func(self.context, ids=ids))

    def run_qc_job(self) -> None:
        ids = self.selected_ids() or None
        self.run_job("qc", lambda: app_service.qc_interviews(self.context, ids=ids))

    def _job_manifest(self):
        self.context = app_service.refresh_manifest(self.context)
        return app_service.JobResult("manifest", 0)

    def open_markdown(self) -> None:
        ids = self.require_selection()
        if not ids:
            return
        path = self.context.paths.review_dir / "md" / f"{ids[0]}.md"
        open_path_or_warn(path)

    def create_review(self) -> None:
        ids = self.require_selection()
        if not ids:
            return
        try:
            app_service.load_review(self.context, ids[0], create=True)
        except FileNotFoundError as exc:
            messagebox.showerror("Revisao indisponivel", str(exc))
            return
        self.refresh_status()
        open_path_or_warn(self.context.paths.review_dir / "edits" / f"{ids[0]}.review.json")

    def open_output_folder(self) -> None:
        open_path_or_warn(self.context.paths.output_root)


def format_duration(value: str) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    minutes = int(seconds // 60)
    sec = int(round(seconds % 60))
    return f"{minutes}:{sec:02d}"


def flag(value: bool) -> str:
    return YES if value else NO


def open_path_or_warn(path: Path) -> None:
    if not path.exists():
        messagebox.showwarning("Nao encontrado", f"Arquivo ou pasta nao encontrado:\n{path}")
        return
    os.startfile(str(path))  # type: ignore[attr-defined]


def main() -> int:
    app = TranscriptionApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
