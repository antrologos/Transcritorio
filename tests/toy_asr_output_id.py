"""Toy: safe_output_id — nome de saida do ASR identico ao interview_id.

Bug real (2026-09-02): TAGARELA e MLX trocavam por "_" qualquer caractere
fora de ASCII alfanumerico (espaco, acentos...) ao gravar
02_asr_raw/{id}.json, enquanto o render procurava pelo id cru ->
"Missing WhisperX JSON" e "montando transcricao editavel: 1 falha(s)" —
depois de uma hora de separacao de falantes. Regra: qualquer nome que o
sistema operacional aceite passa INTACTO; so separadores de caminho e
caracteres proibidos em nome de arquivo viram "_"; vazio/so pontos = "".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import mlx_whisper_runner, parakeet_runner  # noqa: E402
from transcribe_pipeline.utils import safe_output_id  # noqa: E402

# Tudo que um sistema operacional aceita como nome de arquivo fica igual.
INTACTOS = [
    "20260820-FMC2_MinSaude_Sonia Venancio",
    "Entrevista_João_ção (2ª) [final] #1 & cia, 100% ok",
    "_piloto",
    ".oculto",
    "Sonia.Venancio",
    "-teste",
    "2026",
    "MAIÚSCULA e minúscula",
    "O'Neil",
    "café~",
    "a+b=c@d",
    "emoji 🎙️",
]
for raw in INTACTOS:
    assert safe_output_id(raw) == raw, (raw, safe_output_id(raw))

# Separadores de caminho e proibidos no Windows viram "_": a saida fica
# dentro de 02_asr_raw mesmo com manifest editado a mao.
assert safe_output_id("../../../evil") == ".._.._.._evil"
assert safe_output_id("..\\..\\x") == ".._.._x"
assert safe_output_id('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"
assert safe_output_id("nome\x00zero\x1f") == "nome_zero_"
for raw in ("x/y", "x\\y", "/abs/path", "C:\\abs"):
    out = safe_output_id(raw)
    assert "/" not in out and "\\" not in out and ":" not in out, (raw, out)

# Invalidos: vazio, so espacos, so pontos/sublinhados, so separadores.
for raw in ("", "   ", "...", "._.", "//", "\\", None):
    assert safe_output_id(raw) == "", (raw, safe_output_id(raw))  # type: ignore[arg-type]

# Os dois runners usam a MESMA funcao — nenhum regex proprio sobrou.
assert not hasattr(parakeet_runner, "_SAFE_ID_RE"), "parakeet_runner ainda tem regex propria"
assert not hasattr(mlx_whisper_runner, "_SAFE_ID_RE"), "mlx_whisper_runner ainda tem regex propria"
assert parakeet_runner.safe_output_id is safe_output_id
assert mlx_whisper_runner.safe_output_id is safe_output_id

print("PASS: toy_asr_output_id")
