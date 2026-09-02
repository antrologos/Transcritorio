from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable
import numpy as np
import os
import threading
import time
import wave as wave_mod

from .config import Paths
from .manifest import selected_rows
from . import model_manager, runtime
from .utils import append_jsonl, now_utc, write_json

ProgressCallback = Callable[[dict[str, Any]], None]


def _load_wav_as_tensor(audio_path: Path):
    """Load a WAV file as a torch tensor dict, bypassing torchcodec.

    Returns {"waveform": (1, T) float32 tensor, "sample_rate": int}.
    This avoids the torchcodec/FFmpeg DLL dependency that causes
    NameError: 'AudioDecoder' on systems without FFmpeg DLLs registered.
    """
    import torch

    with wave_mod.open(str(audio_path), "r") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

    if n_channels > 1:
        samples = samples.reshape(-1, n_channels)[:, 0]

    waveform = torch.from_numpy(samples).unsqueeze(0)  # (1, T)
    return {"waveform": waveform, "sample_rate": sample_rate}


# --- Progresso honesto da diarizacao (2026-09-02) ---------------------------
# Beta tester em CPU: "congelou no 88%". O 88 era o teto do heartbeat
# exponencial calibrado para GPU (tau=120 s) — em CPU o pyannote leva
# 0,40x o tempo do audio (medido: 300 s de audio em 104 s, 8 nucleos
# fisicos / 16 logicos) e a barra ficava parada 20-45 min. O pyannote
# EMITE progresso real por chunk (segmentation) e por batch (embeddings);
# agora ele alimenta a barra, e o heartbeat vira reserva com expectativa
# por device + duracao. Tudo puro e coberto por tests/toy_diarize_progress.

from .capabilities import expected_diarization_seconds  # noqa: E402 - puras, sem torch/Qt


def diarize_hook_percent(step: str, completed: int | None, total: int | None) -> int | None:
    """% interno (0-100) de um arquivo a partir do hook do pyannote (pura).

    Ordem real do pipeline community-1: segmentation (progresso por chunk)
    -> speaker_counting -> embeddings (progresso por batch) -> clustering
    (sem hook) -> discrete_diarization. Sem total/completed -> None (o
    heartbeat cobre).
    """
    if step == "speaker_counting":
        return 48
    if step == "discrete_diarization":
        return 95
    if not total or completed is None:
        return None
    frac = max(0.0, min(1.0, completed / total))
    if step == "segmentation":
        return 2 + int(43 * frac)
    if step == "embeddings":
        return 50 + int(40 * frac)
    return None


def heartbeat_percent(elapsed: float, expected: float, real_inner: int | None, lo: int, hi: int) -> int:
    """% do arquivo para a barra (pura): o REAL do hook vence o creep.

    Creep = 1 - e^(-t/tau) com tau = expected/2 (chega a ~86% quando o
    tempo esperado passa); cap 0,95 do range para nunca fingir "quase
    pronto"; nunca abaixo de lo+1.
    """
    span = max(1, hi - lo - 3)
    tau = max(1.0, float(expected) / 2.0)
    creep = 1.0 - math.exp(-max(0.0, elapsed) / tau)
    real = (real_inner or 0) / 100.0
    frac = min(0.95, max(creep, real))
    return lo + max(1, int(span * frac))


def _wav_seconds(audio_path: Path) -> float:
    """Duracao de um WAV pelo cabecalho (sem carregar o audio)."""
    try:
        with wave_mod.open(str(audio_path), "r") as wf:
            rate = wf.getframerate() or 16000
            return wf.getnframes() / float(rate)
    except Exception:  # noqa: BLE001 - estimativa e opcional
        return 0.0


