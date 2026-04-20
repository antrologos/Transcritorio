#!/usr/bin/env bash
# Abre o Estudio de Revisao (GUI PySide6). Resolve o venv via runtime.app_data_dir()
# para Mac/Linux. Se nao houver venv, usa o python do sistema.

set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
TRANSCRITORIO_ROOT="$( cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd )"

export PYTHONPATH="${TRANSCRITORIO_ROOT}/scripts/python_sitecustomize:${TRANSCRITORIO_ROOT}:${PYTHONPATH:-}"

PY="${PYTHON:-python3}"

if [ -z "${TRANSCRICAO_VENV:-}" ]; then
    VENV_BASE="$("${PY}" -B -c "
import sys
sys.path.insert(0, '${TRANSCRITORIO_ROOT}')
from transcribe_pipeline.runtime import app_data_dir
print(app_data_dir())
" 2>/dev/null || true)"
    if [ -n "${VENV_BASE}" ]; then
        TRANSCRICAO_VENV="${VENV_BASE}/transcricao-venv"
    fi
fi

if [ -n "${TRANSCRICAO_VENV:-}" ] && [ -x "${TRANSCRICAO_VENV}/bin/python" ]; then
    exec "${TRANSCRICAO_VENV}/bin/python" -B -m transcribe_pipeline.review_studio_qt "$@"
else
    exec "${PY}" -B -m transcribe_pipeline.review_studio_qt "$@"
fi
