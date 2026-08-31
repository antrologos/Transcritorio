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

import math
import re
import time
from pathlib import Path
from typing import Any, Callable

from .config import Paths
from .manifest import selected_rows
from . import model_manager, runtime
from .model_manager import validate_local_diarization_model
from .utils import append_jsonl, now_utc, sanitize_message, write_json

ProgressCallback = Callable[[dict[str, Any]], None]

# Mesmo padrao de sanitizacao do mlx_whisper_runner/asr_output_dir.
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")

SAMPLE_RATE = 16_000
# Janela < 200 s (limite do export); overlap para o merge ter contexto.
WINDOW_S = 170.0
OVERLAP_S = 5.0
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

    if config.get("diarize", True):
        validate_local_diarization_model(config.get("diarize_model"))

    from .whisperx_runner import asr_output_dir
    output_dir = asr_output_dir(paths, config)
    output_dir.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import onnx_asr

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
        try:
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

            offsets: list[float] = []
            per_window: list[list[dict[str, Any]]] = []
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

            words = merge_windows(per_window, offsets)
            segments = words_to_segments(words)
        except Exception as exc:
            detail = sanitize_message(str(exc).strip() or type(exc).__name__)
            if len(detail) > 240:
                detail = detail[:240].rstrip() + "..."
            _emit(progress_callback, interview_id,
                  {"event": "asr_error", "progress": 0,
                   "message": f"Parakeet falhou ({type(exc).__name__}): {detail}"})
            _log_job(paths, interview_id, repo, config, "error",
                     error=sanitize_message(str(exc)))
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

        _emit(progress_callback, interview_id,
              {"event": "asr_done", "progress": 100,
               "message": f"Parakeet concluido em {elapsed:.1f}s"})
        _log_job(paths, interview_id, repo, config, "ok",
                 output_dir=str(output_dir), elapsed_s=elapsed)

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
) -> None:
    # Mesmo schema do jobs.jsonl do whisperx_runner/mlx_whisper_runner;
    # backend distingue o produtor e align documenta a decisao (tempos
    # por palavra nativos do TDT — nenhum alinhador envolvido).
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
