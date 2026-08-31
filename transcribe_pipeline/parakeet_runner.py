"""Runner para o motor experimental Parakeet pt-BR (TAGARELA, ONNX).

Motor alternativo ao WhisperX para portugues: alefiury/parakeet-tdt-0.6b-
v3-ptBR-TAGARELA-onnx via onnx-asr. Vantagens: pontuacao e capitalizacao
nativas + tempos por TOKEN nativos do decoder TDT (nao precisa de
alinhador wav2vec2). Roda em CPU a ~13x tempo real (onnxruntime CPU EP).

Restricoes do export ONNX (medidas em 2026-08-30):
- a atencao relativa do encoder tem tabela fixa de 2501 frames (~200 s);
  audio mais longo estoura em runtime. Por isso o runner fatia o audio
  em janelas de 170 s com 5 s de sobreposicao e funde as palavras
  cortando no PONTO MEDIO do overlap — sempre em fronteira de palavra,
  nunca de token (validado em amostra real de 10 min: 0 violacoes de
  monotonicidade, texto coerente nas fronteiras).
- o modelo so transcreve portugues: idioma configurado != pt bloqueia o
  job com mensagem clara (guard tambem existe na GUI, antes do lote).

A saida imita o whisperx CLI (mesmo contrato do mlx_whisper_runner):
{interview_id}.json com segments/words + SRT/VTT/TXT/TSV no
asr_output_dir; diarizacao/render/review seguem inalterados.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .config import Paths
from .manifest import selected_rows
from . import model_manager, onnx_env, runtime
from .model_manager import validate_local_diarization_model
from .utils import (append_jsonl, now_utc, parse_progress_json_line,
                    run_command_stream, sanitize_message,
                    secure_subprocess_env, write_json)

ProgressCallback = Callable[[dict[str, Any]], None]

# Mesmo padrao de sanitizacao do mlx_whisper_runner/asr_output_dir.
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")

SAMPLE_RATE = 16_000
# Janela < 200 s (limite do export); overlap para o merge ter contexto.
WINDOW_S = 170.0
OVERLAP_S = 5.0
# Janela do modo GPU: 96 s mediu 62x tempo real com 4,7 GB de VRAM na
# RTX 4060 (170 s chega a 7,9 GB — perigoso em placas de 8 GB, e mais
# LENTO por janela: a atencao e quadratica). CPU segue com 170 s
# (RAM barata, menos emendas).
GPU_WINDOW_S = 96.0
# Exit code do worker quando o CUDA EP nao esta utilizavel (fallback
# com mensagem especifica, distinto de falha geral).
WORKER_EXIT_NO_CUDA = 42

# Flag de MODULO, nao de chamada: a GUI transcreve um lote chamando
# run_whisperx uma vez POR ARQUIVO, entao um flag local repetiria ~10 s
# de init CUDA fadado a falhar a cada entrevista. Reseta ao reiniciar o
# app.
_GPU_FAILED_THIS_SESSION = False


class ParakeetGpuError(RuntimeError):
    """Falha do worker GPU — recuperavel via fallback CPU."""


class _WorkerCancelled(Exception):
    """Cancelamento do usuario durante o worker GPU (nao e falha)."""
# Quebra de segmento: pontuacao de fim de frase, pausa longa ou duracao.
_SENTENCE_END = (".", "?", "!", "…")
_MAX_GAP_S = 1.0
_MAX_SEGMENT_S = 30.0
# Duracao estimada da ultima palavra de cada janela (o TDT so da o
# instante de EMISSAO de cada token; o fim real e o inicio da proxima).
_LAST_WORD_S = 0.30


def is_available() -> bool:
    """True se onnx-asr importa no interpretador atual."""
    try:
        import onnx_asr  # noqa: F401
    except Exception:
        return False
    return True


def tokens_to_words(
    tokens: list[str],
    timestamps: list[float],
    logprobs: list[float] | None,
) -> list[dict[str, Any]]:
    """Agrega subword-tokens em palavras com start/end/score.

    Convencao SentencePiece do vocab TAGARELA: token iniciado por espaco
    abre palavra nova; pontuacao (sem espaco) anexa a palavra anterior.
    end = start da palavra seguinte (na mesma janela); a ultima ganha
    +_LAST_WORD_S. score = exp(media dos logprobs dos tokens).
    """
    acc: list[dict[str, Any]] = []
    cur_text = ""
    cur_start = 0.0
    cur_lps: list[float] = []
    for i, tok in enumerate(tokens):
        ts = float(timestamps[i])
        lp = float(logprobs[i]) if logprobs else 0.0
        if tok.startswith(" ") and cur_text:
            acc.append({"word": cur_text, "start": cur_start, "lps": cur_lps})
            cur_text = ""
            cur_lps = []
        if not cur_text:
            cur_start = ts
            cur_text = tok.lstrip(" ")
        else:
            cur_text += tok
        cur_lps.append(lp)
    if cur_text:
        acc.append({"word": cur_text, "start": cur_start, "lps": cur_lps})

    words: list[dict[str, Any]] = []
    for i, w in enumerate(acc):
        if not w["word"].strip():
            continue
        end = acc[i + 1]["start"] if i + 1 < len(acc) else w["start"] + _LAST_WORD_S
        if end < w["start"]:
            end = w["start"]
        lps = w["lps"]
        score = math.exp(sum(lps) / len(lps)) if lps else 1.0
        words.append({
            "word": w["word"],
            "start": round(float(w["start"]), 3),
            "end": round(float(end), 3),
            "score": round(min(max(score, 0.0), 1.0), 3),
        })
    return words


def merge_windows(
    per_window: list[list[dict[str, Any]]],
    offsets: list[float],
    overlap_s: float = OVERLAP_S,
) -> list[dict[str, Any]]:
    """Funde palavras das janelas cortando no meio do overlap.

    Cada janela contribui apenas as palavras cujo START global cai no
    seu intervalo exclusivo [corte anterior, proximo corte) — assim uma
    palavra nunca e cortada ao meio nem duplicada.
    """
    merged: list[dict[str, Any]] = []
    for i, words in enumerate(per_window):
        lo = 0.0 if i == 0 else offsets[i] + overlap_s / 2
        hi = offsets[i + 1] + overlap_s / 2 if i + 1 < len(per_window) else math.inf
        for w in words:
            gs = w["start"] + offsets[i]
            if lo <= gs < hi:
                merged.append({**w, "start": round(gs, 3),
                               "end": round(w["end"] + offsets[i], 3)})
    return merged


def words_to_segments(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converte a lista global de palavras em segmentos estilo whisperx.

    Quebra apos pontuacao de fim de frase, em pausa >= _MAX_GAP_S ou
    quando o segmento passa de _MAX_SEGMENT_S (fallback para fala
    continua sem pontuacao).
    """
    segments: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if not cur:
            return
        segments.append({
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
            "text": " ".join(w["word"] for w in cur),
            "words": list(cur),
        })
        cur.clear()

    for i, w in enumerate(words):
        cur.append(w)
        ends_sentence = w["word"].endswith(_SENTENCE_END)
        long_pause = (i + 1 < len(words)
                      and words[i + 1]["start"] - w["end"] >= _MAX_GAP_S)
        too_long = w["end"] - cur[0]["start"] >= _MAX_SEGMENT_S
        if ends_sentence or long_pause or too_long:
            flush()
    flush()
    return segments


