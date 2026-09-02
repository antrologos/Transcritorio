"""Toy E4-4: parakeet_runner (motor experimental TAGARELA, ONNX).

Cobre as funcoes puras (tokens->palavras, merge de janelas, palavras->
segmentos), o guard pt-only e o dispatch por engine em run_whisperx.
Nao depende de onnx-asr nem de modelo baixado (CI minimo roda).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import model_manager, parakeet_runner, whisperx_runner
from transcribe_pipeline.config import Paths


def make_paths(root: Path) -> Paths:
    out = root / "Transcricoes"
    return Paths(
        project_root=root, output_root=out, config_dir=out / "00_config",
        manifest_dir=out / "00_manifest", wav_dir=out / "01_audio_wav16k_mono",
        asr_dir=out / "02_asr_raw", asr_variants_dir=out / "02_asr_variants",
        diarization_dir=out / "03_diarization", canonical_dir=out / "04_canonical",
        review_dir=out / "05_transcripts_review", qc_dir=out / "06_qc",
        logs_dir=out / "00_project",
    )


# ---------------------------------------------------------------- 1. tokens -> palavras
# Convencao SentencePiece real do TAGARELA (medida em amostra):
# ' Pr','on','to',',' -> "Pronto," (pontuacao sem espaco anexa).
tokens = [" Pr", "on", "to", ",", " tá", " bom", ".", " E", " aí"]
ts = [0.8, 0.96, 1.04, 1.2, 1.44, 1.68, 2.0, 3.5, 3.9]
lps = [0.0] * len(tokens)
words = parakeet_runner.tokens_to_words(tokens, ts, lps)
assert [w["word"] for w in words] == ["Pronto,", "tá", "bom.", "E", "aí"], words
assert words[0]["start"] == 0.8 and words[0]["end"] == 1.44, words[0]
assert words[2]["end"] == 3.5, words[2]  # fim = start da proxima
assert abs(words[4]["end"] - (3.9 + 0.30)) < 1e-6, words[4]  # ultima: +0.30
assert all(0.0 <= w["score"] <= 1.0 for w in words)
print("PASS: tokens_to_words agrega subwords e pontuacao")

# tokens vazios / so espacos nao viram palavra
assert parakeet_runner.tokens_to_words([], [], []) == []
assert parakeet_runner.tokens_to_words([" "], [0.5], [0.0]) == []
print("PASS: tokens_to_words defensivo com entrada vazia")

# ---------------------------------------------------------------- 2. merge de janelas
# Duas janelas, offset 165 s, overlap 5 s -> corte em 167.5 s global.
# Palavra da janela 0 em 168.0 global cai FORA (>= corte); a mesma
# palavra vinda da janela 1 (local 3.0 -> global 168.0) entra.
w0 = [{"word": "antes", "start": 160.0, "end": 160.5, "score": 1.0},
      {"word": "dup", "start": 168.0, "end": 168.4, "score": 1.0}]
w1 = [{"word": "cedo", "start": 1.0, "end": 1.4, "score": 1.0},
      {"word": "dup", "start": 3.0, "end": 3.4, "score": 1.0},
      {"word": "depois", "start": 10.0, "end": 10.5, "score": 1.0}]
merged = parakeet_runner.merge_windows([w0, w1], [0.0, 165.0], overlap_s=5.0)
got = [(w["word"], w["start"]) for w in merged]
assert got == [("antes", 160.0), ("dup", 168.0), ("depois", 175.0)], got
starts = [w["start"] for w in merged]
assert starts == sorted(starts), "merge quebrou monotonicidade"
print("PASS: merge_windows corta no meio do overlap sem duplicar")

# janela unica passa integral
solo = parakeet_runner.merge_windows([w1], [0.0], overlap_s=5.0)
assert len(solo) == 3
print("PASS: merge_windows com janela unica")

# ---------------------------------------------------------------- 3. palavras -> segmentos
ws = [
    {"word": "Oi,", "start": 0.0, "end": 0.3, "score": 1.0},
    {"word": "tudo", "start": 0.4, "end": 0.7, "score": 1.0},
    {"word": "bem?", "start": 0.8, "end": 1.1, "score": 1.0},   # fim de frase
    {"word": "Sim", "start": 1.5, "end": 1.8, "score": 1.0},
    {"word": "claro", "start": 4.0, "end": 4.3, "score": 1.0},  # pausa >= 1s antes
]
segs = parakeet_runner.words_to_segments(ws)
assert len(segs) == 3, segs
assert segs[0]["text"] == "Oi, tudo bem?" and segs[0]["start"] == 0.0
assert segs[0]["end"] == 1.1
assert segs[1]["text"] == "Sim", segs[1]  # quebrou pela pausa 1.8 -> 4.0
assert segs[2]["text"] == "claro"
assert len(segs[0]["words"]) == 3
print("PASS: words_to_segments quebra por pontuacao e pausa")

# fala continua sem pontuacao: quebra por duracao maxima (30 s)
longos = [{"word": f"w{i}", "start": float(i * 2), "end": float(i * 2 + 1),
           "score": 1.0} for i in range(20)]  # 0..39 s, sem pausa >= 1 s
segs2 = parakeet_runner.words_to_segments(longos)
assert len(segs2) >= 2, "fala continua de 39 s deveria quebrar em >= 2 segmentos"
print("PASS: words_to_segments quebra fala continua por duracao")

assert parakeet_runner.words_to_segments([]) == []
print("PASS: words_to_segments vazio")

# ---------------------------------------------------------------- 4. guard pt-only
ok, label = parakeet_runner.language_supported({"asr_language": "pt"})
assert ok and label == "pt"
ok, label = parakeet_runner.language_supported({"asr_language": "PT"})
assert ok, "normalize deveria aceitar maiusculas"
ok, label = parakeet_runner.language_supported({"asr_language": "es"})
assert not ok and label == "es"
# 2026-09-02: vazio/None ("automático") = portugues, alinhado a GUI
# (languages_outside_pt); antes o lote passava pelo gate e caia no runner.
ok, label = parakeet_runner.language_supported({"asr_language": None})
assert ok and label == "pt", (ok, label)
ok, label = parakeet_runner.language_supported({"asr_language": "  "})
assert ok and label == "pt", (ok, label)
print("PASS: language_supported aceita pt e vazio; recusa outros idiomas")

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    paths = make_paths(root)
    paths.manifest_dir.mkdir(parents=True)
    rows = [{"interview_id": "A1", "wav_path": "x.wav", "selected": "true"},
            {"interview_id": "A2", "wav_path": "y.wav", "selected": "true"}]
    eventos: list[dict] = []
    config = {"asr_model": "parakeet-pt", "asr_language": "es"}
    falhas = parakeet_runner.run_parakeet(
        rows, config, paths, progress_callback=eventos.append)
    assert falhas == 2, falhas
    assert len(eventos) == 2 and all(e["event"] == "asr_error" for e in eventos)
    assert "português" in eventos[0]["message"]
    jobs = (paths.manifest_dir / "jobs.jsonl").read_text(encoding="utf-8")
    assert jobs.count('"error"') >= 2 and "parakeet exige idioma pt" in jobs
    print("PASS: run_parakeet bloqueia lote com idioma != pt antes de tudo")

# ---------------------------------------------------------------- 5. dispatch por engine
spec = model_manager.ASR_VARIANTS.get("parakeet-pt")
assert spec is not None, "variante parakeet-pt ausente do catalogo"
assert spec.get("engine") == "parakeet_onnx"
assert spec.get("experimental") is True
assert spec.get("revision"), "SHA pinada obrigatoria"
assert "/" in str(spec.get("repo"))
assert not spec.get("demo_only"), "experimental != demo: deve ser ofertavel"
print("PASS: catalogo tem parakeet-pt com engine/experimental/SHA")

chamadas: list[str] = []
orig = parakeet_runner.run_parakeet
parakeet_runner.run_parakeet = lambda *a, **k: chamadas.append("parakeet") or 0
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        paths = make_paths(root)
        config = {"asr_model": "parakeet-pt", "asr_language": "pt",
                  "model_download_token_env": "TRANSCRITORIO_MODEL_DOWNLOAD_TOKEN"}
        rc = whisperx_runner.run_whisperx([], config, paths)
        assert rc == 0
        assert chamadas == ["parakeet"], "run_whisperx nao roteou para o parakeet"
finally:
    parakeet_runner.run_parakeet = orig
print("PASS: run_whisperx despacha engine parakeet_onnx para o runner")

print("PASS: toy_parakeet_runner")
