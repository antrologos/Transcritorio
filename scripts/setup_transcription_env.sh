#!/usr/bin/env bash
# Cria (ou recria) o virtualenv do Transcritorio em um caminho fora do
# repositorio, resolvido via runtime.app_data_dir().
# Mac: $HOME/Library/Application Support/Transcritorio/transcricao-venv
# Linux: ${XDG_DATA_HOME:-$HOME/.local/share}/Transcritorio/transcricao-venv
#
# FFmpeg deve estar no PATH:
#   Mac:    brew install ffmpeg
#   Ubuntu: sudo apt install ffmpeg
#   Fedora: sudo dnf install ffmpeg

set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_COMPILE=1

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd )"
cd "${REPO_ROOT}"

PY="${PYTHON:-python3}"
if ! command -v "${PY}" >/dev/null 2>&1; then
    echo "Python nao encontrado em PATH (\${PYTHON:-python3}). Instale Python 3.11+." >&2
    exit 1
fi

# Resolver venv path via runtime.app_data_dir (mesma logica que os wrappers usam)
VENV_BASE="$("${PY}" -B -c "
import sys
sys.path.insert(0, '${REPO_ROOT}')
from transcribe_pipeline.runtime import app_data_dir
print(app_data_dir())
")"

if [ -z "${TRANSCRICAO_VENV:-}" ]; then
    TRANSCRICAO_VENV="${VENV_BASE}/transcricao-venv"
fi
mkdir -p "${VENV_BASE}"

echo "Criando venv em ${TRANSCRICAO_VENV} ..."
"${PY}" -m venv "${TRANSCRICAO_VENV}"

VPY="${TRANSCRICAO_VENV}/bin/python"
"${VPY}" -m ensurepip --upgrade
"${VPY}" -m pip install --upgrade pip wheel setuptools

# Torch: CPU para Mac/Linux por padrao (no CUDA path fora de Windows).
# Apple Silicon tem MPS mas faster-whisper (CT2) nao suporta.
# Linux com CUDA pode sobrescrever PIP_INDEX_URL manualmente.
"${VPY}" -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0

"${VPY}" -m pip install whisperx==3.8.5 python-docx jiwer "PySide6==6.11.0" \
    "keyring>=24" "cryptography>=42"

echo
echo "Ambiente criado em ${TRANSCRICAO_VENV}."
echo "FFmpeg deve estar no PATH:"
echo "  Mac:    brew install ffmpeg"
echo "  Ubuntu: sudo apt install ffmpeg"
echo "  Fedora: sudo dnf install ffmpeg"
echo
echo "Baixe os modelos no primeiro uso pela GUI (Gerenciar modelos...) ou:"
echo "  scripts/transcribe.sh models download"