def gpu_execution_plan(
    resolved_device: str,
    *,
    platform: str,
    frozen: bool,
    env_ready: bool,
    cuda_ok: bool,
    gpu_failed_before: bool,
) -> tuple[str, str]:
    """Decide ("gpu"|"cpu", motivo) — pura, testavel.

    resolved_device vem de runtime.resolve_device (ja aplicou "auto").
    Windows nao-frozen apenas: o CUDA EP do onnxruntime-gpu 1.22 usa as
    DLLs do torch/lib (layout do pip Windows); macOS nao tem CUDA;
    Linux tem outro layout de DLLs (follow-up registrado). No frozen,
    sys.executable e a GUI — relancaria o app.
    """
    if resolved_device != "cuda":
        return ("cpu", "")
    if platform != "win32":
        return ("cpu", "aceleração GPU do Parakeet indisponível neste sistema")
    if frozen:
        return ("cpu", "a instalação standalone não suporta a aceleração do Parakeet")
    if not cuda_ok:
        return ("cpu", "componentes CUDA ausentes — instale o Transcritório "
                       "com aceleração NVIDIA")
    if not env_ready:
        return ("cpu", "aceleração GPU do Parakeet não instalada — disponível "
                       "em Gerenciar modelos")
    if gpu_failed_before:
        return ("cpu", "a GPU falhou nesta sessão; usando o processador")
    return ("gpu", "")