def _fmt_eta(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "menos de 2 min"
    mins = int(round(seconds / 60))
    if mins < 60:
        return f"~{mins} min"
    horas, resto = divmod(mins, 60)
    return f"~{horas} h {resto:02d} min"


def run_pyannote_diarization(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    failures = 0
    token_env = str(config["model_download_token_env"])
    try:
        model_name = model_manager.validate_local_diarization_model(config.get("diarize_model"))
    except ValueError as exc:
        print(str(exc))
        return len(selected_rows(rows, ids)) or 1
    rows_to_run = selected_rows(rows, ids)

    if dry_run:
        for row in rows_to_run:
            audio_path = diarization_audio_path(paths, row)
            print(f"pyannote {audio_path} --model {model_name} --offline {speaker_config_summary(config)}")
        return 0

    def emit(event: str, progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "progress": progress, "message": message})

    def _ts() -> str:
        return time.strftime("%H:%M:%S")

    emit("diarize_progress", 0, "Carregando modelo de identificacao de falantes...")
    print(f"[{_ts()}] [diarize] Inicio da diarizacao", flush=True)

    runtime.apply_secure_hf_environment(offline=True, token_env=token_env)
    if config.get("pyannote_metrics_enabled") is not None:
        os.environ["PYANNOTE_METRICS_ENABLED"] = str(config["pyannote_metrics_enabled"])

    print(f"[{_ts()}] [diarize] Importando torch/pyannote...", flush=True)
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        print(f"Missing pyannote dependencies: {exc}")
        return len(rows_to_run) or 1

    torch.set_float32_matmul_precision("high")

    effective_device, fell_back = runtime.resolve_device(config.get("asr_device"))
    if fell_back:
        print("[Transcritorio] CUDA indisponivel. Usando CPU para diarizacao.")
    device = torch.device(effective_device)
    print(f"[{_ts()}] [diarize] Device: {effective_device}. Carregando pipeline...", flush=True)
    if effective_device != "cuda":
        # Em CPU a carga do modelo levou 15-70 s nas medicoes — dizer.
        emit("diarize_progress", 2, "Carregando o modelo de identificação de falantes "
                                    "(em CPU pode levar ~1 min)...")
    try:
        checkpoint = model_name if Path(model_name).exists() else model_manager.local_pyannote_checkpoint()
        pipeline = Pipeline.from_pretrained(checkpoint, token=None, cache_dir=str(runtime.model_cache_dir())).to(device)
    except Exception as exc:  # noqa: BLE001 - provide an actionable standalone error.
        print(f"Could not load local pyannote model: {exc}")
        return len(rows_to_run) or 1

    # Apply custom hyperparameters if configured
    custom_params = _custom_pipeline_params(config)
    if custom_params:
        pipeline.instantiate(custom_params)
        print(f"[{_ts()}] [diarize] Hiperparametros customizados: {custom_params}", flush=True)

    # Rede de embeddings 1x por janela (2026-09-02): 94% do tempo em CPU era
    # a ResNet34 rodando 3x por janela; mesma matematica, saida identica
    # (tests/toy_diar_fast). Chave de escape: diarization_fast_embeddings.
    if bool(config.get("diarization_fast_embeddings", True)):
        from .diar_fast import install_fast_embeddings
        if install_fast_embeddings(pipeline):
            print(f"[{_ts()}] [diarize] embeddings: rede 1x por janela (rapido).", flush=True)
        else:
            print(f"[{_ts()}] [diarize] embeddings: caminho original (modelo sem forward_frames).", flush=True)

    try:
        passo = float(config.get("diarization_segmentation_step") or 0.0)
    except (TypeError, ValueError):
        passo = 0.0
    if passo > 0:
        try:
            passo_s = apply_segmentation_step(pipeline, passo)
            print(f"[{_ts()}] [diarize] passo da segmentacao: {passo_s:.1f} s.", flush=True)
        except (AttributeError, ValueError) as exc:  # sem Inference padrao / valor invalido: fica como esta
            print(f"[{_ts()}] [diarize] passo da segmentacao: padrao ({exc}).", flush=True)

    print(f"[{_ts()}] [diarize] Pipeline carregado.", flush=True)
    emit("diarize_progress", 20, "Modelo carregado.")
    total = len(rows_to_run)

    for idx, row in enumerate(rows_to_run):
        if should_cancel is not None and should_cancel():
            failures += total - idx
            break
        interview_id = row["interview_id"]
        audio_path = diarization_audio_path(paths, row)
        if not audio_path.exists():
            failures += 1
            log_job(paths, interview_id, "error", model_name, audio_path, "audio file missing")
            continue

        file_start_pct = 20 + int(70 * idx / max(1, total))
        file_end_pct = 20 + int(70 * (idx + 1) / max(1, total))
        emit("diarize_progress", file_start_pct, f"Processando {interview_id}...")

        try:
            print(f"[{_ts()}] [diarize] Carregando audio {interview_id}...", flush=True)
            audio_tensor = _load_wav_as_tensor(audio_path)
            print(f"[{_ts()}] [diarize] Audio carregado. Rodando pipeline (pode levar alguns minutos)...", flush=True)

            # Progresso real (hook do pyannote) + heartbeat de reserva com
            # expectativa por device e duracao (ver puras no topo).
            heartbeat_stop = threading.Event()
            t0 = time.monotonic()
            audio_seconds = _wav_seconds(audio_path)
            expected = expected_diarization_seconds(
                audio_seconds, effective_device, os.cpu_count() or 1)
            real_inner: dict[str, int] = {"pct": 0}
            pct_lo, pct_hi = file_start_pct, file_end_pct
            cpu_note = " — sem placa de vídeo esta etapa é a mais demorada" if effective_device != "cuda" else ""

            def _mensagem(elapsed: float) -> str:
                mins, secs = divmod(int(elapsed), 60)
                time_str = f"{mins}min {secs:02d}s" if mins else f"{secs}s"
                real = real_inner["pct"]
                if real >= 10:
                    restante = elapsed * (100 - real) / real
                else:
                    restante = expected - elapsed
                return (f"Separando falantes de {interview_id} — {real}% "
                        f"({time_str}, {_fmt_eta(restante)} restantes){cpu_note}")

            def _emit_now() -> None:
                elapsed = time.monotonic() - t0
                pct = heartbeat_percent(elapsed, expected, real_inner["pct"], pct_lo, pct_hi)
                emit("diarize_progress", pct, _mensagem(elapsed))

            def _on_hook_progress(step_name: str, completed: int | None, total: int | None) -> None:
                novo = diarize_hook_percent(step_name, completed, total)
                if novo is not None and novo > real_inner["pct"]:
                    real_inner["pct"] = novo
                    _emit_now()

            def _heartbeat() -> None:
                while not heartbeat_stop.wait(5):
                    _emit_now()
                    elapsed = int(time.monotonic() - t0)
                    print(f"[{_ts()}] [diarize] heartbeat: {elapsed}s, real {real_inner['pct']}%", flush=True)

            heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
            heartbeat_thread.start()
            # Captura opcional de sinais intermediarios (plano 2026-08-25):
            # o coletor so observa o hook; falha nele nunca afeta o pipeline.
            collector = None
            if bool(config.get("diarization_capture_signals", True)):
                try:
                    from .diar_signals import SignalCollector
                    collector = SignalCollector()
                except Exception:  # noqa: BLE001 - sinais sao opcionais
                    collector = None

            def _hook(step_name: str, step_artifact: Any = None, *, file: Any = None,
                      total: int | None = None, completed: int | None = None) -> None:
                # Progresso primeiro (nunca depende do coletor); depois o
                # coletor de sinais, se ativo. Falha em qualquer um nao
                # derruba a diarizacao.
                try:
                    _on_hook_progress(step_name, completed, total)
                except Exception:  # noqa: BLE001
                    pass
                if collector is not None:
                    try:
                        collector.hook(step_name, step_artifact, file=file, total=total, completed=completed)
                    except Exception:  # noqa: BLE001 - sinais sao opcionais
                        pass

            try:
                output = pipeline(audio_tensor, hook=_hook, **speaker_kwargs(config))
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=2)

            elapsed = int(time.monotonic() - t0)
            print(f"[{_ts()}] [diarize] Pipeline concluido em {elapsed}s.", flush=True)
            regular = getattr(output, "speaker_diarization", output)
            exclusive = getattr(output, "exclusive_speaker_diarization", None)
            try:
                _persist_speaker_embeddings(paths, interview_id, output, model_name)
            except Exception as exc:  # noqa: BLE001 - embeddings sao opcionais
                print(f"[{_ts()}] [diarize] embeddings indisponiveis para {interview_id}: {exc}", flush=True)
            if collector is not None:
                try:
                    from .diar_signals import persist_signals
                    persist_signals(paths, interview_id, collector, output, model_name)
                except Exception as exc:  # noqa: BLE001 - sinais sao opcionais
                    print(f"[{_ts()}] [diarize] sinais indisponiveis para {interview_id}: {exc}", flush=True)
            regular = _postprocess_annotation(regular, config)
            if exclusive is not None:
                exclusive = _postprocess_annotation(exclusive, config, preserve_exclusive=True)
            emit("diarize_progress", file_end_pct - 2, f"Gravando resultados de {interview_id}...")
            write_annotation_outputs(paths, interview_id, "regular", regular, model_name, audio_path)
            if exclusive is not None:
                write_annotation_outputs(paths, interview_id, "exclusive", exclusive, model_name, audio_path)
            status = "ok" if exclusive is not None else "ok_no_exclusive"
            log_job(paths, interview_id, status, model_name, audio_path, "")
            print(f"[{_ts()}] [diarize] {interview_id} concluido: {status}", flush=True)
        except Exception as exc:  # noqa: BLE001 - preserve batch progress and log the failed file.
            failures += 1
            print(f"[{_ts()}] [diarize] ERRO em {interview_id}: {exc}", flush=True)
            log_job(paths, interview_id, "error", model_name, audio_path, str(exc)[-2000:])

    emit("diarize_progress", 100, "Identificacao de falantes concluida.")
    print(f"[{_ts()}] [diarize] Diarizacao finalizada. Falhas: {failures}", flush=True)
    return failures


