"""Toy: os passos da volta guiada — 2026-09-07.

Pedido do usuario: "oferecer no primeiro uso, mas tambem deixar como opcao
que o usuario pode querer usar depois". A parte que da para provar SEM Qt
esta aqui: os passos existem, cada um aponta um alvo nomeado, os textos sao
curtos e falam a lingua da interface. A parte Qt (o alvo e um widget real, o
halo cobre a geometria) fica em toy_tour_window.

Puro: so `volta_guiada`. Sem PySide6.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import volta_guiada as vg  # noqa: E402

assert 6 <= vg.total() <= 10, f"uma volta tem de caber em dois minutos: {vg.total()} passos"
for p in vg.PASSOS:
    assert p.titulo and p.texto and p.alvo, p
    assert len(p.texto) <= 260, f"{p.titulo}: texto longo demais para um cartao ({len(p.texto)})"
    assert p.aba in (None, 0, 1, 2), p
    if p.precisa_entrevista:
        assert p.alvo_reserva and p.reserva, f"{p.titulo}: passo do Estudio precisa de reserva"
titulos = [p.titulo for p in vg.PASSOS]
assert len(titulos) == len(set(titulos)), "titulo repetido"
print(f"PASS: {vg.total()} passos, todos com titulo, texto curto e alvo")

# A jornada: comeca nas gravacoes, passa pelo Estudio, termina na ajuda.
assert "grava" in vg.PASSOS[0].texto.lower() or "grava" in vg.PASSOS[0].titulo.lower()
assert vg.PASSOS[-1].alvo == "menuBar" and "F1" in vg.PASSOS[-1].texto
assert any(p.precisa_entrevista for p in vg.PASSOS), "algum passo tem de apontar o Estudio"
assert vg.rotulo_contador(0) == f"1 de {vg.total()}"
print("PASS: a volta segue a jornada e termina dizendo onde fica a ajuda")

# Lingua: mesmas regras de tests/toy_ui_textos.py.
PROIBIDOS = re.compile(
    r"\b(QC|manifesto|canonical|merge|fundir|Infocitizen)\b"
    r"|menu\s+(Transcrever|Arquivo)\b|\bArquivo\s*(→|->|>)\s", re.I)
IA_SOLTA = re.compile(r"\bIA\b")
SEM_ACENTO = re.compile(
    r"\b(transcricao|transcricoes|midia|midias|rotulo|rotulos|nao|voce|"
    r"exportacao|documentacao|revisao|separacao|audio|audios|video|videos|"
    r"proxima|proximo|usuario|possivel|instalacao|ultima|ultimo)\b", re.I)
problemas = []
for p in vg.PASSOS:
    for campo in (p.titulo, p.texto, p.reserva):
        plano = unicodedata.normalize("NFC", campo)
        for nome, regra in (("proibido", PROIBIDOS), ("IA solta", IA_SOLTA), ("sem acento", SEM_ACENTO)):
            if regra.search(plano):
                problemas.append(f"{p.titulo} ({nome}): {plano[:60]}")
        if "..." in plano:
            problemas.append(f"{p.titulo} (reticencias ASCII): {plano[:60]}")
assert not problemas, "guia verbal violado:\n  " + "\n  ".join(problemas)
print("PASS: os textos da volta seguem o guia verbal")

assert "PySide6" not in sys.modules, "volta_guiada.py tem de continuar puro"
print("PASS: toy_volta_guiada")