def worker_command_env(
    python_exe: str,
    worker_path: Path,
    wav: Path,
    model_dir: Path,
    out_path: Path,
    *,
    window_s: float,
    overlap_s: float,
    torch_lib: Path,
    onnx_dir: Path,
    base_env: dict[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Comando + ambiente do worker GPU (pura, testavel).

    PYTHONPATH prefixado pelo dir onnx-gpu: o subprocesso resolve
    `onnxruntime` de la (sombreando o CPU do app) e todo o resto do
    site-packages normal. PATH prefixado pelo torch/lib para as DLLs
    CUDA. PYTHONIOENCODING evita mojibake do @PROGRESS no pipe.
    """
    command = [
        python_exe, "-B", str(worker_path),
        "--wav", str(wav),
        "--model-dir", str(model_dir),
        "--out", str(out_path),
        "--window-s", str(window_s),
        "--overlap-s", str(overlap_s),
        "--torch-lib", str(torch_lib),
        "--onnx-dir", str(onnx_dir),
    ]
    env = dict(base_env)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(onnx_dir) + (os.pathsep + previous if previous else "")
    env["PATH"] = str(torch_lib) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return command, env


def _recognize_via_worker(
    wav: Path,
    snap: Path,
    interview_id: str,
    progress_callback: ProgressCallback | None,
    should_cancel: Callable[[], bool] | None,
) -> tuple[list[list[dict[str, Any]]], list[float]]:
    """Roda o worker GPU e devolve (palavras_por_janela, offsets).

    Levanta _WorkerCancelled quando o usuario cancelou no meio (nao e
    falha — o chamador NAO deve cair para CPU) e ParakeetGpuError para
    qualquer problema do worker (o chamador faz fallback CPU).
    """
    torch_lib = onnx_env.torch_lib_dir()
    if torch_lib is None:
        raise ParakeetGpuError("torch/lib nao encontrado no ambiente do app")
    tmp_dir = runtime.app_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"parakeet_{uuid4().hex[:12]}.json"
    worker_path = Path(__file__).with_name("parakeet_worker.py")
    command, env = worker_command_env(
        sys.executable, worker_path, wav, snap, out_path,
        window_s=GPU_WINDOW_S, overlap_s=OVERLAP_S,
        torch_lib=torch_lib, onnx_dir=onnx_env.onnx_env_dir(),
        base_env=secure_subprocess_env())

    def on_output(line: str) -> None:
        data = parse_progress_json_line(line)
        if data:
            _emit(progress_callback, interview_id, data)

    try:
        completed = run_command_stream(
            command, on_output=on_output, should_cancel=should_cancel, env=env)
        # Cancelamento PRIMEIRO: terminate() no Windows devolve rc 1 —
        # classificar por returncode confundiria cancelar com falha e
        # dispararia uma re-transcricao CPU inteira.
        if should_cancel is not None and should_cancel():
            raise _WorkerCancelled()
        if completed.returncode == WORKER_EXIT_NO_CUDA:
            tail = (completed.stdout or "").strip().splitlines()
            raise ParakeetGpuError(
                "CUDA indisponivel no pacote de aceleracao"
                + (f" ({tail[-1][:160]})" if tail else ""))
        if completed.returncode != 0 or not out_path.exists():
            tail = sanitize_message((completed.stdout or "").strip()[-240:])
            raise ParakeetGpuError(
                tail or f"worker saiu com codigo {completed.returncode}")
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ParakeetGpuError(f"saida do worker ilegivel: {exc}") from exc
        windows = data.get("windows") if isinstance(data, dict) else None
        if not windows:
            raise ParakeetGpuError("worker nao devolveu janelas")
        per_window = [
            tokens_to_words(list(w.get("tokens") or []),
                            list(w.get("timestamps") or []),
                            w.get("logprobs"))
            for w in windows
        ]
        offsets = [float(w.get("offset", 0.0)) for w in windows]
        return per_window, offsets
    finally:
        out_path.unlink(missing_ok=True)


def language_supported(config: dict) -> tuple[bool, str]:
    """(ok, idioma_normalizado): o motor so aceita portugues explicito."""
    raw = config.get("asr_language")
    if raw is None or not str(raw).strip():
        return (False, "automático")
    code = model_manager.normalize_language(str(raw))
    return (code == "pt", code)


def run_parakeet(
    rows: list[dict[str, str]],
    config: dict,
    paths: Paths,
    ids: list[str] | None = None,
    dry_run: bool = False,
    progress_callback: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Transcreve as linhas com o TAGARELA ONNX. Retorna nro de falhas."""
    global _GPU_FAILED_THIS_SESSION
    variant = str(config.get("asr_model") or "")
    spec = model_manager.ASR_VARIANTS.get(variant) or {}
    repo = str(spec.get("repo") or "")
    revision = str(spec.get("revision") or "")

    ok_lang, lang_label = language_supported(config)
    if not ok_lang:
        # Guard de motor: transcrever outro idioma com um modelo pt-only
        # produziria texto errado em silencio. A GUI ja avisa antes do
        # lote; aqui e a defesa para CLI/config manual.
        blocked = list(selected_rows(rows, ids))
        for row in blocked:
            interview_id = str(row.get("interview_id", "") or "<sem_id>")
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": ("O motor Parakeet pt-BR só transcreve português. "
                               f"Idioma configurado: {lang_label}. Ajuste o idioma "
                               "para Português ou troque o motor.")})
            _log_job(paths, interview_id, repo, config, "error",
                     error=f"parakeet exige idioma pt (configurado: {lang_label})")
        return len(blocked)

    if not is_available():
        raise RuntimeError(
            "onnx-asr nao esta instalado neste ambiente; o motor Parakeet "
            "pt-BR nao pode rodar. Reinstale o Transcritorio ou use o Whisper."
        )

    token_env = str(config.get("model_download_token_env")
                    or "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN")
    cache_only = bool(config.get("asr_model_cache_only", True))
    runtime.apply_secure_hf_environment(offline=cache_only, token_env=token_env)

    cache_dir = Path(str(config.get("model_cache_dir") or runtime.model_cache_dir()))
    snap = model_manager.cached_snapshot_path(repo, cache_dir, revision=revision)
    if snap is None or not model_manager._snapshot_has_weights(snap):
        raise RuntimeError(
            "O modelo Parakeet pt-BR (TAGARELA) não está instalado. Baixe-o "
            "em Gerenciar modelos antes de transcrever com este motor."
        )

    # Tri-state "auto" (2026-08-31): resolver, nunca booleano cru.
    if model_manager.diarize_effective(config)[0]:
        validate_local_diarization_model(config.get("diarize_model"))

    from .whisperx_runner import asr_output_dir
    output_dir = asr_output_dir(paths, config)
    output_dir.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import onnx_asr

    resolved_device, _ = runtime.resolve_device(config.get("asr_device"))
    plan, plan_motivo = gpu_execution_plan(
        resolved_device,
        platform=sys.platform,
        frozen=bool(getattr(sys, "frozen", False)),
        env_ready=onnx_env.onnx_env_ready(),
        cuda_ok=runtime.cuda_libs_present(),
        gpu_failed_before=_GPU_FAILED_THIS_SESSION,
    )
    if resolved_device == "cuda" and plan == "cpu" and plan_motivo:
        # Aviso unico por chamada; a oferta visivel de instalar a
        # aceleracao mora na GUI (antes do job).
        print(f"[Transcritorio] Parakeet: {plan_motivo} — transcrevendo no processador.")

    model = None  # carregado no primeiro uso (3-4 s; uma vez por lote)
    failures = 0
    for row in selected_rows(rows, ids):
        if should_cancel is not None and should_cancel():
            failures += 1
            break

        raw_id = str(row.get("interview_id", "") or "")
        safe_id = _SAFE_ID_RE.sub("_", raw_id).strip("._")
        if not safe_id:
            _emit(progress_callback, raw_id or "<sem_id>",
                  {"event": "asr_error", "progress": 0,
                   "message": "Linha sem interview_id valido; pulando."})
            _log_job(paths, raw_id or "<invalid>", repo, config, "error",
                     error="interview_id vazio ou invalido apos sanitizacao")
            failures += 1
            continue
        interview_id = safe_id

        wav = paths.project_root / row["wav_path"]
        if not wav.exists():
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"WAV ausente: {wav.name}"})
            _log_job(paths, interview_id, repo, config, "error",
                     error=f"WAV nao encontrado: {wav}")
            failures += 1
            continue

        if dry_run:
            print(f"[parakeet] would transcribe {wav} with {repo}")
            continue

        started = time.monotonic()
        device_used = "cuda" if plan == "gpu" else "cpu"
        try:
            offsets: list[float] = []
            per_window: list[list[dict[str, Any]]] = []
            overlap_used = OVERLAP_S

            if plan == "gpu":
                try:
                    per_window, offsets = _recognize_via_worker(
                        wav, snap, interview_id, progress_callback, should_cancel)
                except _WorkerCancelled:
                    failures += 1
                    break
                except ParakeetGpuError as exc:
                    _GPU_FAILED_THIS_SESSION = True
                    plan = "cpu"
                    device_used = "cpu"
                    _emit(progress_callback, interview_id,
                          {"event": "asr_progress", "progress": 1,
                           "message": ("A GPU falhou; continuando no processador. "
                                       f"({sanitize_message(str(exc))[:160]})")})

            if plan == "cpu":
                if model is None:
                    _emit(progress_callback, interview_id,
                          {"event": "asr_progress", "progress": 1,
                           "message": "Carregando o modelo Parakeet pt-BR..."})
                    model = onnx_asr.load_model(
                        "nemo-parakeet-tdt-0.6b-v3", str(snap)).with_timestamps()

                audio = _read_wav_mono16k(wav, np)
                total_s = len(audio) / SAMPLE_RATE
                step = WINDOW_S - OVERLAP_S
                n_win = max(1, math.ceil(max(total_s - OVERLAP_S, 1e-9) / step))

                offsets = []
                per_window = []
                cancelled = False
                for i in range(n_win):
                    if should_cancel is not None and should_cancel():
                        cancelled = True
                        break
                    off = i * step
                    chunk = audio[int(off * SAMPLE_RATE):
                                  int((off + WINDOW_S) * SAMPLE_RATE)]
                    result = model.recognize(chunk, sample_rate=SAMPLE_RATE)
                    offsets.append(off)
                    per_window.append(tokens_to_words(
                        list(result.tokens), list(result.timestamps),
                        list(result.logprobs) if result.logprobs is not None else None))
                    pct = max(1, min(98, int(round((i + 1) / n_win * 98))))
                    _emit(progress_callback, interview_id,
                          {"event": "asr_progress", "progress": pct,
                           "message": f"Transcrevendo com Parakeet ({pct}%)..."})
                if cancelled:
                    failures += 1
                    break

            words = merge_windows(per_window, offsets, overlap_s=overlap_used)
            segments = words_to_segments(words)
        except Exception as exc:
            detail = sanitize_message(str(exc).strip() or type(exc).__name__)
            if len(detail) > 240:
                detail = detail[:240].rstrip() + "..."
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"Parakeet falhou ({type(exc).__name__}): {detail}"})
            _log_job(paths, interview_id, repo, config, "error",
                     error=sanitize_message(str(exc)), device=device_used)
            failures += 1
            continue

        elapsed = time.monotonic() - started
        payload = {
            "language": "pt",
            "segments": segments,
            "text": " ".join(s["text"] for s in segments),
        }
        # Mesmo layout do whisperx CLI --output_format all (e do runner
        # MLX): render.find_whisperx_json e o roteamento de variantes
        # continuam funcionando sem mudanca.
        from .mlx_whisper_runner import _write_srt, _write_tsv, _write_txt, _write_vtt
        write_json(output_dir / f"{interview_id}.json", payload)
        _write_srt(output_dir / f"{interview_id}.srt", segments)
        _write_vtt(output_dir / f"{interview_id}.vtt", segments)
        _write_txt(output_dir / f"{interview_id}.txt", segments)
        _write_tsv(output_dir / f"{interview_id}.tsv", segments)

        rotulo_device = "GPU" if device_used == "cuda" else "CPU"
        _emit(progress_callback, interview_id,
              {"event": "asr_done", "progress": 100,
               "message": f"Parakeet ({rotulo_device}) concluido em {elapsed:.1f}s"})
        _log_job(paths, interview_id, repo, config, "ok",
                 output_dir=str(output_dir), elapsed_s=elapsed,
                 device=device_used)

    return failures


