from __future__ import annotations

import os
import re
import time
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from . import runtime
from . import model_manager
from . import mlx_whisper_runner
from .model_manager import validate_local_diarization_model
from .utils import append_jsonl, now_utc, run_command_stream


ProgressCallback = Callable[[dict[str, Any]], None]


def resolve_align_action(config: dict, cache_dir=None) -> tuple[str, str, str]:
    """Decisao de alinhamento ANTES de transcrever (etapa 4). Testavel.

    Retorna (acao, valor, motivo):
    - ("explicit", repo, "")  : asr_align_model definido (rota expert da CLI)
    - ("model", repo, "")     : pacote do idioma em cache -> --align_model
    - ("no_align", "", motivo): sem tempos por palavra, com o porque

    Regras: Automatico (idioma None) NUNCA alinha — o WhisperX sem
    --language alinharia com INGLES e baixaria pesos da pytorch.org em
    runtime; e nos nunca deixamos o WhisperX baixar alinhador (rotas
    nao-pinadas). Idioma sem pacote transcreve normalmente, so sem
    tempos por palavra.
    """
    if config.get("asr_align_model"):
        return ("explicit", str(config["asr_align_model"]), "")
    language = config.get("asr_language")
    lang = model_manager.normalize_language(language)
    if not lang:
        return ("no_align", "",
                "idioma Automático: a detecção não permite alinhador confiável")
    if model_manager.align_language_supported(lang):
        asset = model_manager.align_asset_for(lang)
        try:
            path = model_manager.cached_snapshot_path(
                asset.repo_id, cache_dir, revision=asset.revision)
            if path and model_manager._snapshot_has_weights(path):
                return ("model", asset.repo_id, "")
        except Exception:  # noqa: BLE001 - cache ilegivel = tratar como ausente
            pass
        # Pacote dedicado ausente: o coringa MMS (se instalado) cobre.
        if model_manager.mms_align_cached(cache_dir):
            return ("model", model_manager.MMS_ALIGN_ASSET.repo_id, "")
        return ("no_align", "", f"pacote de alinhamento de '{lang}' não instalado")
    # Idioma sem pacote dedicado (ex.: suaili): coringa MMS quando
    # instalado; senao transcreve sem tempos, apontando a opcao.
    if model_manager.mms_align_cached(cache_dir):
        return ("model", model_manager.MMS_ALIGN_ASSET.repo_id, "")
    return ("no_align", "",
            f"sem pacote de alinhamento para o idioma '{lang}' — o pacote "
            "multilíngue (MMS) cobre este e outros 1.100 idiomas")


