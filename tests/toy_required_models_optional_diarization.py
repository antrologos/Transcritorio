"""Toy test para get_required_models(include_diarization=False) e propagacao.

Fase 1.2 da migracao v0.2: diarizacao e opcional — quem so quer transcrever
nao precisa de conta HF/token/modelo pyannote gated. O asset 'diarization'
so entra na lista de obrigatorios quando include_diarization=True.

Depende apenas de model_manager ser importavel (sem torch/pyannote).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import model_manager as mm

# Default (True): alignment + diarization presentes (compatibilidade)
keys = [a.key for a in mm.get_required_models(["tiny"])]
assert "asr_tiny" in keys and "alignment_pt" in keys and "diarization" in keys, keys

# include_diarization=False: pyannote fora; alignment PERMANECE (ASR precisa)
keys = [a.key for a in mm.get_required_models(["tiny"], include_diarization=False)]
assert "diarization" not in keys, keys
assert "alignment_pt" in keys, "alignment e obrigatorio para timestamps por palavra"
assert "asr_tiny" in keys, keys

# Nenhum asset gated na lista sem diarizacao -> fluxo sem token e possivel
assert not any(a.gated for a in mm.get_required_models(["tiny"], include_diarization=False)), \
    "sem diarizacao nao pode restar modelo gated (exigiria token)"

# Propagacao: status/all_required_models_cached/has_partial_cache aceitam o flag
with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    st = mm.status(cache, asr_variants=["tiny"], include_diarization=False)
    assert all(item.asset.key != "diarization" for item in st), [i.asset.key for i in st]
    assert mm.all_required_models_cached(cache, asr_variants=["tiny"], include_diarization=False) is False
    assert mm.has_partial_cache(cache, asr_variants=["tiny"], include_diarization=False) is False

# verify_required_models com flag: conta apenas os assets selecionados
with tempfile.TemporaryDirectory() as tmp:
    import transcribe_pipeline.runtime as rt
    _orig = rt.model_cache_dir
    rt.model_cache_dir = lambda: Path(tmp)
    try:
        events: list[dict] = []
        failures = mm.verify_required_models(
            progress_callback=events.append,
            asr_variants=["tiny"],
            include_diarization=False,
        )
        # cache vazio: tiny + alignment ausentes = 2 falhas (pyannote NAO conta)
        assert failures == 2, failures
        labels = " ".join(e.get("message", "") for e in events)
        assert "Separacao de falantes" not in labels, labels
    finally:
        rt.model_cache_dir = _orig

print("PASS: toy_required_models_optional_diarization")
