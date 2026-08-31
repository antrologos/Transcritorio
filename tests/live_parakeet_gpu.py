"""Validacao VIVA do modo GPU do Parakeet — nao roda no CI.

Requer: NVIDIA + torch cu128 + onnx-gpu provisionado + modelo TAGARELA
no cache + um WAV 16k mono passado como argv[1]. Sem qualquer um deles:
SKIP (exit 0). Transcreve de verdade pelo run_parakeet e verifica que o
caminho GPU foi usado (device=cuda no jobs.jsonl) e que a saida e sana.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import model_manager, onnx_env, runtime
from transcribe_pipeline import parakeet_runner as pr
from transcribe_pipeline.config import Paths


def skip(motivo: str) -> None:
    print(f"SKIP: {motivo}")
    sys.exit(0)


if len(sys.argv) < 2:
    skip("uso: live_parakeet_gpu.py <wav 16k mono>")
wav_src = Path(sys.argv[1])
if not wav_src.exists():
    skip(f"wav nao encontrado: {wav_src}")
if sys.platform != "win32":
    skip("modo GPU e win32-only nesta fase")
if not runtime.has_nvidia_gpu() or not runtime.cuda_libs_present():
    skip("sem NVIDIA/CUDA nesta maquina")
if not onnx_env.onnx_env_ready():
    skip("onnx-gpu nao provisionado (Gerenciar modelos > Baixar)")
spec = model_manager.ASR_VARIANTS["parakeet-pt"]
snap = model_manager.cached_snapshot_path(
    str(spec["repo"]), runtime.model_cache_dir(), revision=str(spec["revision"]))
if snap is None:
    skip("modelo TAGARELA nao esta no cache")

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    out = root / "Transcricoes"
    paths = Paths(
        project_root=root, output_root=out, config_dir=out / "00_config",
        manifest_dir=out / "00_manifest", wav_dir=out / "01_audio_wav16k_mono",
        asr_dir=out / "02_asr_raw", asr_variants_dir=out / "02_asr_variants",
        diarization_dir=out / "03_diarization", canonical_dir=out / "04_canonical",
        review_dir=out / "05_transcripts_review", qc_dir=out / "06_qc",
        logs_dir=out / "00_project",
    )
    paths.manifest_dir.mkdir(parents=True)
    paths.wav_dir.mkdir(parents=True)
    wav = paths.wav_dir / "G1.wav"
    wav.write_bytes(wav_src.read_bytes())

    rows = [{"interview_id": "G1", "wav_path": str(wav.relative_to(root)),
             "selected": "true"}]
    config = {"asr_model": "parakeet-pt", "asr_language": "pt",
              "asr_device": "auto", "diarize": False,
              "asr_model_cache_only": True,
              "model_download_token_env": "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN"}
    eventos: list[dict] = []
    t0 = time.time()
    falhas = pr.run_parakeet(rows, config, paths, progress_callback=eventos.append)
    dt = time.time() - t0
    assert falhas == 0, f"falhas={falhas}; ultimo evento: {eventos[-1:] }"

    jobs = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    entry = json.loads(jobs.strip().splitlines()[-1])
    assert entry["device"] == "cuda", f"esperava GPU, rodou {entry['device']}"
    assert entry["backend"] == "parakeet-onnx" and entry["status"] == "ok"

    data = json.loads((paths.asr_dir / "G1.json").read_text(encoding="utf-8"))
    words = [w for s in data["segments"] for w in s.get("words") or []]
    assert len(words) > 50, "poucas palavras"
    starts = [w["start"] for w in words]
    assert starts == sorted(starts), "timestamps nao monotonicos"
    gpu_msgs = [e for e in eventos if "GPU" in str(e.get("message", ""))]
    assert gpu_msgs, "nenhuma mensagem de progresso citou a GPU"
    print(f"PASS: live_parakeet_gpu — {len(words)} palavras em {dt:.1f}s "
          f"(device=cuda, {entry['elapsed_s']}s de transcricao)")
