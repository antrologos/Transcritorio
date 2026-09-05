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

    Diz as DUAS etapas com numero. Ate 2026-09-05 este texto so falava da
    separacao de falantes, apresentada como a demorada, e sugeria desmarcar
    "Separar falantes agora" a quem tivesse pressa. A medicao num notebook de
    4 nucleos derrubou as duas coisas: transcrever 1 h leva ~7,5 min e separar
    os falantes ~6 min. A separacao e a metade BARATA em qualquer contagem de
    nucleos, entao o conselho antigo mandava adiar o que menos pesa.
    """
    if hw.has_gpu:
        return ""
    # Mesma conta da barra e da janela do lote, para os textos nunca divergirem.
    transcricao, separacao = batch_time_estimate(3600.0, "parakeet_onnx", "cpu", hw.cores)
    texto = ("Sem placa de vídeo, 1 hora de entrevista leva cerca de "
             f"{describe_seconds(transcricao)} para transcrever e "
             f"{describe_seconds(separacao)} para separar os falantes.")
    if hw.cores < 4:
        texto += (" Com poucos núcleos é mais devagar do que isso — um lote grande "
                  "dá para deixar rodando e voltar depois.")
    return texto


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
# Como o TAGARELA escala com nucleos. A primeira medicao (2026-09-04) so
# limitou o NUMERO DE THREADS nesta maquina de 24 nucleos: 1 thread 0,207;
# 2 threads 0,107; 4 threads 0,093; 8 threads 0,080; 24 (referencia) 0,061 —
# curva de expoente ~0,25. Mas limitar threads nao simula um notebook: as 4
# threads continuavam com a banda de memoria e os caches de uma estacao de
# trabalho, e o TAGARELA e limitado justamente por banda (varre 2,4 GB de pesos
# a cada passada).
#
# Refeito em 2026-09-05 com AFINIDADE de processo (4 nucleos fisicos de
# verdade) e entrevistas inteiras do acervo: 0,126 s por segundo de audio, e
# nao os 0,093 de antes — 35% mais lento. Contra a referencia de 24 threads
# isso da (24/4)^p = 0,126/0,0606, ou seja p = 0,41. A curva por afinidade,
# em 4 min de audio: 1 thread 0,271; 2 threads 0,172; 3 threads 0,149;
# 4 threads 0,138. Continua otimista para um notebook real (a banda de memoria
# desta maquina e maior), e por isso `measured_ratio` manda quando ha
# historico.
_PARAKEET_CORE_EXPONENT = 0.4
# A separacao de falantes TAMBEM satura, e a formula supunha escala LINEAR
# (24/cores) — errava por quase 2x, para o lado pessimista. Medido em
# 2026-09-05, 4 nucleos fisicos, entrevistas inteiras, com o modelo ja
# carregado (que e o que o servidor de lote faz): 0,100 s por segundo de audio,
# contra os 0,36 que a escala linear previa. (24/4)^p = 0,100/0,060 da p = 0,29.
# Curva por afinidade em 4 min: 1 thread 0,273; 2 threads 0,176; 3 threads
# 0,142; 4 threads 0,123.
#
# A consequencia dessa correcao nao e cosmetica: com a escala linear o app
# dizia que separar falantes era 79% do tempo do lote, quando sao 44% — e
# aconselhava adiar justamente a etapa BARATA.
_DIARIZATION_CORE_EXPONENT = 0.3
# Numero minimo de transcricoes ja feitas para preferir a MEDICAO desta
# maquina a qualquer formula (ver measured_asr_ratio).
MIN_SAMPLES_FOR_MEASURED = 2


def cpu_budget(mode: str, cores: int, *, concurrent: bool = False) -> tuple[int, int]:
    """Quantas threads para transcrever e para separar falantes (pura).

    `(0, 0)` e o SENTINELA "nao mexer": nenhum SessionOptions, nenhuma variavel
    de ambiente, nenhum set_num_threads — o caminho de hoje, identico por
    construcao e nao por teste. So acontece no modo "tudo" sem sobreposicao,
    que e o padrao; quem nunca abrir a opcao nao ve diferenca nenhuma.

    Com sobreposicao NUNCA devolve 0. Medido em 2026-09-05: dois motores com o
    pool dimensionado pela maquina inteira, disputando 4 nucleos, custam 5,9x o
    de um pool do tamanho certo (0,815 contra 0,138 s por segundo de audio).
    Sobrepor sem orcamento explicito seria pior que o sequencial de hoje.

    A divisao ao meio na sobreposicao vem da medicao: as duas etapas custam a
    mesma ordem de grandeza, e dar mais nucleos a uma so faz a outra virar
    gargalo (3/1 e 1/3 ficaram 30% PIORES que o sequencial).
    """
    n = max(1, int(cores or 1))
    metade = max(1, n // 2)
    if not concurrent:
        return (metade, metade) if mode == "metade" else (0, 0)
    if mode == "metade":
        quarto = max(1, n // 4)
        return (quarto, quarto)
    return (metade, max(1, n - metade))


def should_overlap(hw: Hardware, device: str) -> tuple[bool, str]:
    """Vale sobrepor transcricao e separacao de falantes? (motivo junto, pura)

    Medido em 2026-09-05 num notebook de 4 nucleos: sobrepor corta 10,8% do
    relogio do lote (706,9 s -> 630,7 s em 53 min de audio, duas repeticoes
    intercaladas), com a saida identica — transcricao byte a byte, separacao
    com DER 0,000%.

    Os "nao" tem motivo medido, nao cautela:
    - com placa, as duas etapas ja sao rapidas E disputariam a MESMA placa;
      os pesos do pipeline em CUDA (70 contra 25) dizem que nao ha o que ganhar;
    - com 2 nucleos ou menos, cada etapa ficaria com uma thread: o ASR sozinho
      cai de 0,172 para 0,271 s por segundo de audio, e a margem some;
    - com menos de 8 GB, as duas juntas arriscam paginar — e paginar e o pior
      desfecho possivel aqui, porque o ASR ja e limitado por banda de memoria.
      Um lote que pagina fica MAIS LENTO que o sequencial: a melhoria viraria
      prejuizo.
    """
    if device == "cuda":
        return False, "com placa de vídeo as duas etapas já são rápidas e disputariam a mesma placa"
    if int(hw.cores or 1) <= 2:
        return False, "com poucos núcleos, dividir a máquina deixaria as duas etapas lentas"
    if hw.ram_gb is not None and float(hw.ram_gb) < 8.0:
        return False, "a memória não comporta as duas etapas ao mesmo tempo"
    return True, ""


def thread_env(threads: int) -> dict[str, str]:
    """Variaveis que amarram os pools de BLAS/OpenMP num processo filho (pura).

    Vazio quando nao ha orcamento — o filho fica como hoje. Precisam existir
    ANTES de o filho importar torch, por isso ambiente e nao chamada de funcao.
    Quem compoe isto sobre `secure_subprocess_env()` faz a composicao LOCAL:
    aquela funcao e o funil de todo subprocesso (ffmpeg, ffprobe, canais, LLM)
    e um limite la dentro reafinaria todos em silencio.
    """
    if not threads or int(threads) <= 0:
        return {}
    valor = str(int(threads))
    return {
        "OMP_NUM_THREADS": valor,
        "MKL_NUM_THREADS": valor,
        "OPENBLAS_NUM_THREADS": valor,
        "NUMEXPR_NUM_THREADS": valor,
    }


def expected_diarization_seconds(audio_seconds: float, device: str, cores: int) -> float:
    """Tempo esperado da separacao de falantes: carga do modelo + processamento."""
    audio = max(0.0, float(audio_seconds))
    if device == "cuda":
        return 20.0 + 0.0065 * audio
    escala = (_REF_LOGICAL_CORES / max(1, int(cores or 1))) ** _DIARIZATION_CORE_EXPONENT
    return 45.0 + 0.06 * audio * escala


def measured_ratio(samples: list[tuple[float, float]],
                   minimo: int = MIN_SAMPLES_FOR_MEASURED) -> float | None:
    """Segundos de maquina por segundo de audio, MEDIDOS nesta maquina (puro).

    `samples` = [(segundos de audio, segundos que a etapa levou)] do historico
    do proprio projeto. Devolve a MEDIANA (um arquivo atipico nao contamina) ou
    None quando ha amostra de menos.

    Existe porque nenhuma formula da conta: a correcao por numero de nucleos
    nao enxerga a velocidade de cada nucleo. Um caso real (2026-09-04): um
    i7-10610U de 15 W com 8 threads logicos entregou 0,46 s por segundo de
    audio, contra 0,08 previstos pela contagem de nucleos — 5,7x de diferenca,
    toda ela em clock, IPC e reducao termica. Depois da primeira transcricao a
    maquina ja disse a verdade sobre si mesma; a formula so vale antes disso."""
    razoes = sorted(
        elapsed / audio for audio, elapsed in (samples or [])
        if audio and audio > 0 and elapsed and elapsed > 0
    )
    if len(razoes) < max(1, int(minimo)):
        return None
    meio = len(razoes) // 2
    return razoes[meio] if len(razoes) % 2 else (razoes[meio - 1] + razoes[meio]) / 2.0


def batch_time_estimate(
    total_audio_s: float,
    engine: str | None,
    device: str,
    cores: int,
    asr_device: str | None = None,
    asr_samples: list[tuple[float, float]] | None = None,
    diar_samples: list[tuple[float, float]] | None = None,
) -> tuple[float, float]:
    """(segundos de transcricao, segundos de separacao de falantes) do lote.

    `asr_device` e o device REAL da transcricao quando difere do da maquina:
    o TAGARELA numa maquina CUDA roda em CPU ate o pacote onnx-gpu ser
    instalado (parakeet_runner.planned_device); a separacao usa `device`.

    `asr_samples`/`diar_samples` sao o historico [(audio_s, levou_s)] DESTA
    maquina: quando ha o bastante, mandam na estimativa (ver measured_ratio).
    """
    audio = max(0.0, float(total_audio_s))
    if audio == 0:
        return 0.0, 0.0
    dev = "cuda" if device == "cuda" else "cpu"
    dev_asr = "cuda" if (asr_device or device) == "cuda" else "cpu"
    eng = "parakeet_onnx" if engine == "parakeet_onnx" else "whisper"
    medido = measured_ratio(asr_samples or [])
    if medido is not None:
        asr = audio * medido
    else:
        asr = audio * _ASR_SECONDS_PER_AUDIO_SECOND[(eng, dev_asr)]
        if dev_asr == "cpu":
            escala = _REF_LOGICAL_CORES / max(1, int(cores or 1))
            # Whisper escala quase linear com nucleos; o TAGARELA satura.
            asr *= escala if eng == "whisper" else escala ** _PARAKEET_CORE_EXPONENT
    diar_medido = measured_ratio(diar_samples or [])
    diar = (45.0 + audio * diar_medido) if diar_medido is not None \
        else expected_diarization_seconds(audio, dev, cores)
    return asr, diar


def estimate_is_measured(samples: list[tuple[float, float]] | None) -> bool:
    """A estimativa veio da MEDICAO desta maquina? (puro) — a janela diz isso
    ao usuario, porque "≈ 4 h" com base no historico e outra coisa que "≈ 4 h"
    com base numa tabela de outra maquina."""
    return measured_ratio(samples or []) is not None


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
