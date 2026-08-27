"""Toy test: analise de canais (fase 4) — partes puras + fluxo sondar/extrair.

O fluxo completo precisa de ffmpeg (extracao por canal); sem ele, so as
partes puras rodam (CI minimo).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import channels as ch
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
from transcribe_pipeline.render import load_external_diarization
from transcribe_pipeline.utils import read_json, write_json

# --- source_channels: manifesto sujo nao pode quebrar ---
assert ch.source_channels({"source_audio_channels": "2"}) == 2
assert ch.source_channels({"source_audio_channels": ""}) == 0
assert ch.source_channels({"source_audio_channels": "nao-numero"}) == 0
assert ch.source_channels({}) == 0
print("PASS: source_channels")

# --- probe_slices: 3 pontos em arquivo longo, trecho unico em curto ---
slices = ch.probe_slices(3600.0)
assert len(slices) == 3
assert slices[0][0] < slices[1][0] < slices[2][0]
assert all(length == ch.PROBE_SLICE_SECONDS for _s, length in slices)
assert slices[2][0] + ch.PROBE_SLICE_SECONDS <= 3600.0  # nao passa do fim
assert len(ch.probe_slices(45.0)) == 1
assert len(ch.probe_slices(0.0)) == 1
print("PASS: probe_slices")

# --- channel_extract_command: com e sem recorte ---
command = ch.channel_extract_command(
    "ffmpeg", Path("a.mp3"), Path("out.ch1.wav"), 1, 16000, start=12.5, duration=60.0)
assert "pan=mono|c0=c1" in command and "-ss" in command and "12.5" in command
assert command[command.index("-t") + 1] == "60.0"
assert "-ss" not in ch.channel_extract_command("ffmpeg", Path("a"), Path("b"), 0, 16000)
print("PASS: channel_extract_command")

# --- mean_embedding ---
assert ch.mean_embedding([[3.0, 0.0], [1.0, 0.0]]) == [1.0, 0.0]
assert ch.mean_embedding([]) is None
assert ch.mean_embedding([[1.0], [1.0, 2.0]]) is None
assert ch.mean_embedding([[0.0, 0.0]]) is None
print("PASS: mean_embedding")

# --- channel_speaker_map: casamento 1:1, sobras, sem pyannote ---
mapping = ch.channel_speaker_map(
    2, {0: [0.0, 1.0], 1: [1.0, 0.0]},
    {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]})
assert mapping == {0: "SPEAKER_01", 1: "SPEAKER_00"}  # cruzado pelo cosseno
mapping = ch.channel_speaker_map(3, {0: [1.0, 0.0]}, {"SPEAKER_00": [1.0, 0.0]})
assert mapping == {0: "SPEAKER_00", 1: "SPEAKER_01", 2: "SPEAKER_02"}
assert ch.channel_speaker_map(2, {}, {}) == {0: "SPEAKER_00", 1: "SPEAKER_01"}
print("PASS: channel_speaker_map")

# --- segments_from_labels ---
segments = ch.segments_from_labels([0, 0, None, 1, 1, 1, None, None, 0], window_seconds=0.5)
assert segments == [
    {"start": 0.0, "end": 1.0, "channel": 0},
    {"start": 1.5, "end": 3.0, "channel": 1},
    {"start": 4.0, "end": 4.5, "channel": 0},
]
assert ch.segments_from_labels([]) == []
print("PASS: segments_from_labels")

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def write_source(path: Path, blocks: list[tuple[float, int, int]], rate: int = 16000) -> None:
    """Fonte estereo sintetica: blocks = [(segundos, amp_L, amp_R)]."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for seconds, left, right in blocks:
            frame = left.to_bytes(2, "little", signed=True) + right.to_bytes(2, "little", signed=True)
            handle.writeframes(frame * int(rate * seconds))


def project(tmp: Path):
    config = load_config(None)
    paths = make_paths(config, base_dir=tmp)
    ensure_directories(paths)
    return config, paths


