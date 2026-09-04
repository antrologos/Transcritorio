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
import sys
from dataclasses import dataclass
from typing import Iterable

ASR_MODEL_TOKEN = "@asr"  # resolvido para a variante escolhida no projeto
ALIGN_MODEL_TOKEN = "@align"  # resolvido para o pacote do IDIOMA configurado (etapa 4)

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
        # Etapa 4: o modelo e FUNCAO do idioma configurado (@align),
        # como o @asr e funcao da variante escolhida.
        models=(ALIGN_MODEL_TOKEN,),
    ),
    Capability(
        "busca_semantica",
        "Busca por sentido",
        "encontra trechos pelo significado, sem as palavras exatas",
        models=("search_encoder",),
    ),
    # v3 (2026-09-03): tier de qualidade — encoder maior + reordenador que
    # le pergunta e trecho juntos. Roda em CPU (mais devagar); com placa de
    # video fica imediato. Instalado => aplicado (search.active_encoder).
    Capability(
        "busca_qualidade",
        "Busca por sentido — qualidade",
        "trechos mais certeiros, reordenados pela leitura de pergunta e trecho juntos",
        models=("search_encoder_hq", "search_reranker"),
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
      "busca_semantica", "busca_qualidade", "glossario_nomes", "resumo_perguntar")),
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


def recommended_asr_variants(hw: Hardware, platform: str | None = None) -> tuple[str, ...]:
    """Variantes recomendadas para a maquina, a primaria primeiro (pura).

    O TAGARELA (parakeet-pt) e o PADRAO em TODAS as maquinas (decisao do
    usuario 2026-09-02; antes so sem GPU): pt-BR espontaneo com WER 14 vs
    23 do large-v3, pontuacao e tempos por palavra nativos, 13-25x o tempo
    real em CPU — mais rapido ate que o turbo em GPU (~8x). Como so
    transcreve portugues, um Whisper acompanha como RESERVA para outros
    idiomas: large-v3-turbo onde ha GPU util (qualidade), small no resto
    (no Mac o small roda pela rota Metal/MLX). `platform` fica por
    compatibilidade dos toys. Sugestao, nunca imposicao.
    """
    _ = platform or sys.platform
    if hw.has_gpu and (hw.vram_gb is None or hw.vram_gb >= 4.0):
        return ("parakeet-pt", "large-v3-turbo")
    return ("parakeet-pt", "small")


def recommended_asr_variant(hw: Hardware) -> str:
    """Variante PRIMARIA recomendada (pura); ver recommended_asr_variants."""
    return recommended_asr_variants(hw)[0]


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
    """Aviso honesto de tempo em CPU; "" quando ha GPU (pura).

    Ordem de grandeza, nao promessa, com o TAGARELA como motor (poucos
    minutos por hora de audio em qualquer CPU) e a separacao de falantes
    escalando com os nucleos (0,06x na maquina de 24 nucleos).
    """
    if hw.has_gpu:
        return ""
    # Mesma conta que a barra de progresso e a janela do lote usam
    # (expected_diarization_seconds), para os textos nunca divergirem.
    separacao = describe_seconds(expected_diarization_seconds(3600.0, "cpu", hw.cores))
    if hw.cores >= 4:
        return ("Sem placa de vídeo, 1 hora de entrevista leva poucos minutos para "
                f"transcrever e cerca de {separacao} para separar os falantes.")
    return ("Sem placa de vídeo e com poucos núcleos, transcrever é rápido, mas "
            f"separar os falantes de 1 hora de entrevista leva cerca de {separacao} "
            "— desmarque \"Separar falantes agora\" se tiver pressa.")


# --- Estimativa de tempo de um lote (2026-09-02, puras) ---------------------
# Segundos de maquina por segundo de audio, medidos na maquina de
# referencia (os.cpu_count() = 24 nucleos logicos, RTX 4060): TAGARELA
# 16,5x tempo real em CPU (26,4 h em 96 min) e ~62x em GPU; Whisper small
# 1,1x em CPU; turbo 5-10 min por hora em GPU. Diarizacao (rede de
# embeddings 1x por janela + passo de 2 s, A/B 2026-09-02): 0,060x em CPU
# (323 min de audio em 19,3 min; era 0,40x) e 0,0063x em GPU (62 min em
# 23 s na RTX 4060). Escala por 24/cpu_count() em CPU.
_REF_LOGICAL_CORES = 24
_ASR_SECONDS_PER_AUDIO_SECOND = {
    ("parakeet_onnx", "cpu"): 1 / 16.5,
    ("parakeet_onnx", "cuda"): 1 / 62.0,
    ("whisper", "cpu"): 1.1,
    ("whisper", "cuda"): 1 / 8.0,
}


