"""Ferramentas do canal de instalacao uv/PyPI (v0.2).

A partir da v0.2 o Transcritorio e distribuido como pacote Python instalado
via `uv tool install transcritorio` (o standalone PyInstaller foi aposentado).
Este modulo concentra:

- deteccao do modo de execucao (frozen legado vs pacote);
- localizacao do `uv` e os comandos de reparo/atualizacao/aceleracao CUDA
  (exibidos ao usuario para rodar com o app FECHADO — o uv nao consegue
  reinstalar um ambiente cujos arquivos estao em uso pelo proprio app);
- criacao unica do atalho na area de trabalho no primeiro run (Windows).

Sem dependencias de Qt — testavel com stdlib pura.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import app_settings

PACKAGE_NAME = "transcritorio"

# Flags obrigatorias para o extra [cuda] FORA do repositorio: tool.uv.sources
# (que mapeia torch/torchaudio/torchvision ao indice cu128) NAO viaja no wheel
# publicado — sem estas flags, uv resolveria torch>=2.7 do PyPI (CPU no
# Windows) e a aceleracao nunca ativaria. unsafe-best-match e necessario
# porque o indice cu128 tambem publica torchcodec (sem wheel Windows) e o
# first-match-wins travaria nele; com best-match, os builds +cu128 (versao
# local > versao base) vencem para o trio torch e o torchcodec vem do PyPI.
# Validado empiricamente em 2026-08-23 na maquina de desenvolvimento.
_CUDA_INDEX_FLAGS = (
    " --index https://download.pytorch.org/whl/cu128"
    " --index-strategy unsafe-best-match"
)


def is_frozen() -> bool:
    """True no bundle PyInstaller legado (canal standalone aposentado)."""
    return bool(getattr(sys, "frozen", False))


def find_uv() -> str | None:
    return shutil.which("uv")


# Python FIXO (2026-09-02): o uv escolhe o Python mais novo da maquina ao
# (re)criar o ambiente e o torchcodec ainda nao tem wheel para o 3.14 —
# caso real de beta tester; o teto requires-python do pacote NAO faz o uv
# trocar de interpretador. 3.12 e o Python do lock/CI/maquina de referencia.
_PYTHON_FLAG = "--python 3.12"


def repair_command(cuda: bool | None = None) -> str:
    """Comando que reconstroi o ambiente do app (nao toca projetos/modelos)."""
    if cuda is None:
        cuda = cuda_extra_installed()
    if cuda:
        return f'uv tool install --reinstall {_PYTHON_FLAG} "{PACKAGE_NAME}[cuda]"{_CUDA_INDEX_FLAGS}'
    return f'uv tool install --reinstall {_PYTHON_FLAG} "{PACKAGE_NAME}"'


def upgrade_command() -> str:
    return f"uv tool upgrade {PACKAGE_NAME}"


def cuda_install_command() -> str:
    return f'uv tool install --reinstall {_PYTHON_FLAG} "{PACKAGE_NAME}[cuda]"{_CUDA_INDEX_FLAGS}'


def cuda_extra_installed() -> bool:
    """Se o usuario instalou a aceleracao NVIDIA neste computador."""
    return bool(app_settings.load().get("cuda_extra_installed", False))


def mark_cuda_extra_installed(value: bool = True) -> None:
    app_settings.save({"cuda_extra_installed": bool(value)})


def _gui_launcher_path() -> str | None:
    """Executavel que abre a GUI (shim do uv tool / entry point no PATH)."""
    return shutil.which(PACKAGE_NAME)


def _icon_path() -> str:
    """Caminho do .ico para o atalho; "" se ausente (nunca levanta).

    O wheel (v0.2) embarca os icones em transcribe_pipeline/assets
    (package-data); rodando da fonte, ficam em assets/ na raiz do repo.
    """
    for base in (Path(__file__).parent / "assets",
                 Path(__file__).parent.parent / "assets"):
        candidato = base / "transcritorio_icon.ico"
        if candidato.exists():
            return str(candidato)
    return ""


def _shortcut_script(target: str, name: str, arguments: str, icon: str) -> str:
    """Comando PowerShell do atalho (pura, para o toy)."""
    return (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        f"$s = $ws.CreateShortcut((Join-Path $desktop '{name}.lnk')); "
        f"$s.TargetPath = '{target}'; "
        + (f"$s.Arguments = '{arguments}'; " if arguments else "")
        + (f"$s.IconLocation = '{icon}'; " if icon else "")
        + "$s.Save()"
    )


def create_windows_shortcut(target: str, name: str = "Transcritório",
                            arguments: str = "", icon: str = "") -> bool:
    """Cria atalho na area de trabalho via WScript.Shell. Nunca levanta."""
    if sys.platform != "win32":
        return False
    try:
        script = _shortcut_script(target, name, arguments, icon)
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception:
        return False


def ensure_first_run_setup() -> None:
    """No primeiro run do canal uv/PyPI (Windows), cria o atalho da area de
    trabalho uma unica vez. Nunca levanta — falha aqui nao pode impedir o app
    de abrir."""
    try:
        if is_frozen() or sys.platform != "win32":
            return
        settings = app_settings.load()
        if settings.get("shortcut_created"):
            return
        target = _gui_launcher_path()
        if not target:
            return  # instalado sem shim no PATH (ex.: dev) — nada a fazer
        # O atalho aponta para o pythonw do ambiente (nao para o shim .exe do
        # uv): politicas de integridade de codigo (Device Guard/WDAC, vistas
        # em beta 2026-08) bloqueiam executaveis nao assinados, e o pythonw
        # de um CPython oficial e assinado. Um trampolim a menos para todos.
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        icone = _icon_path()
        if pythonw.exists():
            created = create_windows_shortcut(
                str(pythonw), arguments="-m transcribe_pipeline.gui_launcher",
                icon=icone)
        else:
            created = create_windows_shortcut(str(Path(target)), icon=icone)
        if created:
            app_settings.save({"shortcut_created": True})
    except Exception:
        pass
