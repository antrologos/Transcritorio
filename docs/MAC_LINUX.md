# Transcritorio em Mac e Linux (MVP)

A versao 0.2.0 traz suporte experimental a macOS e Linux via scripts
`.sh` e deteccao de dispositivo com MPS. Instaladores `.dmg` e AppImage
ficam para 0.3+.

## macOS (Apple Silicon e Intel)

### Pre-requisitos

```sh
brew install ffmpeg python@3.11
```

### Instalacao

```sh
git clone <url do repo>
cd Transcritorio
./scripts/setup_transcription_env.sh
```

O venv fica em `~/Library/Application Support/Transcritorio/transcricao-venv`.

### Uso

```sh
./scripts/review_studio.sh                         # GUI
./scripts/review_studio.sh --project /path/proj    # GUI com projeto
./scripts/transcribe.sh --project /path/proj manifest
./scripts/transcribe.sh --project /path/proj transcribe --ids ID
```

### Desempenho em Apple Silicon

- `detect_device()` retorna `"mps"` quando o Metal Performance Shaders
  esta disponivel.
- **ASR (faster-whisper / CTranslate2)** nao suporta MPS. O pipeline
  cai automaticamente para **CPU** com aviso:
  > Apple Silicon (MPS) detectado, mas faster-whisper usa CPU para ASR.
  > Transcrevendo em CPU (~3x tempo real).
- **Diarizacao (pyannote)** suporta MPS parcialmente; na pratica o
  rendimento em CPU ainda e razoavel.
- Tempos esperados em M1/M2: 3-5x o tempo de uma RTX 4060 (referencia
  Windows). Audio de 60min tende a durar 40-60min.
- Aceleracao real com `mlx-whisper` e roadmap 0.3+.

## Linux (Ubuntu/Debian)

### Pre-requisitos

```sh
sudo apt update
sudo apt install ffmpeg python3.11 python3.11-venv \
    libxcb-cursor0 libxcb-shape0 libxcb-xinerama0 libxkbcommon-x11-0
```

As libs `libxcb-*` sao necessarias para o Qt PySide6 rodar.

### CUDA (opcional)

Se voce tem uma GPU NVIDIA e driver CUDA 12.x, edite
`scripts/setup_transcription_env.sh` para trocar a linha de instalacao
do torch por:

```sh
"${VPY}" -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 \
    --index-url https://download.pytorch.org/whl/cu128
```

## Linux (Fedora)

```sh
sudo dnf install ffmpeg python3.11
```

Para o RPM Fusion e necessario para o FFmpeg completo.

## Armazenamento de tokens HF

A v0.2 usa a lib `keyring` para armazenar tokens Hugging Face:

- **Windows**: Credential Manager (via WinVault / DPAPI sob o capo).
  Migracao automatica do vault DPAPI legado (`hf_token.vault`) na
  primeira chamada de `retrieve()` — atomica, com rollback em caso de
  falha.
- **macOS**: Keychain.
- **Linux desktop**: SecretService (GNOME Keyring / KWallet).
- **Linux headless** (`$DISPLAY` vazio e dbus ausente): Fernet com
  chave derivada do `/etc/machine-id` via PBKDF2, armazenada em
  `~/.local/share/Transcritorio/hf_token.fallback` com permissao 0600.

Nada do token atravessa a rede a nao ser para o download dos modelos
via API oficial da Hugging Face.

## Troubleshooting

### "PySide6 nao esta instalado"

O script caiu no `${PY}` do sistema. Rode
`./scripts/setup_transcription_env.sh` de novo; confira que o diretorio
`${XDG_DATA_HOME:-$HOME/.local/share}/Transcritorio/transcricao-venv`
(Linux) ou `~/Library/Application Support/Transcritorio/transcricao-venv`
(Mac) existe.

### "Could not find torch.libs" / erro ao importar torch

Confira se `ffmpeg` esta no PATH:
```sh
which ffmpeg
ffmpeg -version
```

### Diarizacao demora (Linux sem CUDA)

Esperado. pyannote com CPU leva ~2x o tempo do audio. Sem GPU NVIDIA
nao ha aceleracao real no Linux.

### Dropbox lock em `.venv` no Linux

O venv vai para `~/.local/share/Transcritorio/...` (XDG), fora do
Dropbox — nao deve dar conflito. Nao mova o venv para dentro do
repo clonado.

### Erro `keyring.errors.NoKeyringError`

O backend grafico de credenciais nao esta ativo. O codigo cai em
Fernet automaticamente; nenhuma acao necessaria.

### MPS detectado mas transcricao lenta

O faster-whisper nao usa MPS. O codigo imprime um aviso explicito no
terminal informando que a ASR roda em CPU. A aceleracao via MPS vai
chegar num backend alternativo (`mlx-whisper`) em 0.3+.
