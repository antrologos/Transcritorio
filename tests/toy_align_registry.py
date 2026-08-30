"""Toy test: registro de pacotes de idioma + decisao de alinhamento (E4-1).

Bugs originais: o modo Automatico era tratado como pt por nos e como
INGLES pelo WhisperX (que ainda baixava o alinhador da pytorch.org em
runtime); en/es/fr/de/it usavam a rota torchaudio sem pin e imune ao
nosso offline; idiomas sem alinhador morriam em ValueError DEPOIS de
transcrever a entrevista inteira.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_service, model_manager
from transcribe_pipeline import whisperx_runner

# --- registro: 16 idiomas, pt compat com o asset pinado existente ---
assert model_manager.align_language_supported("pt")
assert model_manager.align_language_supported("nl")   # holandes (Belgica)
assert model_manager.align_language_supported("EN ")  # normaliza caixa/espaco
assert not model_manager.align_language_supported("sw")   # suaili: so via MMS (E4-3)
assert not model_manager.align_language_supported("")
assert len(model_manager.ALIGN_LANGUAGES) == 16

pt = model_manager.align_asset_for("pt")
assert pt.key == "alignment_pt"                      # chave/cache legados intactos
assert pt.revision == "634ac655299bcdc46c83bc01da9bab52d2987e4f"
assert "language_model/*" in pt.download_exclude

nl = model_manager.align_asset_for("nl")
assert nl.key == "alignment_nl"
assert nl.repo_id == "jonatasgrosman/wav2vec2-large-xlsr-53-dutch"
assert nl.revision, "alinhador sem SHA pinada"
assert nl.gated is False
# en: repo tem pesos duplicados (.bin + .safetensors) — o exclude corta o .bin
en = model_manager.align_asset_for("en")
assert "pytorch_model.bin" in en.download_exclude, en.download_exclude

# todos os repos sao conhecidos (nao viram orfaos na limpeza)
conhecidos = model_manager._known_repos()
for spec in model_manager.ALIGN_LANGUAGES.values():
    assert spec["repo"] in conhecidos, f"{spec['repo']} viraria orfao"

# asset_by_key resolve os pacotes de idioma (Baixar por item do gerenciador)
assert model_manager.asset_by_key("alignment_nl").repo_id == nl.repo_id
assert model_manager.asset_by_key("alignment_pt").key == "alignment_pt"
print("PASS: registro de idiomas")

# --- get_required_models com idiomas do lote ---
chaves = {a.key for a in model_manager.get_required_models(
    ["small"], include_diarization=False, include_alignment=True,
    align_languages=("pt", "en"))}
assert "alignment_pt" in chaves and "alignment_en" in chaves, chaves
chaves = {a.key for a in model_manager.get_required_models(
    ["small"], include_diarization=False, include_alignment=True)}
assert "alignment_pt" in chaves and "alignment_en" not in chaves  # default: pt
chaves = {a.key for a in model_manager.get_required_models(
    ["small"], include_diarization=False, include_alignment=False,
    align_languages=("pt", "en"))}
assert not any(k.startswith("alignment") for k in chaves)  # essencial: nenhum
print("PASS: get_required_models por idiomas do lote")

# --- decisao de alinhamento ANTES de transcrever (pura + cache dir) ---
with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    resolve = whisperx_runner.resolve_align_action

    # explicito do usuario passa direto (rota expert da CLI)
    acao, valor, _ = resolve({"asr_align_model": "meu/modelo"}, cache)
    assert (acao, valor) == ("explicit", "meu/modelo")

    # Automatico (None) = SEM alinhador, sempre (o WhisperX alinharia com
    # ingles e baixaria da pytorch.org)
    acao, _, motivo = resolve({"asr_language": None}, cache)
    assert acao == "no_align" and "utom" in motivo, (acao, motivo)

    # idioma sem pacote (suaili): transcreve sem tempos, avisado
    acao, _, motivo = resolve({"asr_language": "sw"}, cache)
    assert acao == "no_align" and "sw" in motivo

    # idioma suportado mas SEM cache: no_align (nunca deixar o WhisperX
    # baixar alinhador nao-pinado)
    acao, _, motivo = resolve({"asr_language": "nl"}, cache)
    assert acao == "no_align" and "não instalado" in motivo, motivo

    # idioma suportado COM cache (estrutura HF fake com peso >= 4 MB)
    repo_dir = cache / ("models--" + nl.repo_id.replace("/", "--"))
    (repo_dir / "snapshots" / nl.revision).mkdir(parents=True)
    (repo_dir / "refs").mkdir()
    (repo_dir / "refs" / "main").write_text(nl.revision, encoding="utf-8")
    (repo_dir / "snapshots" / nl.revision / "model.bin").write_bytes(b"x")
    (repo_dir / "blobs").mkdir()
    (repo_dir / "blobs" / "h1").write_bytes(b"x" * (5 * 1024 * 1024))
    acao, valor, _ = resolve({"asr_language": "nl"}, cache)
    assert (acao, valor) == ("model", nl.repo_id), (acao, valor)
print("PASS: resolve_align_action")

# --- idiomas do LOTE a partir dos metadados por arquivo ---
contexto = SimpleNamespace(
    config={"asr_language": "pt"},
    metadata={
        "A": {},                        # herda do projeto -> pt
        "B": {"language": "en"},        # pacote suportado
        "C": {"language": "auto"},      # deteccao -> sem alinhador
        "D": {"language": "sw"},        # sem pacote -> aviso
        "E": {"language": "project"},   # sentinela de heranca -> pt
    },
)
langs, avisos = app_service.alignment_languages_for(contexto, ["A", "B", "C", "D", "E"])
assert langs == ("en", "pt"), langs
assert avisos == ("automático", "sw"), avisos
# projeto em modo auto: heranca vira aviso de automatico
contexto.config["asr_language"] = None
langs, avisos = app_service.alignment_languages_for(contexto, ["A"])
assert langs == () and avisos == ("automático",)
print("PASS: alignment_languages_for")

# --- MMS: pacote multilingue coringa (E4-3, decisao do usuario) ---
mms = model_manager.asset_by_key("alignment_mms")
assert mms.repo_id == "MahmoudAshraf/mms-300m-1130-forced-aligner"
assert mms.revision == "49402e9577b1158620820667c218cd494cc44486"
assert "pytorch_model.bin" in mms.download_exclude  # pesos duplicados do safetensors
assert "NC" in mms.license_notice or "COMERCIAL" in mms.license_notice.upper(), \
    "aviso de licenca obrigatorio no MMS"
assert mms.repo_id in model_manager._known_repos()

with tempfile.TemporaryDirectory() as tmp:
    cache = Path(tmp)
    resolve = whisperx_runner.resolve_align_action

    # suaili sem NADA: no_align, e o motivo aponta o pacote multilingue
    acao, _, motivo = resolve({"asr_language": "sw"}, cache)
    assert acao == "no_align" and "multil" in motivo.lower(), motivo

    # suaili COM o MMS instalado: alinha pelo coringa
    repo_dir = cache / ("models--" + mms.repo_id.replace("/", "--"))
    (repo_dir / "snapshots" / mms.revision).mkdir(parents=True)
    (repo_dir / "refs").mkdir()
    (repo_dir / "refs" / "main").write_text(mms.revision, encoding="utf-8")
    (repo_dir / "snapshots" / mms.revision / "m.safetensors").write_bytes(b"x")
    (repo_dir / "blobs").mkdir()
    (repo_dir / "blobs" / "h1").write_bytes(b"x" * (5 * 1024 * 1024))
    acao, valor, _ = resolve({"asr_language": "sw"}, cache)
    assert (acao, valor) == ("model", mms.repo_id), (acao, valor)

    # idioma SUPORTADO sem o pacote dedicado tambem cai no MMS instalado
    acao, valor, _ = resolve({"asr_language": "nl"}, cache)
    assert (acao, valor) == ("model", mms.repo_id), (acao, valor)

    # Automatico continua SEM alinhador mesmo com MMS (deteccao nao e confiavel)
    acao, _, _motivo = resolve({"asr_language": None}, cache)
    assert acao == "no_align"
print("PASS: fallback MMS")

print("PASS: toy_align_registry")
