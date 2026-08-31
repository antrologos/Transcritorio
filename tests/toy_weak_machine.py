"""Toy 3V (v0.2.0): maquina simples recebe recomendacoes coerentes.

Compradores de requisito: numa maquina fraca simulada
(TRANSCRITORIO_FAKE_HARDWARE), o app recomenda o perfil Essencial e o
modelo small (nunca turbo/GPU), avisa da lentidao de CPU e o perfil
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
    recommended_asr_variant,
    recommended_profile,
)

# Perfis simulaveis existem e batem com o codigo real
minimo = parse_fake_hardware("minimo")
assert minimo == Hardware(has_gpu=False, vram_gb=None, ram_gb=4.0, cores=2,
                          free_disk_gb=12.0), minimo
cpu = parse_fake_hardware("cpu")
assert cpu is not None and not cpu.has_gpu

# Maquina minima (4 GB RAM / 2 nucleos): Essencial + small, nunca GPU
assert recommended_profile(minimo) == "essencial"
assert recommended_asr_variant(minimo) == "small"

# Maquina CPU comum (8 GB / 4 nucleos): Padrao + small
assert recommended_profile(cpu) == "padrao"
assert recommended_asr_variant(cpu) == "small"

# GPU 6 GB+: Completo + turbo
gpu = parse_fake_hardware("gpu8")
assert recommended_profile(gpu) == "completo"
assert recommended_asr_variant(gpu) == "large-v3-turbo"

# GPU fraca (< 6 GB) NAO ganha o Completo (analise AI pede 6 GB VRAM)
gpu_fraca = parse_fake_hardware("gpu4")
assert recommended_profile(gpu_fraca) != "completo", \
    recommended_profile(gpu_fraca)

# Aviso de lentidao em CPU existe e fala com gente; com GPU, silencio
aviso = cpu_speed_warning(minimo)
assert aviso and isinstance(aviso, str), aviso
assert cpu_speed_warning(gpu) == ""

print("PASS: toy_weak_machine (Essencial/small p/ maquina fraca; "
      "Completo so com GPU 6GB+; aviso de lentidao)")