if HAS_NUMPY and HAS_FFMPEG:
    # --- informativo: L fala na 1a metade, R na 2a -> extrai e segmenta ---
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config, paths = project(root)
        src_dir = root / "orig"; src_dir.mkdir()
        write_source(src_dir / "I01.wav", [(30.0, 9000, 0), (30.0, 0, 9000)])
        wav_rel = f"{paths.wav_dir.relative_to(paths.project_root)}/I01.wav".replace("\\", "/")
        row = {"interview_id": "I01", "selected": "true", "source_path": "orig/I01.wav",
               "wav_path": wav_rel, "source_audio_channels": "2", "duration_sec": "60"}
        work_root = root / "trabalho"
        assert ch.run_channel_analysis([row], config, paths, work_root=work_root) == 0
        # intermediarios apagados: nada sobra na raiz de trabalho
        assert list(work_root.rglob("*")) == [], list(work_root.rglob("*"))
        payload = read_json(ch.channels_json_path(paths, "I01"))
        assert payload["decision"] == "informative", payload
        spans = [(s["start"], s["end"], s["speaker"]) for s in payload["segments"]]
        assert len(spans) == 2 and spans[0][2] != spans[1][2], spans
        assert spans[0][0] == 0.0 and abs(spans[1][0] - 30.0) < 1.0, spans
        # o render le a fonte channels
        segs = load_external_diarization(paths, "I01", dict(config, diarization_source="channels"))
        assert len(segs) == 2 and set(segs[0]) == {"start", "end", "speaker"}
        # NENHUM wav por canal deixado no projeto
        assert list(paths.wav_dir.glob("*.ch*.wav")) == []
        assert list(paths.project_root.rglob("*.ch*.wav")) == []
    print("PASS: fluxo informativo (sonda -> extrai -> segmenta)")

    # --- dual-mono (caso REAL do acervo): ambience, zero extracao ---
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config, paths = project(root)
        src_dir = root / "orig"; src_dir.mkdir()
        write_source(src_dir / "A01.wav", [(20.0, 8000, 8000), (10.0, 0, 0), (30.0, 8000, 8000)])
        wav_rel = f"{paths.wav_dir.relative_to(paths.project_root)}/A01.wav".replace("\\", "/")
        row = {"interview_id": "A01", "selected": "true", "source_path": "orig/A01.wav",
               "wav_path": wav_rel, "source_audio_channels": "2", "duration_sec": "60"}
        work_root = root / "trabalho"
        assert ch.run_channel_analysis([row], config, paths, work_root=work_root) == 0
        assert list(work_root.rglob("*")) == []
        payload = read_json(ch.channels_json_path(paths, "A01"))
        assert payload["decision"] == "ambience", payload
        assert payload["segments"] == [] and payload["envelope_correlation"] >= 0.98
        assert load_external_diarization(paths, "A01", dict(config, diarization_source="channels")) == []
        assert list(paths.project_root.rglob("*.ch*.wav")) == []
        # segunda passada nao re-sonda (decisao fresca)
        before = ch.channels_json_path(paths, "A01").read_text(encoding="utf-8")
        assert ch.run_channel_analysis([row], config, paths, work_root=work_root) == 0
        assert ch.channels_json_path(paths, "A01").read_text(encoding="utf-8") == before
    print("PASS: fluxo ambience (dual-mono, sem extracao)")

    # --- mono e fonte ausente: silencio ---
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config, paths = project(root)
        mono = {"interview_id": "M01", "selected": "true", "source_path": "orig/M01.mp3",
                "wav_path": "x.wav", "source_audio_channels": "1", "duration_sec": "60"}
        missing = {"interview_id": "X99", "selected": "true", "source_path": "orig/nao_existe.wav",
                   "wav_path": "x.wav", "source_audio_channels": "2", "duration_sec": "60"}
        assert ch.run_channel_analysis([mono, missing], config, paths) == 0
        assert not ch.channels_json_path(paths, "M01").exists()
        assert not ch.channels_json_path(paths, "X99").exists()
    print("PASS: mono e fonte ausente pulados em silencio")
elif HAS_NUMPY:
    # sem ffmpeg: ao menos a decisao sobre envelopes sinteticos
    env_info = [[0.9] * 20 + [0.0] * 20, [0.0] * 20 + [0.9] * 20]
    assert ch.analyze_envelopes(env_info)[0] == "informative"
    same = [0.5, 0.0, 0.5, 0.9, 0.1] * 4
    assert ch.analyze_envelopes([same, same])[0] == "ambience"
    print("SKIP: fluxo completo (ffmpeg ausente) | PASS: analyze_envelopes")
else:
    print("SKIP: partes com numpy (ambiente minimo)")

# --- render ignora channels.json de outra versao de decisao ---
with tempfile.TemporaryDirectory() as tmp:
    config, paths = project(Path(tmp))
    write_json(ch.channels_json_path(paths, "Z01"),
               {"decision": "ambience", "segments": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]})
    assert load_external_diarization(paths, "Z01", dict(config, diarization_source="channels")) == []
    assert load_external_diarization(paths, "SEM_ARQUIVO", dict(config, diarization_source="channels")) == []
print("PASS: load_external_diarization (fonte channels)")

print("PASS: toy_channels")
