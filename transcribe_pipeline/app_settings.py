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


def diarize_default() -> bool | str:
    """Default de 'diarize' para PROJETOS NOVOS: True ou "auto".

    "auto" = separar falantes quando o modelo estiver instalado no
    momento do job (resolvido por model_manager.diarize_effective).
    False persistido NAO e devolvido: ele so foi gravado pelo perfil
    Essencial de wizards antigos, e significava "nao instalado agora",
    nunca "nao quero falantes" — congela-lo nos projetos novos era o
    bug (2026-08-31). O False explicito continua existindo, mas apenas
    POR PROJETO, quando o usuario desmarca a caixa Separar falantes.
    """
    raw = load().get("diarize_default", "auto")
    if raw is True:
        return True
    return "auto"


def asr_model_default() -> str:
    """Modelo de transcricao escolhido no assistente (por maquina).

    E o default de projetos NOVOS e dos gates sem projeto aberto. Sem
    isto, quem escolhia o tiny no assistente criava projetos exigindo o
    turbo — e caia num pedido de download de 3,1 GB que nunca quis
    (bug do 1o teste real, 2026-08-30).
    """
    from . import model_manager

    valor = str(load().get("asr_model_default") or "").strip()
    return valor if valor in model_manager.ASR_VARIANTS else model_manager.DEFAULT_ASR_VARIANT


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


COMPUTER_USE_MODES = ("tudo", "metade")
COMPUTER_USE_DEFAULT = "tudo"


def computer_use() -> str:
    """Quanto do computador o app pode usar enquanto transcreve (por maquina).

    "tudo" (padrao) e o comportamento de sempre: o app NAO define numero de
    threads nenhum, e os motores usam o que acharem. "metade" deixa o
    computador utilizavel enquanto o lote roda, ao custo medido de ~25% a mais
    de tempo (2026-09-05, notebook de 4 nucleos: 0,172 contra 0,138 s por
    segundo de audio) — com a transcricao saindo IDENTICA byte a byte.

    Preferencia da MAQUINA, nunca do projeto: um projeto viaja pelo Dropbox
    para outro computador com outra contagem de nucleos.
    """
    valor = str(load().get("computer_use") or "").strip().lower()
    return valor if valor in COMPUTER_USE_MODES else COMPUTER_USE_DEFAULT


SEARCH_MAX_RESULTS_DEFAULT = 20
SEARCH_MAX_RESULTS_RANGE = (5, 100)


def search_max_results() -> int:
    """Quantos trechos a janela Perguntar traz NO MAXIMO (por maquina).

    Padrao 20 (decisao do usuario 2026-09-03: mais que os 8 antigos), mas
    a busca so devolve os que tratam do tema — pode vir menos.
    """
    try:
        valor = int(load().get("search_max_results", SEARCH_MAX_RESULTS_DEFAULT))
    except (TypeError, ValueError):
        return SEARCH_MAX_RESULTS_DEFAULT
    lo, hi = SEARCH_MAX_RESULTS_RANGE
    return max(lo, min(hi, valor))


def language_default() -> str:
    """Idioma default de projetos NOVOS (escolha do assistente, etapa 4).

    Quando o usuario marca UM unico idioma na instalacao, projetos novos
    nascem nele; com varios (ou nenhum registro), o default neutro e pt.
    Sempre um codigo do registro de pacotes de idioma — nunca "auto".
    """
    from . import model_manager

    valor = model_manager.normalize_language(load().get("language_default"))
    return valor if model_manager.align_language_supported(valor) else "pt"
