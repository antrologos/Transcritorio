# Changelog

## 0.1.2 — 2026-04-24

Bundle Windows agora funciona **standalone** em PCs sem CUDA Toolkit.
Plataformas Mac e Linux inalteradas no comportamento (torch+cpu wheel
ja resolvia). Tamanho do Windows installer subiu de 596 MB para 1.63 GB
como trade-off pela robustez — v0.1.1 dependia silenciosamente do
CUDA Toolkit instalado pelo usuario e falhava em PCs sem ele.

### Split CPU/CUDA preciso

- `packaging/bundle_filter.py` — lista `CPU_EXTRA` agora mapeia
  **exatamente** as 14 DLLs CUDA que o torch cu128 carrega sob demanda
  via dlopen (cudnn_ops/adv/cnn/engines_*/graph/heuristic, nvrtc*,
  curand, cusolverMg, cufftw, caffe2_nvrtc). A lista `MINIMAL` e vazia
  — `variant=full` preserva todas as 25 CUDA DLLs para o split_bundle
  ter o que rotear ao `cuda_pack`.
- `packaging/transcritorio.spec` — coleta **explicita** das 14 DLLs
  lazy-load via `binaries`. O hook-torch do PyInstaller so pega
  imports IAT; sem essa adicao as lazy-load nunca chegam ao bundle.
- `transcribe_pipeline/runtime.py` — `cuda_libs_present()` usa
  `cudnn_ops64_9.dll` como canario do cuda_pack instalado (antes era
  `torch_cuda.dll`, que agora fica sempre no bundle base por ser IAT).
  `detect_device()` em Windows exige `cuda_libs_present()` alem de
  `torch.cuda.is_available()` — evita crash `cudnn_graph64_9.dll not
  found` em Conv/LSTM quando o usuario NVIDIA ainda nao baixou o
  cuda_pack.

### Por que o bundle Windows cresceu

Versoes 0.1.x anteriores strippavam `cufft64`, `cusparse64` e
`nvJitLink` do bundle — essas 3 sao **imports IAT** de
`torch_cpu.dll`/`torch.dll` em torch cu128. `import torch` falha com
`OSError [WinError 126]` sem elas. v0.1.1 "funcionava" so em PCs que
tinham CUDA Toolkit instalado em `C:\Program Files\NVIDIA GPU
Computing Toolkit` resolvendo via PATH. Em PCs sem CUDA Toolkit o
bundle crashava silenciosamente no primeiro `import torch`.

v0.1.2 preserva as 11 CUDA DLLs IAT obrigatorias no bundle base. As
14 lazy-load vao para o `cuda_pack` separado (download-on-demand a
partir do dialog "Detectamos placa NVIDIA" no primeiro launch).

### Artefatos

| Sistema | Arquivo | Tamanho |
|---|---|---|
| Windows 10/11 | `Transcritorio-0.1.2-Setup.exe` | ~1.63 GB |
| Windows CUDA pack | `transcritorio-cuda-pack-0.1.2-win64.zip` | ~890 MB |
| macOS arm64 | `Transcritorio.dmg` | ~600 MB |
| Linux x86_64 | `Transcritorio-x86_64.AppImage` | ~771 MB |

## 0.1.1 — 2026-04-20

Primeira versao com distribuicao cross-plataforma automatica via GitHub
Actions. Nenhuma mudanca de API ou UX no app em si; esta e uma release
de **infraestrutura** que torna o Transcritorio instalavel em macOS e
Linux alem do Windows.

### Novos artefatos de release

- **Linux AppImage** (`Transcritorio-x86_64.AppImage`, ~1.5 GB) — roda
  em qualquer distro com glibc 2.35+. Pre-requisitos: `ffmpeg` +
  libs xcb do sistema. Veja [`docs/LINUX_INSTALL.md`](docs/LINUX_INSTALL.md).
- **macOS .dmg** (`Transcritorio.dmg`, ~500 MB, arm64) — nao assinado;
  primeira execucao requer "botao direito > Abrir". Veja
  [`docs/MAC_INSTALL.md`](docs/MAC_INSTALL.md). Icone e background
  customizados.
- **Windows Setup** (`Transcritorio-0.1.1-Setup.exe`) — mesmo formato
  de antes, agora buildado no CI em vez de localmente.

### Mudancas internas

- **CI multiplataforma** (`.github/workflows/ci.yml`): matriz Windows /
  Linux / macOS rodando toys + smokes a cada push e PR em `main`.
  Deps minimas (PySide6, numpy, keyring, cryptography — sem torch/
  whisperx/pyannote) rodam em ~2-3min por OS.
- **Release workflow** (`.github/workflows/release.yml`): gatilho
  `workflow_dispatch` (manual) ou tag `v*.*.*`. Builda 3 artefatos,
  automatiza smoke Linux via Xvfb offscreen + CLI.
- **Bundle variant infra** (`packaging/bundle_filter.py` novo): spec
  PyInstaller aceita `TRANSCRITORIO_BUNDLE_VARIANT=cpu|full` via env
  var. Em `cpu`, strippa ~3 GB de CUDA DLLs (torch_cuda, cudnn*,
  cublas*, etc.) cross-plataforma (.dll / .so / .dylib).
- **Helper `runtime.cuda_libs_present()`** — detecta se torch_cuda
  esta no bundle. Usado pela GUI para oferecer download do CUDA pack
  em primeira execucao (pipeline 2E).
- **CUDA download-on-demand** (Item 2B-E do backlog):
  - `build.ps1` produz bundle base CPU + `cuda_pack.zip` separado
  - Inno Setup `[Components]` com detecao de NVIDIA via `nvidia-smi`
  - Download sob demanda via Inno Download Plugin
  - Dialog GUI pos-instalacao oferece CUDA se NVIDIA detectada.

### Testes

De 9 toys na 0.1.0 para **17 toys + 5 smokes**. Cobertura nova:
edge cases de filtro cross-plataforma, cuda_libs_present com FS
inusual, detect_device com torch atipico, token_vault com backends
estranhos.

### Autoria

Commits de 0.1.1 sao de autoria exclusivamente humana; assistentes
LLM nao aparecem como Co-Authored-By, Signed-off-by ou similar
(conforme CLAUDE.md regra #9).

## 0.1.0 — 2026-04-14

Release inicial. Windows-only, instalador Inno Setup, ASR via WhisperX
+ diarizacao pyannote community-1. GUI em PySide6.
