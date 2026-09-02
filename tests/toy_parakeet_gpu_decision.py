"""Toy E-GPU: gpu_execution_plan, worker_command_env e contrato do worker.

Nao depende de GPU nem de onnx-asr: so as funcoes puras e o round-trip
do JSON que o worker emite alimentando a agregacao existente.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import parakeet_runner as pr

# ---------------------------------------------------------------- 1. matriz de decisao
BASE = dict(platform="win32", frozen=False, env_ready=True, cuda_ok=True,
            gpu_failed_before=False)

modo, motivo = pr.gpu_execution_plan("cuda", **BASE)
assert modo == "gpu" and motivo == "", (modo, motivo)

modo, motivo = pr.gpu_execution_plan("cpu", **BASE)
assert modo == "cpu" and motivo == "", "cpu explicito nao gera aviso"

for chave, valor, trecho in [
    ("platform", "darwin", "indisponível"),
    ("platform", "linux", "indisponível"),
    ("frozen", True, "standalone"),
    ("cuda_ok", False, "CUDA"),
    ("env_ready", False, "Gerenciar modelos"),
    ("gpu_failed_before", True, "falhou nesta sessão"),
]:
    kwargs = dict(BASE)
    kwargs[chave] = valor
    modo, motivo = pr.gpu_execution_plan("cuda", **kwargs)
    assert modo == "cpu", (chave, valor)
    assert trecho in motivo, (chave, motivo)
print("PASS: gpu_execution_plan cobre a matriz inteira")

# ---------------------------------------------------------------- 2. worker_command_env
base_env = {"PATH": "C:\\antigo", "OUTRA": "x"}
cmd, env = pr.worker_command_env(
    "PY.exe", Path("W:/worker.py"), Path("W:/a.wav"), Path("W:/modelo"),
    Path("W:/out.json"), window_s=pr.GPU_WINDOW_S, overlap_s=pr.OVERLAP_S,
    torch_lib=Path("T:/torch/lib"), onnx_dir=Path("O:/onnx-gpu"),
    base_env=base_env)
assert cmd[0] == "PY.exe" and cmd[1] == "-B"
assert "--window-s" in cmd and cmd[cmd.index("--window-s") + 1] == "96.0"
assert cmd[cmd.index("--onnx-dir") + 1] == str(Path("O:/onnx-gpu"))
assert env["PYTHONPATH"].startswith(str(Path("O:/onnx-gpu")))
assert env["PATH"].startswith(str(Path("T:/torch/lib")) + os.pathsep)
assert env["PATH"].endswith("C:\\antigo")
assert env["PYTHONIOENCODING"] == "utf-8"
assert env["PYTHONDONTWRITEBYTECODE"] == "1"
assert env["OUTRA"] == "x" and base_env == {"PATH": "C:\\antigo", "OUTRA": "x"}, \
    "base_env nao pode ser mutado"
# PYTHONPATH previo e preservado ATRAS do onnx_dir
_, env2 = pr.worker_command_env(
    "PY.exe", Path("W:/worker.py"), Path("W:/a.wav"), Path("W:/modelo"),
    Path("W:/out.json"), window_s=96.0, overlap_s=5.0,
    torch_lib=Path("T:/lib"), onnx_dir=Path("O:/dir"),
    base_env={"PYTHONPATH": "C:\\meu"})
assert env2["PYTHONPATH"] == str(Path("O:/dir")) + os.pathsep + "C:\\meu"
print("PASS: worker_command_env monta comando e ambiente")

# ---------------------------------------------------------------- 3. contrato do worker
# O JSON de janelas/tokens crus alimenta a agregacao existente e produz
# o MESMO resultado do caminho CPU in-process com as mesmas janelas.
payload = {"windows": [
    {"offset": 0.0,
     "tokens": [" Oi", ",", " tudo", " bem", "?", " Sim"],
     "timestamps": [0.5, 0.7, 1.0, 1.4, 1.7, 89.0],
     "logprobs": [0.0] * 6},
    {"offset": 91.0,  # janela seguinte com step 96-5=91
     "tokens": [" Sim", " claro", "."],
     "timestamps": [-1.0, 3.0, 3.4],  # -1.0: token do overlap, descartado
     "logprobs": [0.0] * 3},
]}
texto = json.dumps(payload, ensure_ascii=False)
lido = json.loads(texto)
per_window = [pr.tokens_to_words(w["tokens"], w["timestamps"], w["logprobs"])
              for w in lido["windows"]]
offsets = [w["offset"] for w in lido["windows"]]
merged = pr.merge_windows(per_window, offsets, overlap_s=5.0)
palavras = [w["word"] for w in merged]
# corte em 91 + 2.5 = 93.5: "Sim" da janela 0 (89.0 < 93.5) fica;
# "Sim" da janela 1 (global 90.0 < 93.5) sai; "claro." (94.0) entra.
assert palavras == ["Oi,", "tudo", "bem?", "Sim", "claro."], palavras
starts = [w["start"] for w in merged]
assert starts == sorted(starts)
segs = pr.words_to_segments(merged)
assert segs[0]["text"] == "Oi, tudo bem?"
print("PASS: round-trip do contrato do worker bate com a agregacao")

# ---------------------------------------------------------------- 4. classificacao do worker
# _recognize_via_worker com run_command_stream simulado: a ORDEM de
# classificacao importa (cancelar PRIMEIRO — terminate() no Windows
# devolve rc 1 e nao pode virar "falha de GPU" + fallback CPU).
import subprocess
import tempfile

with tempfile.TemporaryDirectory() as td:
    tmp_root = Path(td)
    orig_stream = pr.run_command_stream
    orig_torch = pr.onnx_env.torch_lib_dir
    orig_dir = pr.onnx_env.onnx_env_dir
    orig_app = pr.runtime.app_data_dir
    pr.onnx_env.torch_lib_dir = lambda: tmp_root
    pr.onnx_env.onnx_env_dir = lambda: tmp_root / "onnx-gpu"
    pr.runtime.app_data_dir = lambda: tmp_root
    try:
        def chama(rc, cancelado=False, escreve=None):
            def fake_stream(command, on_output=None, should_cancel=None, env=None, **kw):
                out = command[command.index("--out") + 1]
                if escreve is not None:
                    Path(out).write_text(json.dumps(escreve), encoding="utf-8")
                if on_output is not None:
                    on_output('@PROGRESS {"event":"asr_progress","progress":50,'
                              '"message":"Transcrevendo com Parakeet (GPU, 50%)..."}\n')
                return subprocess.CompletedProcess(command, rc, "linha de erro", "")
            pr.run_command_stream = fake_stream
            eventos = []
            try:
                return pr._recognize_via_worker(
                    Path("W:/a.wav"), Path("W:/modelo"), "T1",
                    eventos.append, (lambda: cancelado)), eventos
            finally:
                pr.run_command_stream = orig_stream

        # cancelado vence mesmo com rc != 0
        try:
            chama(1, cancelado=True)
            raise AssertionError("devia levantar _WorkerCancelled")
        except pr._WorkerCancelled:
            pass
        # exit 42 = CUDA indisponivel
        try:
            chama(pr.WORKER_EXIT_NO_CUDA)
            raise AssertionError("devia levantar ParakeetGpuError")
        except pr.ParakeetGpuError as exc:
            assert "CUDA" in str(exc)
        # rc 0 sem arquivo de saida = falha
        try:
            chama(0)
            raise AssertionError("devia levantar ParakeetGpuError")
        except pr.ParakeetGpuError:
            pass
        # rc 0 com JSON valido: devolve janelas parseadas + repassa progresso
        (per_win, offs), eventos = chama(0, escreve={"windows": [
            {"offset": 0.0, "tokens": [" Oi"], "timestamps": [0.5],
             "logprobs": [0.0]}]})
        assert offs == [0.0] and per_win[0][0]["word"] == "Oi"
        assert any(e.get("progress") == 50 and "GPU" in e.get("message", "")
                   for e in eventos), eventos
        # tmp limpo (unlink no finally)
        assert not list((tmp_root / "tmp").glob("parakeet_*.json"))
    finally:
        pr.run_command_stream = orig_stream
        pr.onnx_env.torch_lib_dir = orig_torch
        pr.onnx_env.onnx_env_dir = orig_dir
        pr.runtime.app_data_dir = orig_app
print("PASS: _recognize_via_worker classifica cancelado/42/falha/ok")

# ---------------------------------------------------------------- 4b. planned_device (estimativas/selo)
_orig_platform = sys.platform
_orig_ready = pr.onnx_env.onnx_env_ready
_orig_cuda = pr.runtime.cuda_libs_present
_orig_failed = pr._GPU_FAILED_THIS_SESSION
try:
    sys.platform = "win32"
    pr.onnx_env.onnx_env_ready = lambda env_dir=None: True
    pr.runtime.cuda_libs_present = lambda: True
    pr._GPU_FAILED_THIS_SESSION = False
    assert pr.planned_device("cuda") == "cuda"
    assert pr.planned_device("cpu") == "cpu"
    pr.onnx_env.onnx_env_ready = lambda env_dir=None: False
    assert pr.planned_device("cuda") == "cpu"          # sem o pacote onnx-gpu: CPU
    pr.onnx_env.onnx_env_ready = lambda env_dir=None: True
    sys.platform = "linux"
    assert pr.planned_device("cuda") == "cpu"          # fora do Windows: CPU
    sys.platform = "win32"
    pr._GPU_FAILED_THIS_SESSION = True
    assert pr.planned_device("cuda") == "cpu"          # GPU falhou na sessao
finally:
    sys.platform = _orig_platform
    pr.onnx_env.onnx_env_ready = _orig_ready
    pr.runtime.cuda_libs_present = _orig_cuda
    pr._GPU_FAILED_THIS_SESSION = _orig_failed
print("PASS: planned_device")

# ---------------------------------------------------------------- 5. constantes coerentes
assert 0 < pr.GPU_WINDOW_S < 200.0, "janela GPU deve ficar sob o limite do export"
assert pr.GPU_WINDOW_S < pr.WINDOW_S, "GPU usa janela menor (VRAM + velocidade)"
assert pr.WORKER_EXIT_NO_CUDA == 42
print("PASS: constantes")

print("PASS: toy_parakeet_gpu_decision")