def diarization_audio_path(paths: Paths, row: dict[str, str]) -> Path:
    wav_path = row.get("wav_path", "")
    return paths.project_root / wav_path if wav_path else paths.project_root / row["source_path"]


def _persist_speaker_embeddings(paths: Paths, interview_id: str, output, model_name: str) -> None:
    """Grava os centroides por falante (DiarizeOutput.speaker_embeddings) —
    insumo do reconhecimento local de vozes recorrentes (plano D2.5+X1a).

    Mapeamento confirmado empiricamente (pyannote 4.0): embeddings[s]
    corresponde a labels()[s] da annotation ANTES do pos-processamento (o
    pos-processamento pode remover um falante inteiro e desalinharia os
    indices). Vetores nao-finitos (falante sem fala util) sao descartados.
    """
    from .voice_recognition import write_speaker_embeddings

    embeddings = getattr(output, "speaker_embeddings", None)
    annotation = getattr(output, "speaker_diarization", None)
    if embeddings is None or annotation is None:
        return
    labels = list(annotation.labels())
    by_speaker: dict[str, list[float]] = {}
    for index, label in enumerate(labels):
        if index >= len(embeddings):
            break
        vector = [float(value) for value in embeddings[index]]
        if vector and all(math.isfinite(value) for value in vector):
            by_speaker[str(label)] = vector
    if by_speaker:
        write_speaker_embeddings(paths, interview_id, by_speaker, model_name)


