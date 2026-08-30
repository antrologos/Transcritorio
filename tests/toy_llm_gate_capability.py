"""Toy test: gates de clique de IA usam o registro de capacidades (SL-A).

Bug original: todos os gates de clique usavam has_nvidia_gpu() binario —
uma GPU de 2 GB recebia oferta de download de 8,7 GB sem nenhuma mencao
a VRAM, disco ou ao ambiente LLM de ~3 GB instalado depois sem aviso.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError as exc:  # pragma: no cover - CI minimo sem Qt
    print(f"SKIP: PySide6 ausente ({exc})")
    sys.exit(0)

from transcribe_pipeline import capabilities as caps
from transcribe_pipeline import model_manager
import transcribe_pipeline.review_studio_qt as rs

app = QApplication.instance() or QApplication([])

GPU8 = caps.Hardware(has_gpu=True, vram_gb=8.0, ram_gb=32.0, cores=16, free_disk_gb=99.0)
GPU2 = caps.Hardware(has_gpu=True, vram_gb=2.0, ram_gb=16.0, cores=8, free_disk_gb=99.0)
TAMANHOS = {caps.ASR_MODEL_TOKEN: 3.1, "alignment_pt": 1.4, "diarization": 0.07,
            "search_encoder": 0.5, "ner_gliner": 1.1, "llm_qwen": 8.7}

# --- capability_for_model (pura) ---
assert caps.capability_for_model("llm_qwen").key == "resumo_perguntar"
assert caps.capability_for_model("diarization").key == "separar_falantes"
assert caps.capability_for_model("nao_existe") is None
print("PASS: capability_for_model")


class _FakeQMB:
    """Grava o que seria mostrado; nunca abre janela."""
    StandardButton = QMessageBox.StandardButton
    Icon = QMessageBox.Icon
    perguntas: list[tuple[str, str]] = []
    avisos: list[tuple[str, str]] = []
    infos: list[tuple[str, str]] = []

    @staticmethod
    def question(parent, titulo, texto, *a, **k):
        _FakeQMB.perguntas.append((titulo, texto))
        return QMessageBox.StandardButton.No

    @staticmethod
    def warning(parent, titulo, texto, *a, **k):
        _FakeQMB.avisos.append((titulo, texto))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def information(parent, titulo, texto, *a, **k):
        _FakeQMB.infos.append((titulo, texto))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def _reset():
        _FakeQMB.perguntas.clear()
        _FakeQMB.avisos.clear()
        _FakeQMB.infos.clear()


class _Janela:
    _ensure_optional_model = rs.ReviewStudioWindow._ensure_optional_model
    _ensure_llm_model = rs.ReviewStudioWindow._ensure_llm_model
    _capability_state = rs.ReviewStudioWindow._capability_state

    def __init__(self, hardware, em_cache):
        self.context = None
        self._caps_cache = (hardware, set(em_cache), TAMANHOS)


_qmb_original = rs.QMessageBox
rs.QMessageBox = _FakeQMB
pedidos_de_disco: list[float] = []


def _disco_ok(required_gb=None):
    pedidos_de_disco.append(float(required_gb or 0))
    return {"ok": True, "free_gb": 42.0, "message": ""}


try:
    # --- oferta do Qwen: VRAM + ambiente ~3 GB + disco declarados ---
    _FakeQMB._reset(); pedidos_de_disco.clear()
    janela = _Janela(GPU8, set())
    with patch.object(model_manager, "check_disk_space", _disco_ok), \
         patch("transcribe_pipeline.llm_env.llm_env_ready", lambda *a, **k: False):
        ok = janela._ensure_optional_model("llm_qwen", "o modelo de análise",
                                           "Motivo X.", needs_llm_env=True)
    assert ok is False and len(_FakeQMB.perguntas) == 1  # recusou = sem download
    texto = _FakeQMB.perguntas[0][1]
    assert "8.7" in texto, texto
    assert "memória de vídeo" in texto and "8 GB" in texto, texto
    assert "~3 GB" in texto, "ambiente LLM nao declarado: " + texto
    assert "42.0 GB" in texto, texto
    assert pedidos_de_disco and abs(pedidos_de_disco[0] - 11.7) < 0.01, pedidos_de_disco
    print("PASS: oferta do Qwen declara VRAM, ambiente e disco")

    # --- GLiNER: sem linha de VRAM (roda em CPU), com ambiente ---
    _FakeQMB._reset(); pedidos_de_disco.clear()
    with patch.object(model_manager, "check_disk_space", _disco_ok), \
         patch("transcribe_pipeline.llm_env.llm_env_ready", lambda *a, **k: False):
        janela._ensure_optional_model("ner_gliner", "o modelo de nomes",
                                      "Motivo Y.", needs_llm_env=True)
    texto = _FakeQMB.perguntas[0][1]
    assert "memória de vídeo" not in texto, texto
    assert "~3 GB" in texto
    assert abs(pedidos_de_disco[0] - 4.1) < 0.01, pedidos_de_disco
    print("PASS: oferta do GLiNER sem VRAM, com ambiente")

    # --- ambiente ja pronto: nada de ~3 GB na oferta nem no disco ---
    _FakeQMB._reset(); pedidos_de_disco.clear()
    with patch.object(model_manager, "check_disk_space", _disco_ok), \
         patch("transcribe_pipeline.llm_env.llm_env_ready", lambda *a, **k: True):
        janela._ensure_optional_model("llm_qwen", "o modelo de análise",
                                      "Motivo.", needs_llm_env=True)
    texto = _FakeQMB.perguntas[0][1]
    assert "~3 GB" not in texto
    assert abs(pedidos_de_disco[0] - 8.7) < 0.01, pedidos_de_disco
    print("PASS: ambiente pronto nao e cobrado de novo")

    # --- disco insuficiente: avisa e NAO pergunta ---
    _FakeQMB._reset()
    with patch.object(model_manager, "check_disk_space",
                      lambda *a, **k: {"ok": False, "free_gb": 1.0,
                                       "message": "Espaço insuficiente."}):
        ok = janela._ensure_optional_model("llm_qwen", "o modelo de análise", "Motivo.")
    assert ok is False and not _FakeQMB.perguntas and _FakeQMB.avisos
    print("PASS: disco insuficiente aborta antes da pergunta")

    # --- _ensure_llm_model: GPU de 2 GB com modelo em cache SEGUE rodando ---
    # (regressao corrigida 2026-08-30: VRAM baixa e aviso, nao veto)
    _FakeQMB._reset()
    janela2 = _Janela(GPU2, {"llm_qwen"})
    with patch("transcribe_pipeline.summarize.summarize_ready", lambda: (True, "")):
        ok = janela2._ensure_llm_model()
    assert ok is True and not _FakeQMB.infos, _FakeQMB.infos
    print("PASS: GPU pequena com modelo baixado continua funcionando")

    # --- sem GPU nenhuma: bloqueio DURO continua valendo ---
    _FakeQMB._reset()
    SEM_GPU = caps.Hardware(has_gpu=False, ram_gb=16.0, cores=8, free_disk_gb=50.0)
    janela2b = _Janela(SEM_GPU, {"llm_qwen"})
    ok = janela2b._ensure_llm_model()
    assert ok is False and not _FakeQMB.perguntas
    assert _FakeQMB.infos and "NVIDIA" in _FakeQMB.infos[0][1], _FakeQMB.infos
    print("PASS: sem GPU o bloqueio duro permanece")

    # --- GPU de 2 GB SEM modelo: a oferta sai com o aviso de conta e risco ---
    _FakeQMB._reset(); pedidos_de_disco.clear()
    janela2c = _Janela(GPU2, set())
    with patch.object(model_manager, "check_disk_space", _disco_ok), \
         patch("transcribe_pipeline.llm_env.llm_env_ready", lambda *a, **k: True), \
         patch("transcribe_pipeline.summarize.summarize_ready",
               lambda: (False, "modelo ausente")):
        ok = janela2c._ensure_llm_model()
    assert ok is False and _FakeQMB.perguntas
    texto = _FakeQMB.perguntas[0][1]
    assert "conta e risco" in texto, texto
    print("PASS: oferta em GPU pequena avisa conta e risco")

    # --- _ensure_llm_model: GPU boa sem modelo -> oferta com needs_llm_env ---
    _FakeQMB._reset()
    chamadas: list[tuple] = []

    class _JanelaOferta(_Janela):
        def _ensure_optional_model(self, key, titulo, motivo, needs_llm_env=False):
            chamadas.append((key, needs_llm_env))
            return False

    janela3 = _JanelaOferta(GPU8, set())
    with patch("transcribe_pipeline.summarize.summarize_ready",
               lambda: (False, "modelo ausente")):
        ok = janela3._ensure_llm_model()
    assert ok is False and chamadas == [("llm_qwen", True)], chamadas
    print("PASS: gate do resumo delega a oferta com ambiente declarado")
finally:
    rs.QMessageBox = _qmb_original

print("PASS: toy_llm_gate_capability")
