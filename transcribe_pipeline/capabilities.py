"""Registro de capacidades: o que o app pode fazer NESTA maquina.

Fonte unica de verdade para tres coisas que hoje vivem espalhadas e
divergem entre si: o assistente de instalacao (o que recomendar e
baixar), o estado da interface (o que fica disponivel) e o gerenciador
de modelos (o que cada download habilita).

O problema que isto resolve: o app nao sabia do que era capaz. So
detectava se EXISTE uma placa NVIDIA — uma de 2 GB passava no teste e
falhava depois ao carregar um modelo de 8,7 GB — e nenhuma acao ficava
desabilitada por falta de recurso: a indisponibilidade so aparecia
depois do clique, em tres padroes diferentes, um deles sem saida.

Regra de ouro do modulo: **recomendar, nunca decidir**. Uma capacidade
incompativel com a maquina continua escolhivel; o que muda e o aviso.

Nucleo puro e testavel: as funcoes de decisao recebem o retrato do
hardware e o conjunto de modelos em cache; nada aqui importa torch, Qt
ou rede.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

ASR_MODEL_TOKEN = "@asr"  # resolvido para a variante escolhida no projeto

# Simulacao de maquina para TESTE do assistente ("cpu", "minimo",
# "gpu2", "gpu24"...). Sem isso seria impossivel ver, numa maquina boa,
# o que o assistente recomendaria numa fraca.
FAKE_HARDWARE_ENV = "TRANSCRITORIO_FAKE_HARDWARE"


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    explains: str                      # o que habilita, em linguagem do usuario
    models: tuple[str, ...] = ()       # chaves em model_manager
    needs_gpu: bool = False
    min_vram_gb: float = 0.0
    needs_llm_env: bool = False
    essential: bool = False


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "transcrever",
        "Transcrever entrevistas",
        "converte a fala em texto com marcação de tempo",
        models=(ASR_MODEL_TOKEN,),
        essential=True,
    ),
    Capability(
        "separar_falantes",
        "Separar quem fala",
        "divide a transcrição por falante (Entrevistador/Entrevistado)",
        models=("diarization",),
    ),
    Capability(
        "tempos_por_palavra",
        "Tempos por palavra",
        "duplo clique numa palavra vai ao áudio; corte no ponto exato",
        models=("alignment_pt",),
    ),
    Capability(
        "busca_semantica",
        "Busca por sentido",
        "encontra trechos pelo significado, sem as palavras exatas",
        models=("search_encoder",),
    ),
    Capability(
        "glossario_nomes",
        "Glossário de nomes",
        "lista pessoas, lugares e instituições e junta grafias diferentes",
        models=("ner_gliner",),
        needs_llm_env=True,
    ),
    Capability(
        "resumo_perguntar",
        "Resumir e perguntar com AI",
        "resumo com índice temático e perguntas respondidas com citações",
        models=("llm_qwen",),
        needs_gpu=True,
        min_vram_gb=6.0,
        needs_llm_env=True,
    ),
)

# Perfis sugeridos, do mais leve ao mais completo (ordem importa).
PROFILES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("essencial", "Essencial",
     ("transcrever",)),
    ("padrao", "Padrão",
     ("transcrever", "separar_falantes", "tempos_por_palavra")),
    ("completo", "Completo",
     ("transcrever", "separar_falantes", "tempos_por_palavra",
      "busca_semantica", "glossario_nomes", "resumo_perguntar")),
)


@dataclass(frozen=True)
class Hardware:
    """Retrato da maquina. Campos None = nao foi possivel medir."""
    has_gpu: bool = False
    vram_gb: float | None = None
    ram_gb: float | None = None
    cores: int = 1
    free_disk_gb: float | None = None


def capability(key: str) -> Capability:
    for item in CAPABILITIES:
        if item.key == key:
            return item
    raise KeyError(f"Capacidade desconhecida: {key}")


def capability_for_model(model_key: str) -> Capability | None:
    """Capacidade habilitada por este modelo; None se nenhuma (pura).

    Liga a chave de um modelo opcional (llm_qwen, ner_gliner, ...) aos
    requisitos declarados no registro — e o que permite a uma oferta de
    download dizer o requisito de hardware ANTES do usuario aceitar.
    """
    for item in CAPABILITIES:
        if model_key in item.models:
            return item
    return None


def parse_fake_hardware(spec: str | None) -> Hardware | None:
    """Retrato SIMULADO a partir do valor do env de teste (pura).

    "cpu" = maquina comum sem placa; "minimo" = maquina apertada;
    "gpuN" = placa NVIDIA com N GB de video. None = nao simular.
    """
    valor = (spec or "").strip().lower()
    if not valor:
        return None
    if valor == "cpu":
        return Hardware(has_gpu=False, vram_gb=None, ram_gb=8.0, cores=4, free_disk_gb=50.0)
    if valor == "minimo":
        return Hardware(has_gpu=False, vram_gb=None, ram_gb=4.0, cores=2, free_disk_gb=12.0)
    if valor.startswith("gpu"):
        try:
            vram = float(valor[3:] or 8)
        except ValueError:
            return None
        return Hardware(has_gpu=True, vram_gb=vram, ram_gb=16.0, cores=8, free_disk_gb=100.0)
    return None


def hardware_snapshot() -> Hardware:
    """Retrato da maquina; degrada em silencio quando a sonda falha."""
    from . import runtime

    fake = parse_fake_hardware(os.environ.get(FAKE_HARDWARE_ENV))
    if fake is not None:
        return fake
    return Hardware(
        has_gpu=runtime.has_nvidia_gpu(),
        vram_gb=runtime.total_vram_gb(),
        ram_gb=runtime.total_ram_gb(),
        cores=runtime.cpu_cores(),
        free_disk_gb=runtime.free_disk_gb(),
    )


def describe_hardware(hw: Hardware) -> str:
    """Uma linha em linguagem de gente, para o assistente (pura)."""
    partes = []
    if hw.has_gpu:
        partes.append(f"placa NVIDIA com {hw.vram_gb:.0f} GB" if hw.vram_gb
                      else "placa NVIDIA")
    else:
        partes.append("sem placa NVIDIA")
    partes.append(f"{hw.cores} núcleos de CPU")
    if hw.ram_gb:
        partes.append(f"{hw.ram_gb:.0f} GB de memória")
    if hw.free_disk_gb:
        partes.append(f"{hw.free_disk_gb:.0f} GB livres em disco")
    return ", ".join(partes)


def hardware_blocker(cap: Capability, hw: Hardware) -> str:
    """Motivo pelo qual a maquina NAO RODA a capacidade; "" se roda (pura).

    Bloqueio DURO apenas: sem placa NVIDIA quando a capacidade exige.
    VRAM abaixo do minimo NAO bloqueia — vira hardware_warning
    ("recomendar, nunca decidir": o usuario tenta por conta e risco;
    barrar por VRAM desligava o resumo de quem ja tinha o modelo
    baixado numa placa de 4 GB — regressao corrigida em 2026-08-30).
    Vram desconhecida tambem nao bloqueia: preferimos deixar tentar a
    barrar por uma sonda que falhou.
    """
    if cap.needs_gpu and not hw.has_gpu:
        return ("precisa de uma placa de vídeo NVIDIA; este computador "
                "não tem uma disponível")
    return ""


def hardware_warning(cap: Capability, hw: Hardware) -> str:
    """Aviso de "roda, mas por conta e risco"; "" sem ressalvas (pura).

    So se aplica quando a maquina RODA (ha placa): abaixo do minimo de
    VRAM o recurso pode falhar ao carregar ou ficar lento — o usuario
    decide, avisado.
    """
    if (cap.min_vram_gb and hw.has_gpu and hw.vram_gb is not None
            and hw.vram_gb < cap.min_vram_gb):
        return (f"recomenda cerca de {cap.min_vram_gb:.0f} GB de memória de vídeo; "
                f"esta placa tem {hw.vram_gb:.0f} GB — pode falhar ou ficar lento")
    return ""


def capability_status(
    cap: Capability,
    hw: Hardware,
    cached_models: Iterable[str],
    model_sizes: dict[str, float] | None = None,
) -> tuple[str, str, float]:
    """(estado, motivo, GB a baixar) — o predicado unico do app (pura).

    estado: "pronta" | "instalavel" | "incompativel".
    "incompativel" nao significa proibida: significa que a maquina nao
    deve dar conta, e o usuario decide assumindo o risco.
    """
    bloqueio = hardware_blocker(cap, hw)
    faltando = [m for m in cap.models if m not in set(cached_models)]
    tamanho = sum((model_sizes or {}).get(m, 0.0) for m in faltando)
    if bloqueio:
        return "incompativel", f"{cap.label} {bloqueio}.", tamanho
    if faltando:
        return "instalavel", f"{cap.label}: falta baixar o modelo.", round(tamanho, 2)
    return "pronta", "", 0.0


def recommended_asr_variant(hw: Hardware) -> str:
    """Variante do Whisper recomendada para a maquina (pura).

    Em GPU o turbo e o melhor custo-beneficio. Em CPU, modelos grandes
    levam horas por entrevista: small equilibra qualidade e tempo; em
    maquina apertada, base. Sugestao, nunca imposicao.
    """
    if hw.has_gpu and (hw.vram_gb is None or hw.vram_gb >= 4.0):
        return "large-v3-turbo"
    if (hw.ram_gb is not None and hw.ram_gb < 8) or hw.cores <= 2:
        return "base"
    return "small"


def recommended_profile(hw: Hardware) -> str:
    """Perfil sugerido para a maquina (pura). Sugestao, nunca imposicao."""
    if hw.has_gpu and (hw.vram_gb is None or hw.vram_gb >= 6.0):
        return "completo"
    # Sem placa util: separar falantes e alinhar rodam em CPU, so mais
    # devagar — vale a pena, exceto em maquina claramente apertada.
    if (hw.ram_gb is not None and hw.ram_gb < 8) or hw.cores <= 2:
        return "essencial"
    return "padrao"


def profile_capabilities(profile_key: str) -> tuple[str, ...]:
    for key, _label, caps in PROFILES:
        if key == profile_key:
            return caps
    raise KeyError(f"Perfil desconhecido: {profile_key}")


def profile_size(
    profile_or_keys: str | Iterable[str],
    model_sizes: dict[str, float],
    cached_models: Iterable[str] = (),
) -> float:
    """GB a baixar para habilitar as capacidades dadas (pura)."""
    keys = (profile_capabilities(profile_or_keys)
            if isinstance(profile_or_keys, str) else tuple(profile_or_keys))
    ja_tem = set(cached_models)
    necessarios: set[str] = set()
    for key in keys:
        necessarios.update(m for m in capability(key).models if m not in ja_tem)
    return round(sum(model_sizes.get(m, 0.0) for m in necessarios), 2)


def cpu_speed_warning(hw: Hardware) -> str:
    """Aviso honesto de lentidao em CPU; "" quando ha GPU (pura).

    Ordem de grandeza, nao promessa: em CPU o Whisper roda perto do
    tempo real por nucleo util, entao uma entrevista de 1 h leva de
    dezenas de minutos a algumas horas.
    """
    if hw.has_gpu:
        return ""
    if hw.cores >= 8:
        return "Sem placa de vídeo, 1 hora de entrevista deve levar de 30 a 60 minutos."
    if hw.cores >= 4:
        return "Sem placa de vídeo, 1 hora de entrevista deve levar de 1 a 2 horas."
    return ("Sem placa de vídeo e com poucos núcleos, 1 hora de entrevista pode "
            "levar várias horas — prefira o modelo menor.")


def model_sizes_from_registry(asr_variant: str | None = None) -> dict[str, float]:
    """{chave do modelo: GB} a partir do registro (I/O leve, sem rede)."""
    from . import model_manager

    tamanhos: dict[str, float] = {}
    for asset in model_manager._FIXED_MODELS + model_manager._OPTIONAL_MODELS:
        tamanhos[asset.key] = float(asset.estimated_gb)
    variante = asr_variant or model_manager.DEFAULT_ASR_VARIANT
    info: dict[str, Any] = model_manager.ASR_VARIANTS.get(variante, {})
    tamanhos[ASR_MODEL_TOKEN] = float(info.get("estimated_gb", 0.0))
    return tamanhos


def cached_model_keys(asr_variant: str | None = None) -> set[str]:
    """Chaves de modelo ja presentes no cache (I/O de disco, sem rede)."""
    from . import model_manager, runtime

    cache = runtime.model_cache_dir()
    presentes: set[str] = set()
    for asset in model_manager._FIXED_MODELS + model_manager._OPTIONAL_MODELS:
        try:
            if model_manager.optional_model_cached(asset, cache):
                presentes.add(asset.key)
        except Exception:  # noqa: BLE001 - cache ilegivel = tratar como ausente
            continue
    variante = asr_variant or model_manager.DEFAULT_ASR_VARIANT
    try:
        if variante in model_manager.installed_asr_variants(cache):
            presentes.add(ASR_MODEL_TOKEN)
    except Exception:  # noqa: BLE001
        pass
    return presentes
