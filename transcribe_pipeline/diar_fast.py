"""Embeddings do pyannote em CPU: rede UMA vez por janela, pooling por mascara.

Medido em 2026-09-02 (clipe de 5 min, 16 threads): 94% do tempo da
separacao de falantes vai para os embeddings, porque o pipeline
community-1 (pyannote.audio 4.0.4, SpeakerDiarization.get_embeddings)
gera um par (waveform, mascara) por VAGA de falante local (3) e roda
fbank + ResNet34 inteiros por par — cada janela de 10 s atravessa a rede
tres vezes, mesmo numa entrevista a dois. A mascara so entra no pooling
(StatsPool aceita weights (batch, speakers, frames)), e o modelo ja expoe
forward_frames (fbank + backbone) e forward_embedding (pooling + seg_1).

Este modulo liga, na INSTANCIA do pipeline, um get_embeddings que roda o
backbone uma vez por janela e o pooling com as 3 mascaras: mesma
matematica, mesmo layout de saida (num_chunks, local_num_speakers, dim),
mesmo contrato de hook. Nada do pyannote instalado e alterado; se o
checkpoint nao expuser o caminho separado, o original permanece.
Gate: tests/toy_diar_fast.py (puras + igualdade numerica no modelo real).
"""
from __future__ import annotations

import math
import types
import warnings
from typing import Any, Callable, Optional

import numpy as np


def select_used_masks(masks: np.ndarray, clean_masks: np.ndarray, min_num_frames: int) -> np.ndarray:
    """Mascara usada por vaga de falante (pura) — replica iter_waveform_and_mask.

    masks/clean_masks: (num_frames, local_num_speakers). Devolve
    (local_num_speakers, num_frames) float32: a limpa (sem sobreposicao)
    quando tem mais que min_num_frames frames, senao a cheia. NaN vira 0.
    """
    masks = np.nan_to_num(np.asarray(masks), nan=0.0).astype(np.float32)
    clean_masks = np.nan_to_num(np.asarray(clean_masks), nan=0.0).astype(np.float32)
    out = np.empty((masks.shape[1], masks.shape[0]), dtype=np.float32)
    for s, (mask, clean) in enumerate(zip(masks.T, clean_masks.T)):
        out[s] = clean if np.sum(clean) > min_num_frames else mask
    return out


def embedding_batch_count(num_chunks: int, batch_size: int) -> int:
    """Lotes de JANELAS (nao de pares) — total do hook de progresso (pura)."""
    return math.ceil(num_chunks / max(1, int(batch_size)))


def fast_get_embeddings(
    self: Any,
    file: Any,
    binary_segmentations: Any,
    exclude_overlap: bool = False,
    hook: Optional[Callable] = None,
):
    """Substituto de SpeakerDiarization.get_embeddings (mesma assinatura).

    Retorna (num_chunks, local_num_speakers, dimension) float32, como o
    original; emite hook("embeddings", lote2d, total=, completed=) por lote,
    como o original (o apply emite o 3-D final por conta propria).
    """
    if getattr(self, "training", False):
        # Otimizacao de hiperparametros usa cache proprio — caminho original.
        return self._get_embeddings_original(
            file, binary_segmentations, exclude_overlap=exclude_overlap, hook=hook)

    import torch
    from pyannote.core import SlidingWindowFeature

    duration = binary_segmentations.sliding_window.duration
    num_chunks, num_frames, num_speakers = binary_segmentations.data.shape

    # Identico ao original: frames com >= 2 falantes sao zerados na mascara
    # "limpa"; min_num_frames vem do menor trecho que o embedder aceita.
    if exclude_overlap:
        min_num_samples = self._embedding.min_num_samples
        num_samples = duration * self._embedding.sample_rate
        min_num_frames = math.ceil(num_frames * min_num_samples / num_samples)
        clean_frames = 1.0 * (np.sum(binary_segmentations.data, axis=2, keepdims=True) < 2)
        clean_segmentations = SlidingWindowFeature(
            binary_segmentations.data * clean_frames, binary_segmentations.sliding_window)
    else:
        min_num_frames = -1
        clean_segmentations = SlidingWindowFeature(
            binary_segmentations.data, binary_segmentations.sliding_window)

    batch_size = max(1, int(getattr(self, "embedding_batch_size", 32) or 32))
    batch_count = embedding_batch_count(num_chunks, batch_size)
    model = self._embedding.model_
    device = self._embedding.device

    if hook is not None:
        hook("embeddings", None, total=batch_count, completed=0)

    def _iter_chunks():
        for (chunk, masks), (_, clean_masks) in zip(binary_segmentations, clean_segmentations):
            waveform, _ = self._audio.crop(file, chunk, mode="pad")
            # waveform: (1, num_samples); mascaras: (num_frames, local_num_speakers)
            yield waveform[None], torch.from_numpy(
                select_used_masks(masks, clean_masks, min_num_frames))[None]
            # (1, 1, num_samples), (1, local_num_speakers, num_frames)

    saidas: list[np.ndarray] = []
    lote_w: list[torch.Tensor] = []
    lote_m: list[torch.Tensor] = []
    feitos = 0

    def _flush() -> None:
        nonlocal feitos, lote_w, lote_m
        if not lote_w:
            return
        waveforms = torch.vstack(lote_w)      # (B, 1, num_samples)
        weights = torch.vstack(lote_m)        # (B, local_num_speakers, num_frames)
        with torch.inference_mode():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                frames = model.forward_frames(waveforms.to(device))
                emb = model.forward_embedding(frames, weights=weights.to(device))
                # (B, local_num_speakers, dimension)
        lote = emb.cpu().numpy()
        saidas.append(lote)
        feitos += 1
        if hook is not None:
            hook("embeddings", lote.reshape(-1, lote.shape[-1]), total=batch_count, completed=feitos)
        lote_w, lote_m = [], []

    for w, m in _iter_chunks():
        lote_w.append(w)
        lote_m.append(m)
        if len(lote_w) >= batch_size:
            _flush()
    _flush()

    if not saidas:
        return np.zeros((0, num_speakers, int(self._embedding.dimension)), dtype=np.float32)
    return np.concatenate(saidas, axis=0).astype(np.float32, copy=False)


def install_fast_embeddings(pipeline: Any) -> bool:
    """Liga o caminho rapido na instancia; False = ficou o original.

    Exige o embedder PyTorch do pyannote com forward_frames/forward_embedding
    (e o caso do community-1). Guarda o original em _get_embeddings_original.
    """
    embedder = getattr(pipeline, "_embedding", None)
    model = getattr(embedder, "model_", None)
    if model is None or getattr(embedder, "device", None) is None:
        return False
    if not (callable(getattr(model, "forward_frames", None))
            and callable(getattr(model, "forward_embedding", None))):
        return False
    if not hasattr(pipeline, "get_embeddings") or not hasattr(pipeline, "_audio"):
        return False
    if not hasattr(pipeline, "_get_embeddings_original"):
        pipeline._get_embeddings_original = pipeline.get_embeddings
    pipeline.get_embeddings = types.MethodType(fast_get_embeddings, pipeline)
    return True
