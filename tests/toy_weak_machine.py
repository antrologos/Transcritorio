"""Toy 3V (v0.2.0): maquina simples recebe recomendacoes coerentes.

Compradores de requisito: numa maquina fraca simulada
(TRANSCRITORIO_FAKE_HARDWARE), o app recomenda o perfil Essencial e o
TAGARELA como motor primario com o small de reserva (padrao sem GPU
desde 2026-09-01; nunca turbo/GPU), avisa da lentidao de CPU e o perfil
Completo (analise AI) nunca e sugerido sem GPU de 6 GB. E o que os
requisitos publicados no site/README prometem.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.capabilities import (
    Hardware,
    cpu_speed_warning,
    parse_fake_hardware,
    recommended_asr_variants,
    recommended_profile,
)

# Perfis simulaveis existem e batem com o codigo real
minimo = parse_fake_hardware("minimo")
assert minimo == Hardware(has_gpu=False, vram_gb=None, ram_gb=4.0, cores=2,
                          free_disk_gb=12.0), minimo
cpu = parse_fake_hardware("cpu")
assert cpu is not None and not cpu.has_gpu

# 2026-09-02: TAGARELA primario em TODAS as maquinas (decisao do usuario);
# a reserva Whisper para outros idiomas e o turbo com GPU util, small no
# resto (Mac inclusive — la o small roda pela rota MLX). Plataforma
# explicita para o toy valer igual nos 3 SOs do CI.
assert recommended_profile(minimo) == "essencial"
assert recommended_asr_variants(minimo, platform="win32") == ("parakeet-pt", "small")
assert recommended_asr_variants(minimo, platform="linux") == ("parakeet-pt", "small")

# Maquina CPU comum (8 GB / 4 nucleos): Padrao + TAGARELA/small
assert recommended_profile(cpu) == "padrao"
assert recommended_asr_variants(cpu, platform="win32") == ("parakeet-pt", "small")

# Mac: TAGARELA primario tambem; small (MLX) de reserva
assert recommended_asr_variants(cpu, platform="darwin") == ("parakeet-pt", "small")

# GPU 6 GB+: Completo + TAGARELA primario com o turbo de reserva
gpu = parse_fake_hardware("gpu8")
assert recommended_profile(gpu) == "completo"
assert recommended_asr_variants(gpu, platform="win32") == ("parakeet-pt", "large-v3-turbo")
assert recommended_asr_variants(gpu, platform="linux") == ("parakeet-pt", "large-v3-turbo")
assert recommended_asr_variants(gpu, platform="darwin")[0] == "parakeet-pt"

# GPU fraca (< 6 GB) NAO ganha o Completo (analise AI pede 6 GB VRAM)
gpu_fraca = parse_fake_hardware("gpu4")
assert recommended_profile(gpu_fraca) != "completo", \
    recommended_profile(gpu_fraca)

# O tamanho estimado do @asr soma a dupla (Essencial-CPU ~3,45 GB)
from transcribe_pipeline.capabilities import model_sizes_from_registry
assert model_sizes_from_registry(("parakeet-pt", "small"))["@asr"] == 3.45
assert model_sizes_from_registry("small")["@asr"] == 0.9  # singular intacto

# Aviso de lentidao em CPU existe e fala com gente; com GPU, silencio
aviso = cpu_speed_warning(minimo)
assert aviso and isinstance(aviso, str), aviso
assert cpu_speed_warning(gpu) == ""

print("PASS: toy_weak_machine (Essencial + TAGARELA/small p/ maquina "
      "fraca; Completo so com GPU 6GB+; aviso de lentidao)")
