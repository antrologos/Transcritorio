"""Toy: a preparacao de modelos nao consulta a rede pelo que ja esta no disco — 2026-09-07.

Incidente de 2026-09-05: um projeto em espanhol so precisava do pacote de
alinhamento do espanhol. O dialogo de preparacao, vendo que so faltava um
modelo aberto, nao pediu token; mas o lote re-verificava TODOS os modelos
pela rede, inclusive o pyannote (restrito) que ja estava inteiro no disco.
HEAD sem token -> 401 -> uma falha -> lote reportado como falho -> a
transcricao pedida nunca comecou. A causa real ficou so no log.

O conserto e uma guarda: modelo pinado completo no cache nao vai a rede
(`force=True` continua indo). E a falha, quando ha, vira uma frase que diz
o modelo e o motivo. Este toy troca o downloader por um dublê e prova:

  1. o que esta completo no disco e PULADO; o que falta e baixado;
  2. com force, tudo vai a rede (o parametro deixa de ser letra morta);
  3. um 401 vira "token ausente ou inválido", com o nome do modelo;
  4. token "" nao bloqueia mais o plano B do ambiente;
  5. snapshot VAZIO nao conta como instalado (o predicado e estrito).

Sem Qt. Sem rede. HOME e cache de modelos em tempdir.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_HOME = tempfile.mkdtemp()
_CACHE = tempfile.mkdtemp()
os.environ["TRANSCRITORIO_HOME"] = _HOME
os.environ["TRANSCRITORIO_MODEL_CACHE"] = _CACHE

from transcribe_pipeline import model_manager as mm  # noqa: E402

VARIANTE = "large-v3-turbo"
ASSETS = mm.get_required_models([VARIANTE], include_diarization=True,
                                include_alignment=True, align_languages=("es",))
por_chave = {a.key: a for a in ASSETS}
pyannote = por_chave["diarization"]
whisper = por_chave[f"asr_{VARIANTE}"]
espanhol = por_chave["alignment_es"]
assert pyannote.gated and not whisper.gated and not espanhol.gated


def instalar_falso(asset, *, com_pesos: bool = True) -> None:
    """Deixa o modelo como o hub deixa: snapshots/<sha>/ + um blob real."""
    repo = mm.hf_cache_path(asset.repo_id, Path(_CACHE))
    (repo / "snapshots" / asset.revision).mkdir(parents=True, exist_ok=True)
    (repo / "blobs").mkdir(parents=True, exist_ok=True)
    if com_pesos:
        (repo / "blobs" / "peso").write_bytes(b"\0" * (mm._WEIGHT_BLOB_MIN_BYTES + 1))


# --------------------------------------------------------------- o dublê
chamadas: list[dict] = []


def downloader_falso(**kw) -> None:
    chamadas.append(kw)
    if kw["repo_id"] == pyannote.repo_id and not kw.get("token"):
        # E exatamente o que o servidor fez em 2026-09-05.
        raise RuntimeError("HEAD .gitattributes: status 401")


mm._manual_snapshot_download = downloader_falso
mm._ensure_nltk_punkt = lambda: None  # o toy nao pode ir a rede

# ------------------------------------------------ 1. completo no disco = pulado
instalar_falso(pyannote)
instalar_falso(whisper)
# o espanhol NAO esta instalado (era o estado real as 19:58)
chamadas.clear()
relatorio: list[str] = []
falhas = mm.download_required_models(
    token="", asr_variants=[VARIANTE], include_diarization=True,
    include_alignment=True, align_languages=("es",), relatorio=relatorio)
baixados = [c["repo_id"] for c in chamadas]
assert falhas == 0, (falhas, relatorio)
assert baixados == [espanhol.repo_id], f"so o que falta vai a rede: {baixados}"
assert relatorio == []
print("PASS: pyannote e whisper completos no disco nao vao a rede; o espanhol vai")

# ------------------------------------------------------- 2. force vai a rede
chamadas.clear()
relatorio = []
falhas = mm.download_required_models(
    token="", force=True, asr_variants=[VARIANTE], include_diarization=True,
    include_alignment=True, align_languages=("es",), relatorio=relatorio)
assert {c["repo_id"] for c in chamadas} == {pyannote.repo_id, whisper.repo_id, espanhol.repo_id}, \
    "force re-verifica tudo"
assert falhas == 1, falhas
print("PASS: force=True re-verifica os tres (o parametro deixou de ser letra morta)")

# -------------------------------------------- 3. a falha vira frase de gente
assert len(relatorio) == 1 and relatorio[0].startswith(pyannote.label), relatorio
assert "401" in relatorio[0] and "token" in relatorio[0].lower(), relatorio
assert "Gerenciar modelos" in relatorio[0], "tem de dizer ONDE consertar"
assert "HEAD" not in relatorio[0], "o texto cru do servidor nao e para o usuario"
print("PASS: 401 vira 'token ausente ou inválido', com o nome do modelo e o caminho do menu")

for exc, trecho in (
    (RuntimeError("HEAD config.yaml: status 403"), "termos"),
    (ConnectionError("Failed to establish a new connection"), "conexão"),
    (TimeoutError("read timed out"), "conexão"),
    (RuntimeError("API retornou 0 arquivos"), "API retornou 0 arquivos"),
):
    assert trecho in mm.motivo_da_falha(exc), (exc, mm.motivo_da_falha(exc))
print("PASS: 403, sem rede e erro generico tem cada um a sua frase")

# ------------------------------------ 4. token "" nao bloqueia o ambiente
chamadas.clear()
os.environ["TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN"] = "hf_do_ambiente"
try:
    mm.download_required_models(
        token="", force=True, asr_variants=[VARIANTE], include_diarization=True,
        include_alignment=False)
    tokens = {c["repo_id"]: c.get("token") for c in chamadas}
    assert tokens[pyannote.repo_id] == "hf_do_ambiente", tokens
finally:
    os.environ.pop("TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN", None)
print("PASS: token vazio deixa o token do ambiente passar (is not None -> or)")

# ------------------------------------------------ 5. snapshot vazio nao conta
chamadas.clear()
instalar_falso(espanhol, com_pesos=False)  # pasta criada, download interrompido
mm.download_required_models(
    token="", asr_variants=[VARIANTE], include_diarization=False,
    include_alignment=True, align_languages=("es",))
assert [c["repo_id"] for c in chamadas] == [espanhol.repo_id], \
    "snapshot sem pesos NAO e 'instalado' — tem de ir a rede"
print("PASS: snapshot vazio (download interrompido) nao passa pela guarda")

assert "PySide6" not in sys.modules
print("PASS: toy_pre_voo_pula_cache")
