"""Worker GPU do motor Parakeet — roda em SUBPROCESSO, nunca importado.

Contrato (mesmo espirito do llm_worker.py): o nivel de modulo e stdlib
pura; numpy/onnx_asr so dentro de main(). Este arquivo NAO importa o
pacote transcribe_pipeline — e executado por caminho de arquivo com o
PROPRIO interpretador do app, mas com PYTHONPATH prefixado pelo
diretorio onnx-gpu (onnxruntime-gpu isolado; ver onnx_env.py). Emitir
tokens CRUS por janela e deixar a agregacao palavra/segmento para o
processo pai mantem um unico dono das saidas (parakeet_runner).

Protocolo:
- stdout: linhas "@PROGRESS {json}" (parse no pai via
  utils.parse_progress_json_line); qualquer outra linha vira contexto
  de erro.
- saida: JSON {"windows": [{"offset", "tokens", "timestamps",
  "logprobs"}]} escrito direto em --out (sem .tmp/rename).
- exit 0 = ok; exit 42 = CUDA EP indisponivel ou pacote errado (o pai
  faz fallback CPU com mensagem especifica); exit 1 = falha geral.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave

PROGRESS_PREFIX = "@PROGRESS "
EXIT_NO_CUDA = 42
SAMPLE_RATE = 16_000


def emit(progress: int, message: str) -> None:
    print(PROGRESS_PREFIX + json.dumps(
        {"event": "asr_progress", "progress": int(progress),
         "message": message}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-s", type=float, required=True)
    parser.add_argument("--overlap-s", type=float, required=True)
    parser.add_argument("--torch-lib", required=True)
    parser.add_argument("--onnx-dir", required=True)
    args = parser.parse_args()

    # DLLs CUDA (cublas/cudnn9) vem do torch cu128 do ambiente do app.
    if os.path.isdir(args.torch_lib):
        os.add_dll_directory(args.torch_lib)

    import onnxruntime

    # Defesa contra o modo de falha SILENCIOSO medido em 2026-08-30:
    # onnxruntime CPU vencendo a resolucao de import, ou build sem CUDA.
    # Falha ruidosa e recuperavel (exit 42) em vez de rodar lento como
    # se fosse GPU.
    ort_file = os.path.abspath(getattr(onnxruntime, "__file__", "") or "")
    onnx_dir = os.path.abspath(args.onnx_dir)
    if not ort_file.startswith(onnx_dir):
        print(f"onnxruntime resolvido fora do diretorio de aceleracao: {ort_file}")
        return EXIT_NO_CUDA
    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        print("CUDAExecutionProvider indisponivel no onnxruntime carregado.")
        return EXIT_NO_CUDA

    import numpy as np
    import onnx_asr

    emit(1, "Carregando o modelo Parakeet na GPU...")
    model = onnx_asr.load_model(
        "nemo-parakeet-tdt-0.6b-v3", args.model_dir,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    ).with_timestamps()

    # Introspeccao best-effort: confirmar que a sessao do encoder ficou
    # no CUDA EP (atributo privado do onnx_asr — se a estrutura mudar,
    # seguimos; o check de import acima ja segurou o essencial).
    try:
        session = model.asr._encoder  # noqa: SLF001
        if "CUDAExecutionProvider" not in session.get_providers():
            print("Sessao do encoder ficou sem o CUDA EP (init falhou).")
            return EXIT_NO_CUDA
    except AttributeError:
        pass

    with wave.open(args.wav, "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 \
                or w.getsampwidth() != 2:
            print(f"WAV fora do formato do pipeline (16 kHz mono PCM16): {args.wav}")
            return 1
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0

    total_s = len(audio) / SAMPLE_RATE
    step = args.window_s - args.overlap_s
    n_win = max(1, math.ceil(max(total_s - args.overlap_s, 1e-9) / step))

    windows = []
    for i in range(n_win):
        off = i * step
        chunk = audio[int(off * SAMPLE_RATE):
                      int((off + args.window_s) * SAMPLE_RATE)]
        result = model.recognize(chunk, sample_rate=SAMPLE_RATE)
        windows.append({
            "offset": off,
            "tokens": list(result.tokens),
            "timestamps": [float(t) for t in result.timestamps],
            "logprobs": ([float(p) for p in result.logprobs]
                         if result.logprobs is not None else None),
        })
        pct = max(1, min(98, int(round((i + 1) / n_win * 98))))
        emit(pct, f"Transcrevendo com Parakeet (GPU, {pct}%)...")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"windows": windows}, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - stdout vira tail de erro no pai
        print(f"{type(exc).__name__}: {exc}")
        sys.exit(1)