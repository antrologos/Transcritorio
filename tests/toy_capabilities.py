"""Toy test: registro de capacidades (etapa 1) — decisao pura.

Roda no CI minimo: nada de Qt, torch ou rede. As funcoes de decisao
recebem o retrato do hardware e o cache; as sondas reais ficam fora.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import capabilities as cap

# Maquinas sinteticas
FRACA = cap.Hardware(has_gpu=False, vram_gb=None, ram_gb=8.0, cores=4, free_disk_gb=40.0)
MINIMA = cap.Hardware(has_gpu=False, vram_gb=None, ram_gb=4.0, cores=2, free_disk_gb=12.0)
GPU_PEQUENA = cap.Hardware(has_gpu=True, vram_gb=2.0, ram_gb=16.0, cores=8, free_disk_gb=90.0)
GPU_BOA = cap.Hardware(has_gpu=True, vram_gb=8.0, ram_gb=32.0, cores=16, free_disk_gb=400.0)

TAMANHOS = {cap.ASR_MODEL_TOKEN: 3.1, "alignment_pt": 1.4, "diarization": 0.07,
            "search_encoder": 0.5, "ner_gliner": 1.1, "llm_qwen": 8.7}

# --- registro coerente ---
assert cap.capability("transcrever").essential is True
assert len({c.key for c in cap.CAPABILITIES}) == len(cap.CAPABILITIES)
for _k, _label, chaves in cap.PROFILES:
    for chave in chaves:
        cap.capability(chave)          # levanta se o perfil citar chave inexistente
try:
    cap.capability("nao_existe")
    raise AssertionError("deveria ter levantado")
except KeyError:
    pass
print("PASS: registro")

# --- hardware_blocker: so a maquina bloqueia, e vram desconhecida nao bloqueia ---
llm = cap.capability("resumo_perguntar")
assert cap.hardware_blocker(llm, FRACA)                     # sem GPU
assert "2 GB" in cap.hardware_blocker(llm, GPU_PEQUENA)     # GPU pequena demais
assert cap.hardware_blocker(llm, GPU_BOA) == ""
assert cap.hardware_blocker(llm, cap.Hardware(has_gpu=True, vram_gb=None)) == "", \
    "sonda de VRAM falhou: deve deixar tentar, nao barrar"
assert cap.hardware_blocker(cap.capability("transcrever"), MINIMA) == ""
print("PASS: hardware_blocker")

# --- capability_status: os quatro cenarios ---
estado, motivo, gb = cap.capability_status(
    cap.capability("transcrever"), FRACA, {cap.ASR_MODEL_TOKEN}, TAMANHOS)
assert (estado, gb) == ("pronta", 0.0) and motivo == ""

estado, motivo, gb = cap.capability_status(
    cap.capability("busca_semantica"), FRACA, set(), TAMANHOS)
assert estado == "instalavel" and gb == 0.5 and "falta baixar" in motivo

estado, motivo, gb = cap.capability_status(llm, FRACA, set(), TAMANHOS)
assert estado == "incompativel" and "NVIDIA" in motivo

# incompativel por VRAM tem prioridade sobre "falta baixar": nao adianta
# oferecer 8,7 GB para uma placa que nao roda o modelo
estado, motivo, _gb = cap.capability_status(llm, GPU_PEQUENA, set(), TAMANHOS)
assert estado == "incompativel" and "memória de vídeo" in motivo

# ja em cache + GPU boa
estado, _motivo, gb = cap.capability_status(llm, GPU_BOA, {"llm_qwen"}, TAMANHOS)
assert (estado, gb) == ("pronta", 0.0)
print("PASS: capability_status")

# --- recommended_profile: recomenda, nao decide ---
assert cap.recommended_profile(GPU_BOA) == "completo"
assert cap.recommended_profile(FRACA) == "padrao"
assert cap.recommended_profile(MINIMA) == "essencial"
assert cap.recommended_profile(GPU_PEQUENA) == "padrao"      # GPU de 2 GB nao vale LLM
# GPU presente com sonda de VRAM falhando: nao rebaixar por causa da sonda
assert cap.recommended_profile(cap.Hardware(has_gpu=True, cores=8, ram_gb=16.0)) == "completo"
print("PASS: recommended_profile")

# --- profile_size: soma o que FALTA, sem duplicar modelo compartilhado ---
assert cap.profile_size("essencial", TAMANHOS) == 3.1
assert cap.profile_size("padrao", TAMANHOS) == round(3.1 + 0.07 + 1.4, 2)
assert cap.profile_size("completo", TAMANHOS) == round(sum(TAMANHOS.values()), 2)
# o que ja esta em cache nao volta a contar
assert cap.profile_size("padrao", TAMANHOS, cached_models={cap.ASR_MODEL_TOKEN}) == 1.47
assert cap.profile_size(["glossario_nomes", "busca_semantica"], TAMANHOS) == 1.6
print("PASS: profile_size")

# --- textos de apoio ---
assert "sem placa NVIDIA" in cap.describe_hardware(FRACA)
assert "8 GB" in cap.describe_hardware(GPU_BOA)
assert cap.cpu_speed_warning(GPU_BOA) == ""
assert "30 a 60 minutos" in cap.cpu_speed_warning(
    cap.Hardware(has_gpu=False, cores=16))
assert "várias horas" in cap.cpu_speed_warning(MINIMA)
print("PASS: textos de apoio")

# --- integracao com o registro real de modelos (sem rede) ---
try:
    tamanhos = cap.model_sizes_from_registry()
    assert tamanhos[cap.ASR_MODEL_TOKEN] > 0
    for chave in ("alignment_pt", "diarization", "llm_qwen", "ner_gliner", "search_encoder"):
        assert chave in tamanhos, f"{chave} ausente no registro"
    # todo modelo citado por uma capacidade precisa existir no registro
    for item in cap.CAPABILITIES:
        for modelo in item.models:
            assert modelo in tamanhos, f"{item.key} cita modelo inexistente: {modelo}"
    print("PASS: modelos das capacidades existem no registro")
except ImportError as exc:
    print(f"SKIP: registro de modelos ({exc})")

print("PASS: toy_capabilities")