def _read_wav_mono16k(path: Path, np: Any) -> Any:
    """Le o WAV preparado do pipeline (16 kHz mono PCM16) como float32."""
    import wave

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
            raise RuntimeError(
                f"WAV fora do formato do pipeline (esperado {SAMPLE_RATE} Hz "
                f"mono): {path.name} tem {w.getframerate()} Hz, "
                f"{w.getnchannels()} canal(is).")
        if w.getsampwidth() != 2:
            raise RuntimeError(
                f"WAV fora do formato do pipeline (esperado PCM16): "
                f"{path.name} tem sampwidth={w.getsampwidth()}.")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _emit(callback: ProgressCallback | None, interview_id: str,
          payload: dict[str, Any]) -> None:
    """Best-effort: callback rude nao pode abortar o lote."""
    if callback is None:
        return
    data = dict(payload)
    data.setdefault("file_id", interview_id)
    try:
        callback(data)
    except Exception:
        pass


def _log_job(
    paths: Paths,
    interview_id: str,
    model: str,
    config: dict,
    status: str,
    *,
    output_dir: str | None = None,
    elapsed_s: float | None = None,
    error: str | None = None,
    device: str = "cpu",
) -> None:
    # Mesmo schema do jobs.jsonl do whisperx_runner/mlx_whisper_runner;
    # backend distingue o produtor, align documenta a decisao (tempos
    # por palavra nativos do TDT) e device registra CPU vs GPU (worker).
    entry: dict[str, Any] = {
        "interview_id": interview_id,
        "stage": "transcribe",
        "status": status,
        "started_at": now_utc(),
        "model": config.get("asr_model", ""),
        "resolved_model": model,
        "backend": "parakeet-onnx",
        "language": "pt",
        "align": "native",
        "device": device,
        "compute_type": "",
        "batch_size": "",
        "variant": config.get("asr_variant") or "",
    }
    if output_dir is not None:
        entry["output_dir"] = output_dir
    if elapsed_s is not None:
        entry["elapsed_s"] = round(elapsed_s, 2)
    if error is not None:
        entry["error"] = error
    append_jsonl(paths.manifest_dir / "jobs.jsonl", entry)
