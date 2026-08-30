"""Preferencias da INSTALACAO (por maquina), fora dos projetos.

Distinto do run_config.yaml (por projeto): aqui ficam escolhas feitas no
primeiro uso que valem como default para projetos novos — ex.: se o usuario
optou por identificacao de falantes no wizard (v0.2, diarizacao opcional).
Gravado em runtime.app_data_dir()/app_settings.json (fora do Dropbox).
"""
from __future__ import annotations

from typing import Any

from . import runtime
from .utils import read_json, write_json

_FILENAME = "app_settings.json"


def _settings_path():
    return runtime.app_data_dir() / _FILENAME


def load() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Arquivo corrompido nunca pode impedir o app de abrir.
        return {}


def save(updates: dict[str, Any]) -> None:
    data = load()
    data.update(updates)
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)


def diarize_default() -> bool:
    """Default de 'diarize' para PROJETOS NOVOS (escolha do wizard)."""
    return bool(load().get("diarize_default", True))


def install_profile() -> str:
    """Perfil de instalacao escolhido no assistente (por maquina).

    "essencial" | "padrao" | "completo". Instalacoes anteriores ao
    conceito de perfil caem em "padrao" — e o que elas tinham de fato
    (transcricao + alinhamento + falantes conforme diarize_default).
    """
    valor = str(load().get("install_profile") or "").strip().lower()
    return valor if valor in {"essencial", "padrao", "completo"} else "padrao"


def alignment_default() -> bool:
    """O perfil essencial dispensa o alinhador (sem tempos por palavra)."""
    return install_profile() != "essencial"