def expected_diarization_seconds(audio_seconds: float, device: str, cores: int) -> float:
    """Tempo esperado da separacao de falantes: carga do modelo + processamento."""
    audio = max(0.0, float(audio_seconds))
    if device == "cuda":
        return 20.0 + 0.0065 * audio
    escala = _REF_LOGICAL_CORES / max(1, int(cores or 1))
    return 45.0 + 0.06 * audio * escala


def batch_time_estimate(
    total_audio_s: float,
    engine: str | None,
    device: str,
    cores: int,
    asr_device: str | None = None,
) -> tuple[float, float]:
    """(segundos de transcricao, segundos de separacao de falantes) do lote.

    `asr_device` e o device REAL da transcricao quando difere do da maquina:
    o TAGARELA numa maquina CUDA roda em CPU ate o pacote onnx-gpu ser
    instalado (parakeet_runner.planned_device); a separacao usa `device`.
    """
    audio = max(0.0, float(total_audio_s))
    if audio == 0:
        return 0.0, 0.0
    dev = "cuda" if device == "cuda" else "cpu"
    dev_asr = "cuda" if (asr_device or device) == "cuda" else "cpu"
    eng = "parakeet_onnx" if engine == "parakeet_onnx" else "whisper"
    asr = audio * _ASR_SECONDS_PER_AUDIO_SECOND[(eng, dev_asr)]
    if dev_asr == "cpu" and eng == "whisper":
        asr *= _REF_LOGICAL_CORES / max(1, int(cores or 1))
    return asr, expected_diarization_seconds(audio, dev, cores)


def describe_seconds(seconds: float) -> str:
    """Duracao em linguagem de gente: "menos de 1 min", "4 min", "1 h 10 min"."""
    seconds = max(0.0, float(seconds))
    mins = int(round(seconds / 60))
    if mins < 1:
        return "menos de 1 min"
    if mins < 60:
        return f"{mins} min"
    horas, resto = divmod(mins, 60)
    return f"{horas} h {resto:02d} min" if resto else f"{horas} h"


def model_sizes_from_registry(asr_variant: str | Iterable[str] | None = None,
                              align_language: str | None = None) -> dict[str, float]:
    """{chave do modelo: GB} a partir do registro (I/O leve, sem rede).

    asr_variant aceita uma variante ou uma sequencia (recomendacao
    plural do wizard): o token @asr vira a SOMA dos tamanhos — em CPU o
    Essencial baixa TAGARELA + small.
    align_language (etapa 4): resolve o @align para o idioma configurado
    do projeto; sem pacote (auto/idioma exotico) o tamanho e 0.0 — nao
    ha download que habilite tempos por palavra nesse caso."""
    from . import model_manager

    tamanhos: dict[str, float] = {}
    for asset in model_manager._FIXED_MODELS + model_manager._OPTIONAL_MODELS:
        tamanhos[asset.key] = float(asset.estimated_gb)
    if asr_variant is None or isinstance(asr_variant, str):
        variantes: tuple[str, ...] = (asr_variant or model_manager.DEFAULT_ASR_VARIANT,)
    else:
        variantes = tuple(asr_variant) or (model_manager.DEFAULT_ASR_VARIANT,)
    tamanhos[ASR_MODEL_TOKEN] = round(sum(
        float((model_manager.ASR_VARIANTS.get(v) or {}).get("estimated_gb", 0.0))
        for v in variantes), 2)
    idioma = model_manager.normalize_language(align_language) or "pt"
    if model_manager.align_language_supported(idioma):
        tamanhos[ALIGN_MODEL_TOKEN] = float(
            model_manager.align_asset_for(idioma).estimated_gb)
    else:
        tamanhos[ALIGN_MODEL_TOKEN] = 0.0
    return tamanhos


def cached_model_keys(asr_variant: str | None = None,
                      align_language: str | None = None) -> set[str]:
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
    # Aceita a dupla (TAGARELA + reserva): @asr so conta como presente quando
    # TODAS as variantes estao instaladas (revisao 2026-09-02).
    if asr_variant is None or isinstance(asr_variant, str):
        variantes: tuple[str, ...] = (asr_variant or model_manager.DEFAULT_ASR_VARIANT,)
    else:
        variantes = tuple(asr_variant) or (model_manager.DEFAULT_ASR_VARIANT,)
    try:
        instaladas = set(model_manager.installed_asr_variants(cache))
        if all(v in instaladas for v in variantes):
            presentes.add(ASR_MODEL_TOKEN)
    except Exception:  # noqa: BLE001
        pass
    idioma = model_manager.normalize_language(align_language) or "pt"
    try:
        if model_manager.align_language_supported(idioma):
            asset = model_manager.align_asset_for(idioma)
            snap = model_manager.cached_snapshot_path(
                asset.repo_id, cache, revision=asset.revision)
            if snap is not None and model_manager._snapshot_has_weights(snap):
                presentes.add(ALIGN_MODEL_TOKEN)
    except Exception:  # noqa: BLE001
        pass
    return presentes