def apply_segmentation_step(pipeline: Any, step: float) -> float:
    """Passo da janela deslizante da segmentacao, como FRACAO da janela
    (0.1 = 1 s a cada 10 s, padrao do pyannote; 0.2 = 2 s). So o construtor
    le `segmentation_step`; a Inference ja criada le `.step` em segundos —
    os dois sao ajustados. A/B 2026-09-02 (sintetico com verdade por
    construcao + 10 entrevistas reais + verificador acustico): 2 s tem a
    mesma qualidade e e 2x mais rapido. Devolve o passo efetivo em segundos.
    """
    if step > 1.0:
        raise ValueError(
            f"diarization_segmentation_step e fracao da janela (0 < x <= 1); recebido {step}")
    inference = pipeline._segmentation
    duration = float(inference.duration)
    if step > 0 and abs(float(pipeline.segmentation_step) - step) > 1e-9:
        pipeline.segmentation_step = step
        inference.step = step * duration
    return float(inference.step)


def _custom_pipeline_params(config: dict) -> dict:
    """Hiperparametros customizados para pipeline.instantiate().

    Cada chave setada vale por si (antes, fa/fb so eram aplicados se o
    threshold tambem estivesse setado — config morto). instantiate() com
    dict parcial preserva os demais parametros do config.yaml do modelo
    (validado empiricamente no community-1 em 2026-08-23).
    """
    params: dict = {}
    clustering: dict = {}
    if config.get("diarization_clustering_threshold") is not None:
        clustering["threshold"] = float(config["diarization_clustering_threshold"])
    if config.get("diarization_fa") is not None:
        clustering["Fa"] = float(config["diarization_fa"])
    if config.get("diarization_fb") is not None:
        clustering["Fb"] = float(config["diarization_fb"])
    if clustering:
        params["clustering"] = clustering
    if config.get("diarization_min_duration_off") is not None:
        params["segmentation"] = {"min_duration_off": float(config["diarization_min_duration_off"])}
    return params


