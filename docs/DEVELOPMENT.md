# Desenvolvimento

Este documento descreve como rodar o Transcritório **do código-fonte**
(não do instalador). Para usuário final, prefira os binários em
[Releases](https://github.com/antrologos/Transcritorio/releases/latest).

## Pré-requisitos

- **Python 3.11+** (3.13 também funciona)
- **FFmpeg 7.x** com `ffmpeg` e `ffprobe` no `PATH`
- **Git**
- Conta Hugging Face com token de leitura (só para o primeiro download
  dos modelos — depois roda offline)

## Setup do ambiente

```bash
# 1. Clone
git clone https://github.com/antrologos/Transcritorio.git
cd Transcritorio

# 2. Criar venv fora do Dropbox (se estiver numa pasta sincronizada)
python -m venv "%LOCALAPPDATA%\Transcritorio\transcricao-venv"  # Windows
python -m venv ~/.local/share/transcritorio-venv                # Linux/Mac

# 3. Ativar venv
.\Scripts\activate.bat                                          # Windows
source ~/.local/share/transcritorio-venv/bin/activate           # Linux/Mac

# 4. Instalar deps + projeto em modo editável
pip install --upgrade pip wheel setuptools
pip install torch==2.8.0 torchaudio==2.8.0              # CPU/CUDA conforme plataforma
pip install torchcodec==0.7.0
pip install -e .
```

No macOS Apple Silicon, adicione a aceleração Metal:

```bash
pip install -e ".[mac]"    # equivalente a -e . + mlx-whisper>=0.4.0
```

No Linux com placa NVIDIA, substitua o torch CPU pelo CUDA:

```bash
pip install torch==2.8.0 torchaudio==2.8.0 \
  --index-url https://download.pytorch.org/whl/cu128
```

## Download de modelos no primeiro uso

Os modelos Whisper e pyannote são baixados do Hugging Face **uma vez**
e ficam em cache local. Depois disso o app roda offline.

Pela CLI:

```bash
export TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN="hf_SEU_TOKEN"   # Linux/Mac
$env:TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN="hf_SEU_TOKEN"     # PowerShell

python -m transcribe_pipeline.cli models download
python -m transcribe_pipeline.cli models verify

unset TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN
```

Pela GUI: **Configurações → Configurar modelos...** (wizard em português).

Segurança de tokens: nunca comite, nunca grave em logs. Detalhes em
[`docs/SEGURANCA_SEGREDOS.md`](SEGURANCA_SEGREDOS.md).

## Executar

**GUI (Estúdio de Revisão)**:

```bash
python -m transcribe_pipeline.review_studio_qt
```

Wrappers conveniência (Windows):

```cmd
scripts\review_studio.cmd
scripts\review_studio.cmd --project "C:\caminho\do\projeto"
```

**CLI** (pipeline completo por etapa):

```bash
python -m transcribe_pipeline.cli --project /caminho/do/projeto manifest
python -m transcribe_pipeline.cli --project /caminho/do/projeto prepare-audio --ids A01
python -m transcribe_pipeline.cli --project /caminho/do/projeto transcribe --ids A01
python -m transcribe_pipeline.cli --project /caminho/do/projeto diarize --ids A01
python -m transcribe_pipeline.cli --project /caminho/do/projeto render --ids A01
python -m transcribe_pipeline.cli --project /caminho/do/projeto qc --ids A01
```

Wrappers Windows em `scripts/transcribe.cmd` apontam para os mesmos
comandos.

## Regras do ambiente

- **Bytecode no Dropbox**: sempre `python -B` ou
  `PYTHONDONTWRITEBYTECODE=1` pra não gerar `__pycache__` dentro de
  pastas sincronizadas.
- **Venv fora do Dropbox**: o Dropbox pode corromper arquivos de venv.
- **FFmpeg no `PATH`**: o pipeline chama `ffmpeg` e `ffprobe` via
  subprocess. No bundle distribuído, eles ficam em
  `vendor/ffmpeg/bin/`; no source, precisam estar no `PATH` do SO.

## Build do instalador (empacotamento)

Veja [`docs/STANDALONE_DISTRIBUTION.md`](STANDALONE_DISTRIBUTION.md) para
detalhes completos de build. Em resumo:

- Windows: `packaging/build.ps1` monta venv isolado, roda PyInstaller,
  gera Setup.exe via Inno Setup.
- macOS/Linux: via CI — `.github/workflows/release.yml` cobre os 3 SOs.
- **Antes de taguear** uma release pública: percorra
  [`docs/PACKAGING_CHECKLIST.md`](PACKAGING_CHECKLIST.md).

## Testes

```bash
# Python -B evita bytecode no Dropbox
python -B tests/toy_mlx_whisper_runner.py
python -B tests/toy_whisperx_mlx_dispatch.py
# ... ou roda o suite inteiro:
python -B -m pytest tests/  # se pytest estiver instalado
```

Os scripts em `tests/toy_*.py` e `tests/smoke_*.py` são testes em estilo
"toy example": rodam rápido, sem depender de hardware GPU ou modelos
reais.

## Contribuir

Abra uma issue em
[GitHub Issues](https://github.com/antrologos/Transcritorio/issues)
descrevendo o bug ou feature. PRs bem-vindos; siga o estilo do código
existente.
