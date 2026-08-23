"""Toy test para utils.is_interview_artifact().

Valida que o filtro de artefatos por entrevista nao sofre colisao de prefixo
(bug: rglob("{id}*") casava entrevista_10 para id entrevista_1, apagando/movendo
arquivos de OUTRA entrevista em delete_transcription_outputs/collect_trash_files).

Sem dependencias pesadas — roda com stdlib pura.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.utils import is_interview_artifact

# Artefatos da propria entrevista: devem casar
assert is_interview_artifact("entrevista_1.json", "entrevista_1")
assert is_interview_artifact("entrevista_1.whisperx.json", "entrevista_1")
assert is_interview_artifact("entrevista_1.canonical.json", "entrevista_1")
assert is_interview_artifact("entrevista_1.review.json", "entrevista_1")
assert is_interview_artifact("entrevista_1.exclusive.rttm", "entrevista_1")
assert is_interview_artifact("entrevista_1.md", "entrevista_1")
assert is_interview_artifact("entrevista_1.docx", "entrevista_1")
assert is_interview_artifact("entrevista_1_nvivo.tsv", "entrevista_1")
assert is_interview_artifact("entrevista_1", "entrevista_1")

# Colisao de prefixo: NAO devem casar (bug original)
assert not is_interview_artifact("entrevista_10.json", "entrevista_1")
assert not is_interview_artifact("entrevista_10.review.json", "entrevista_1")
assert not is_interview_artifact("entrevista_10_nvivo.tsv", "entrevista_1")
assert not is_interview_artifact("entrevista_1b.json", "entrevista_1")
assert not is_interview_artifact("entrevista_1 copia.json", "entrevista_1")

# Ids com pontos/acentos (nomes reais de arquivos de campo)
assert is_interview_artifact("A01P_0608.json", "A01P_0608")
assert not is_interview_artifact("A01P_06081.json", "A01P_0608")
assert is_interview_artifact("gravação#3.json", "gravação#3")

# Nao casar id de outra entrevista que e prefixo com underscore generico
assert not is_interview_artifact("entrevista_1_parte2.json", "entrevista_1")

print("PASS: toy_artifact_match")