def speaker_kwargs(config: dict) -> dict[str, int]:
    num_speakers = config.get("diarization_num_speakers")
    if num_speakers is not None:
        return {"num_speakers": int(num_speakers)}
    result: dict[str, int] = {}
    if config.get("min_speakers") is not None:
        result["min_speakers"] = int(config["min_speakers"])
    if config.get("max_speakers") is not None:
        result["max_speakers"] = int(config["max_speakers"])
    return result


def speaker_config_summary(config: dict) -> str:
    kwargs = speaker_kwargs(config)
    return " ".join(f"--{key} {value}" for key, value in kwargs.items())


def write_annotation_outputs(paths: Paths, interview_id: str, kind: str, annotation, model_name: str, audio_path: Path) -> None:
    payload = {
        "interview_id": interview_id,
        "kind": kind,
        "diarization_model": model_name,
        "audio_path": str(audio_path),
        "created_at": now_utc(),
        "segments": annotation_to_segments(annotation),
    }
    write_json(paths.diarization_dir / "json" / f"{interview_id}.{kind}.json", payload)
    with (paths.diarization_dir / "rttm" / f"{interview_id}.{kind}.rttm").open("w", encoding="utf-8") as handle:
        annotation.write_rttm(handle)


def _postprocess_annotation(annotation, config: dict, preserve_exclusive: bool = False):
    """Remove micro-segmentos e funde turnos proximos do mesmo falante.

    preserve_exclusive: support(collar) funde POR ROTULO e, no padrao A-B-A
    com gap < collar, estica A por cima do aparte curto de B — reintroduzindo
    overlap na annotation exclusiva, cuja invariante e nao ter overlap
    (finding C59). Nesse modo a fusao so ocorre entre segmentos consecutivos
    na ordem global, nunca por cima da fala de outro falante.
    """
    from pyannote.core import Annotation

    min_seg = float(config.get("diarization_min_segment") or 0.0)
    collar = float(config.get("diarization_collar") or 0.0)

    if min_seg > 0:
        cleaned = Annotation(uri=annotation.uri)
        for segment, track, speaker in annotation.itertracks(yield_label=True):
            if segment.duration >= min_seg:
                cleaned[segment, track] = speaker
        annotation = cleaned

    if collar > 0:
        if preserve_exclusive:
            annotation = _merge_collar_preserving_exclusivity(annotation, collar)
        else:
            annotation = annotation.support(collar=collar)

    return annotation


def _merge_collar_preserving_exclusivity(annotation, collar: float):
    """Funde segmentos consecutivos (ordem global) do mesmo falante com
    gap < collar — semantica estrita identica ao support(), exceto que um
    segmento de outro falante entre os dois sempre impede a fusao."""
    from pyannote.core import Annotation, Segment

    entries = sorted(
        ((segment, speaker) for segment, _track, speaker in annotation.itertracks(yield_label=True)),
        key=lambda item: (item[0].start, item[0].end),
    )
    merged: list[tuple[Segment, str]] = []
    for segment, speaker in entries:
        if merged:
            prev_segment, prev_speaker = merged[-1]
            if prev_speaker == speaker and (segment.start - prev_segment.end) < collar:
                merged[-1] = (Segment(prev_segment.start, max(prev_segment.end, segment.end)), speaker)
                continue
        merged.append((segment, speaker))
    result = Annotation(uri=annotation.uri)
    for index, (segment, speaker) in enumerate(merged):
        result[segment, index] = speaker
    return result


def annotation_to_segments(annotation) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for segment, _track, speaker in annotation.itertracks(yield_label=True):
        segments.append({"start": float(segment.start), "end": float(segment.end), "speaker": str(speaker)})
    return segments


def log_job(paths: Paths, interview_id: str, status: str, model_name: str, audio_path: Path, message: str) -> None:
    append_jsonl(
        paths.manifest_dir / "jobs.jsonl",
        {
            "interview_id": interview_id,
            "stage": "diarize",
            "status": status,
            "started_at": now_utc(),
            "model": model_name,
            "audio_path": str(audio_path),
            "message": message,
        },
    )
