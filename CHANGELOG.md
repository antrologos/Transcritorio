# Changelog

## 0.1.4 — 2026-04-24

Segundo bug latente pego pelo gate CI introduzido em v0.1.3: apos resolver
o `PackageNotFoundError: torchcodec`, o gate revelou que `whisperx` tambem
quebrava com `ModuleNotFoundError: No module named 'torchvision'` antes de
qualquer transcribe.

### Root cause

`torchmetrics.functional.image.arniqa.py:31` importa `torchvision` no topo
do modulo. Esse arquivo e carregado eagerly via:
```
whisperx -> asr -> vads/pyannote -> pyannote.audio -> lightning -> torchmetrics
```
O spec.py de v0.1.x explicitamente excluia `torchvision` do bundle por
ele "nao ser usado" — comentario incorreto, ele e usado transitivamente.

### Fix

- `packaging/transcritorio.spec`:
  - Removido `"torchvision"` do `excludes`.
  - Adicionado `torchvision` ao loop de `collect_submodules`.
- `.github/workflows/release.yml`:
  - Linux job instala `torchvision==0.23.0 --index-url whl/cpu`.
  - Mac job instala `torchvision==0.23.0` (default arm64+CPU/MPS wheel).
  - Windows ja tinha `torchvision==0.23.0` no install com `whl/cu128`.

### Como o bug nao apareceu antes

Mesmo padrao do bug torchcodec da v0.1.3: o CI nunca invocava `whisperx.exe`
contra audio real. v0.1.3 introduziu o gate que executa o binario contra
3s de silencio offline; foi ele que pegou os DOIS bugs em sequencia.

## 0.1.3 — 2026-04-24

Correcao de bug critico em **todas as plataformas**: `whisperx.exe` crashava
no primeiro transcribe com `PackageNotFoundError: torchcodec`. Bug existia
silenciosamente desde v0.1.1 porque o CI nunca exercitou o caminho
`whisperx.exe audio.wav` — so testava `Test-Path` do binario.

### Root cause

`transformers==5.5.1` em `audio_utils.py:55` faz:
```python
if is_torchcodec_available():
    TORCHCODEC_VERSION = version.parse(importlib.metadata.version("torchcodec"))
```

`is_torchcodec_available()` usa `find_spec("torchcodec")` que retorna
truthy pois o pacote Python esta no bundle. Mas o PyInstaller empacota
os arquivos `.py` sem empacotar o `torchcodec-0.7.0.dist-info/`, entao
`version("torchcodec")` levanta `PackageNotFoundError`. Esse caminho
dispara assim que `whisperx` importa `alignment` (toda invocacao do
whisperx.exe com audio real).

### Fix

- `packaging/transcritorio.spec` — `copy_metadata()` para 13 pacotes
  (torchcodec + torch/torchaudio/transformers/huggingface_hub/tokenizers/
  tqdm/regex/requests/packaging/filelock/pyyaml/numpy). Defensivo — qualquer
  outro pacote que chame `importlib.metadata.version("<self>")` em runtime
  estava sujeito ao mesmo bug.
- `packaging/transcritorio.spec` — `hidden_imports` ganhou `torchcodec` +
  `collect_submodules("torchcodec")`. Complementa o `copy_metadata`.
- `.github/workflows/release.yml` — novo gate "Frozen-bundle whisperx
  import chain" em Windows, Linux e Mac. Roda `whisperx` contra audio
  real (3s silencio, modelo tiny offline). Pega este tipo de bug antes
  da release publicar.

### Como o bug passou despercebido

- `transcritorio-cli.exe models smoke-test` usa `faster_whisper.WhisperModel`
  diretamente, nao carrega `whisperx.alignment` nem `transformers.audio_utils`.
- `whisperx.exe --help` passa porque argparse carrega antes dos imports lazy.
- CI sempre usou `Test-Path whisperx.exe` como validacao — existe o binario,
  mas nunca foi invocado.
- O usuario de v0.1.1 que tenha `torchcodec` instalado via pip no sistema
  (fora do bundle) nao via o bug, porque `find_spec` resolveria via PATH
  do Python externo. Apenas bundles PyInstaller frozen quebravam.

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