def run_whisperx(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    # Motores nao-Whisper (E4-4): variantes com "engine" proprio desviam
    # antes de qualquer heuristica de dispositivo — a escolha explicita
    # do motor vence o fast-path de plataforma.
    _spec = model_manager.ASR_VARIANTS.get(str(config.get("asr_model") or ""))
    if _spec and _spec.get("engine") == "parakeet_onnx":
        from . import parakeet_runner
        return parakeet_runner.run_parakeet(
            rows, config, paths,
            ids=ids, dry_run=dry_run,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    # Apple Silicon fast path: when MPS is detected and mlx-whisper is
    # installed, route transcription through the MLX runner. faster-whisper
    # (used by the whisperx CLI) does not support Metal; without this branch
    # we would fall back to CPU and lose the ~3-5x speedup available on M-series.
    wanted_device = (config.get("asr_device") or "").lower()
    mlx_opt_in = bool(config.get("asr_use_mlx_on_mps", True))
    if mlx_opt_in and wanted_device != "cpu":
        if runtime.detect_device() == "mps" and mlx_whisper_runner.is_available():
            return mlx_whisper_runner.run_mlx_whisper(
                rows, config, paths,
                ids=ids, dry_run=dry_run,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )

    failures = 0
    token_env = str(config["model_download_token_env"])
    cache_only = bool(config.get("asr_model_cache_only", True))
    model_cache_dir = str(config.get("model_cache_dir") or runtime.model_cache_dir())
    runtime.apply_secure_hf_environment(offline=cache_only, token_env=token_env)
    output_dir = asr_output_dir(paths, config)
    output_dir.mkdir(parents=True, exist_ok=True)

    for row in selected_rows(rows, ids):
        if should_cancel is not None and should_cancel():
            failures += 1
            break
        wav = paths.project_root / row["wav_path"]
        # Tri-state "auto" (2026-08-31): a decisao de separar falantes e
        # resolvida no momento do job, nunca lida como booleano cru.
        if model_manager.diarize_effective(config)[0]:
            validate_local_diarization_model(config.get("diarize_model"))
        device, fell_back = runtime.resolve_device(config.get("asr_device"))
        if fell_back:
            detected = runtime.detect_device()
            if detected == "mps":
                # mlx-whisper not installed; we could not take the Metal fast path.
                print(f"[Transcritorio] Apple Silicon (MPS) detectado mas mlx-whisper nao esta instalado. Transcrevendo {row['interview_id']} em CPU (~3x tempo real). Instale 'mlx-whisper' para usar Metal.")
            else:
                print(f"[Transcritorio] CUDA indisponivel. Usando CPU para transcrever {row['interview_id']}.")
        compute_type, batch_size = runtime.resolve_compute_settings(
            device, config.get("asr_compute_type"), config.get("asr_batch_size")
        )
        configured_ct = str(config.get("asr_compute_type") or "auto").strip().lower()
        if device == "cpu" and configured_ct not in ("auto", "", compute_type):
            # Coercao de seguranca: float16 em CPU viraria float32 no CT2
            # (~2x RAM). Informar para o usuario entender a troca.
            print(f"[Transcritorio] Precisao '{configured_ct}' nao e adequada para CPU; usando '{compute_type}'.")
        effective_model = model_manager.resolve_asr_model(str(config["asr_model"]))
        # Pass the full repo_id (not the shortcut) so faster_whisper looks in
        # the cache dir we actually downloaded to. faster_whisper hardcodes
        # "large-v3-turbo" -> "mobiuslabsgmbh/..." but we use "dropbox-dash/...".
        effective_repo = model_manager.resolve_asr_repo(effective_model)
        command = [
            runtime.resolve_executable("whisperx"),
            str(wav),
            "--model",
            effective_repo,
            "--model_dir",
            model_cache_dir,
            "--device",
            device,
            "--compute_type",
            compute_type,
            "--batch_size",
            str(batch_size),
            "--output_format",
            "all",
            "--output_dir",
            str(output_dir),
        ]
        if cache_only:
            command.extend(["--model_cache_only", "True"])
        if config.get("asr_language"):
            command.extend(["--language", str(config["asr_language"])])
        add_optional_arg(command, "--beam_size", config.get("asr_beam_size"))
        add_optional_arg(command, "--initial_prompt", config.get("asr_initial_prompt"))
        add_optional_arg(command, "--hotwords", config.get("asr_hotwords"))
        add_optional_arg(command, "--vad_method", config.get("asr_vad_method"))
        add_optional_arg(command, "--vad_onset", config.get("asr_vad_onset"))
        add_optional_arg(command, "--vad_offset", config.get("asr_vad_offset"))
        add_optional_arg(command, "--chunk_size", config.get("asr_chunk_size"))
        # Etapa 4: a decisao de alinhamento acontece ANTES de transcrever —
        # nunca deixamos o WhisperX escolher (ele alinharia "auto" com
        # ingles, baixaria pesos nao-pinados da pytorch.org em runtime e
        # estouraria ValueError DEPOIS da transcricao em idiomas sem
        # alinhador). --no_align degrada com graca: tempos por bloco.
        align_acao, align_valor, align_motivo = resolve_align_action(config)
        if align_acao in ("explicit", "model"):
            command.extend(["--align_model", align_valor])
        else:
            command.append("--no_align")
            print(f"[Transcritorio] Transcrevendo {row['interview_id']} sem "
                  f"tempos por palavra ({align_motivo}).")
        if model_manager.diarize_effective(config)[0]:
            command.append("--diarize")
            add_optional_arg(command, "--min_speakers", config.get("min_speakers"))
            add_optional_arg(command, "--max_speakers", config.get("max_speakers"))
            add_optional_arg(command, "--diarize_model", config.get("diarize_model"))

        redacted = list(command)
        if dry_run:
            print(" ".join(redacted))
            continue

        if config.get("pyannote_metrics_enabled") is not None:
            os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

        tracker = WhisperXProgressTracker(row["interview_id"], progress_callback)
        tracker.emit({"event": "asr_progress", "progress": 1, "message": "Carregando modelo de IA na GPU..."})
        result = run_command_stream(command, cwd=paths.project_root, on_output=tracker.feed, should_cancel=should_cancel)
        cancelled = should_cancel is not None and should_cancel()
        tracker.emit({"event": "asr_done", "progress": 100 if result.returncode == 0 else tracker.last_percent, "message": "WhisperX finalizado."})
        status = "ok" if result.returncode == 0 else "cancelled" if cancelled else "error"
        failures += 0 if result.returncode == 0 else 1
        append_jsonl(
            paths.manifest_dir / "jobs.jsonl",
            {
                "interview_id": row["interview_id"],
                "stage": "transcribe",
                "status": status,
                "started_at": now_utc(),
                "model": config["asr_model"],
                # "backend" distinguishes whisperx CLI runs from mlx-whisper
                # runs on Apple Silicon. Both write to the same jobs.jsonl.
                "backend": "whisperx",
                # Valores EFETIVOS (pos resolve_compute_settings), nao os
                # configurados — o log deve registrar o que realmente rodou.
                "compute_type": compute_type,
                "batch_size": batch_size,
                # Etapa 4: registrar o idioma e a decisao de alinhamento
                # EFETIVOS — o JSON do WhisperX nao preserva o idioma
                # detectado, entao este log e a unica trilha auditavel.
                "language": model_manager.normalize_language(config.get("asr_language")) or "auto",
                "align": (align_valor if align_acao in ("explicit", "model")
                          else f"no_align: {align_motivo}"),
                "variant": config.get("asr_variant") or "",
                "output_dir": str(output_dir),
                "command": redacted,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
        )
    return failures


class WhisperXProgressTracker:
    def __init__(self, interview_id: str, callback: ProgressCallback | None) -> None:
        self.interview_id = interview_id
        self.callback = callback
        self.tail = ""
        self.last_percent = 1
        self.last_message_at = 0.0
        self._creep_start = time.monotonic()

    def feed(self, chunk: str) -> None:
        self.tail = (self.tail + chunk)[-4000:]
        percent = parse_progress_percent(self.tail)
        if percent is not None and percent != self.last_percent:
            self.last_percent = max(percent, self.last_percent)
            self._creep_start = time.monotonic()
            self.emit({"event": "asr_progress", "progress": self.last_percent, "message": self.current_message()})
            return

        now = time.monotonic()
        if now - self.last_message_at >= 2.0:
            # Creep: advance 1% per 4s when no tqdm progress is detected.
            # Covers model loading (~30s), VAD (~10s), and alignment (~60s)
            # where WhisperX emits no percentage. Cap at 90 to leave room
            # for real tqdm values (which arrive at 90-100%).
            creep_elapsed = now - self._creep_start
            if self.last_percent < 90 and creep_elapsed >= 2.0:
                self.last_percent = min(90, self.last_percent + 1)
                self._creep_start = now
            message = self.current_message() or "Carregando modelo de IA na GPU..."
            self.last_message_at = now
            self.emit({"event": "asr_progress", "progress": self.last_percent, "message": message})

    def current_message(self) -> str:
        text = self.tail.replace("\r", "\n")
        lines = [clean_output_line(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        return lines[-1] if lines else ""

    def emit(self, payload: dict[str, Any]) -> None:
        if self.callback is None:
            return
        payload = dict(payload)
        payload.setdefault("file_id", self.interview_id)
        self.callback(payload)


def parse_progress_percent(text: str) -> int | None:
    matches = re.findall(r"(?<!\d)(\d{1,3})\s*%", text)
    if not matches:
        return None
    value = max(0, min(100, int(matches[-1])))
    return value


def clean_output_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def add_optional_arg(command: list[str], flag: str, value) -> None:
    if value is None or value == "":
        return
    command.extend([flag, str(value)])


def asr_output_dir(paths: Paths, config: dict):
    variant = config.get("asr_variant")
    if not variant:
        return paths.asr_dir
    safe_variant = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(variant)).strip("._")
    if not safe_variant:
        raise ValueError("Invalid empty ASR variant name after sanitization.")
    return paths.asr_variants_dir / safe_variant
