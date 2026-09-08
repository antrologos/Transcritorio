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

# Etapa 4: tempos_por_palavra usa o token @align (resolvido pelo idioma
# configurado), nao mais a chave fixa "alignment_pt".
TAMANHOS = {cap.ASR_MODEL_TOKEN: 3.1, cap.ALIGN_MODEL_TOKEN: 1.4,
            "diarization": 0.07,
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

# --- hardware_blocker: so o bloqueio DURO (sem placa); VRAM baixa vira AVISO ---
# Regressao corrigida em 2026-08-30: barrar por VRAM decidia PELO usuario
# ("recomendar, nunca decidir" / "por sua conta e risco") e desligava o
# resumo de quem ja tinha o modelo baixado numa placa de 4 GB.
llm = cap.capability("resumo_perguntar")
assert cap.hardware_blocker(llm, FRACA)                     # sem GPU: bloqueio duro
assert cap.hardware_blocker(llm, GPU_PEQUENA) == ""         # GPU pequena: NAO bloqueia
assert "2 GB" in cap.hardware_warning(llm, GPU_PEQUENA)     # ...mas avisa
assert cap.hardware_warning(llm, GPU_BOA) == ""
assert cap.hardware_warning(llm, FRACA) == ""               # sem GPU o aviso nao se aplica (ja bloqueou)
assert cap.hardware_blocker(llm, GPU_BOA) == ""
assert cap.hardware_blocker(llm, cap.Hardware(has_gpu=True, vram_gb=None)) == "", \
    "sonda de VRAM falhou: deve deixar tentar, nao barrar"
assert cap.hardware_warning(llm, cap.Hardware(has_gpu=True, vram_gb=None)) == ""
assert cap.hardware_blocker(cap.capability("transcrever"), MINIMA) == ""
print("PASS: hardware_blocker + hardware_warning")

# --- capability_status: os quatro cenarios ---
estado, motivo, gb = cap.capability_status(
    cap.capability("transcrever"), FRACA, {cap.ASR_MODEL_TOKEN}, TAMANHOS)
assert (estado, gb) == ("pronta", 0.0) and motivo == ""

estado, motivo, gb = cap.capability_status(
    cap.capability("busca_semantica"), FRACA, set(), TAMANHOS)
assert estado == "instalavel" and gb == 0.5 and "falta baixar" in motivo

estado, motivo, gb = cap.capability_status(llm, FRACA, set(), TAMANHOS)
assert estado == "incompativel" and "NVIDIA" in motivo

# GPU pequena NAO e mais "incompativel": a oferta segue, com o aviso de
# VRAM entrando pelo hardware_warning (por conta e risco do usuario)
estado, _motivo, gb = cap.capability_status(llm, GPU_PEQUENA, set(), TAMANHOS)
assert estado == "instalavel" and gb == 8.7, (estado, gb)
estado, _motivo, _gb = cap.capability_status(llm, GPU_PEQUENA, {"llm_qwen"}, TAMANHOS)
assert estado == "pronta", "modelo baixado em placa pequena deve continuar utilizavel"

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
# 2026-09-05: o aviso passa a dizer as DUAS etapas com numero, e a MESMA conta
# da barra/janela do lote. Antes so falava da separacao de falantes, chamada de
# "a etapa demorada", e mandava desmarcar "Separar falantes agora" a quem
# tivesse pressa. Medido num notebook de 4 nucleos: transcrever 1 h leva
# ~7,5 min e separar ~6 min — a separacao e a metade BARATA, em qualquer
# contagem de nucleos, entao aquele conselho adiava o que menos pesa.
for _n in (2, 4, 8, 16, 24):
    _t, _d = cap.batch_time_estimate(3600.0, "parakeet_onnx", "cpu", _n)
    assert 0.6 < _d / _t < 1.4, \
        f"as duas etapas na mesma ordem em {_n} nucleos: {_t:.0f} vs {_d:.0f} s"
# Na maquina alvo do plano (4 nucleos) a transcricao e a metade MAIOR. Antes
# desta correcao a formula dava 341 s de transcricao contra 1341 s de
# separacao — 3,9x — e era com esse numero que a janela do lote aconselhava.
_t4, _d4 = cap.batch_time_estimate(3600.0, "parakeet_onnx", "cpu", 4)
assert _t4 > _d4, f"4 nucleos: {_t4:.0f} vs {_d4:.0f} s"
_aviso4 = cap.cpu_speed_warning(cap.Hardware(has_gpu=False, cores=4))
assert "transcrever" in _aviso4 and "separar os falantes" in _aviso4
assert "Separar falantes agora" not in _aviso4, "conselho derrubado pela medicao"
assert "Separar falantes agora" not in cap.cpu_speed_warning(MINIMA)
assert "deixar rodando" in cap.cpu_speed_warning(MINIMA), "poucos nucleos ganham ressalva"
assert all("Sem placa de vídeo" in cap.cpu_speed_warning(h) for h in (MINIMA, cap.Hardware(has_gpu=False, cores=8)))
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
