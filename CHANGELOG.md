# Changelog

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
