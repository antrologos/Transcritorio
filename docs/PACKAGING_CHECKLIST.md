# Packaging Checklist — antes de push de tag `v*`

Esta checklist existe para evitar lacunas de bundle que quebram a promessa
"baixou, clicou, funciona" em alguma plataforma. Foi criada depois que a
v0.1.1 shippou sem ffmpeg em Mac e Linux (usuarios tinham que rodar
`brew install ffmpeg` / `apt install ffmpeg`).

**Aplique antes de:**
- Push de tag `v*.*.*` que dispara `release.yml` (ver regra 10 em `CLAUDE.md`)
- Re-tag destrutivo (usar apenas se release anterior nunca foi baixada
  publicamente — ver `memory/feedback_version_bumps.md`)

## Dependencias de runtime bundled por plataforma

Todas devem ser **SIM** antes de taguear:

- [ ] **FFmpeg + ffprobe Windows**: `release.yml` step "Download + stage FFmpeg
      (BtbN 7.1 shared)" popula `packaging/vendor/ffmpeg/bin/ffmpeg.exe` +
      `ffprobe.exe`. Spec `packaging/transcritorio.spec:168-172` inclui.
- [ ] **FFmpeg + ffprobe macOS**: `release.yml` step analogo baixa builds
      arm64 de https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip e
      https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip; copia para
      `packaging/vendor/ffmpeg/bin/`. Mesmo spec pega.
- [ ] **FFmpeg + ffprobe Linux**: `release.yml` step analogo baixa static
      build de https://johnvansickle.com/ffmpeg/releases/ (ex:
      `ffmpeg-7.1-amd64-static.tar.xz`); extrai `ffmpeg` e `ffprobe`
      para `packaging/vendor/ffmpeg/bin/`. Mesmo spec pega.
- [ ] **libxcb** (Linux AppImage runtime): nao precisa bundle — o AppImage
      ja depende de libs do sistema; usuario instala via `apt` as xcb
      (isso e aceitavel porque libxcb e parte do ambiente X11 padrao, nao
      uma dependencia de runtime do codigo do app).

## Dependencias que **NAO** devem ser bundled

- [ ] **Modelos Whisper / pyannote**: licencas + tamanho. Usuario baixa uma
      vez via wizard na primeira execucao. HF cache em
      `%LOCALAPPDATA%/Transcritorio/models/huggingface`.
- [ ] **Modelos MLX** (macOS): idem; baixados na primeira transcricao por
      `mlx_whisper.transcribe()` automaticamente.
- [ ] **torch_cuda.dll** (Windows): em bundle SEPARADO `transcritorio-cuda-pack-*-win64.zip`.
      App oferece download via dialog `_maybe_offer_cuda_install()` se
      NVIDIA detectado e dll ausente.

## Assets visuais / integracao SO

- [ ] **Windows**: `.ico` gerado, `transcritorio.iss` valido, DiskSpanning
      produz Setup.exe + Setup-*.bin
- [ ] **macOS**: `.icns` gerado, `create-dmg` com background customizado
      em `packaging/mac/dmg_background.png`
- [ ] **Linux**: PNG icons 256x256 + 512x512 + `.desktop` file em
      `AppDir/usr/share/applications/`

## Consistencia de versao

- [ ] `pyproject.toml` tem `version = "X.Y.Z"` que bate com a tag `vX.Y.Z`
- [ ] `site-src/src/data/version.json` em sync com a mesma versao
- [ ] `transcribe_pipeline/__init__.py` `__version__` em sync (se usado)
- [ ] README menciona versao correta (seccao de downloads e badges)

## Verificacoes automaticas no build

- [ ] `build.ps1` / `.spec` emite build stamp e hash de `cli.py` — nao
      rodar com stamp stale
- [ ] Windows: verifica `torch_cuda.dll` presente no bundle `full`
- [ ] Windows: Setup.exe + Setup-1.bin em `installer-out/`
- [ ] Mac: `dist/Transcritorio.app` existe e abre (smoke opcional)
- [ ] Linux: `smoke-linux-appimage` job roda CLI `--help` e GUI offscreen

## Smoke tests pos-build (manual, recomendado)

Fora do CI (CI nao tem hardware real da plataforma):

- [ ] Windows: instalar Setup.exe, abrir, transcrever 30s de audio
- [ ] macOS: abrir `.dmg` num Mac M-series real, arrastar para
      Applications, transcrever 30s — badge "MLX (Metal)" deve aparecer
- [ ] Linux: baixar `.AppImage` em Ubuntu 22.04+, `chmod +x`, executar,
      transcrever 30s (WSL funciona via WSLg)

## Historia de lacunas ja ocorridas

| Data | Versao | Lacuna | Correcao |
|---|---|---|---|
| 2026-04-21 | v0.1.1 | FFmpeg nao bundled em Mac/Linux | Adicionado na mesma v0.1.1 via workflow_dispatch parcial |

Atualizar esta tabela sempre que uma nova lacuna for descoberta pos-release.
