"""Toy test: analise de canais (fase 4) — partes puras + smoke sem torch."""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import channels as ch
from transcribe_pipeline.audio import channel_wav_commands
from transcribe_pipeline.config import ensure_directories, load_config, make_paths
from transcribe_pipeline.render import load_external_diarization
from transcribe_pipeline.utils import read_json, write_json

# --- channel_wav_commands: montagem pura, teto de canais ---
wav = Path("C:/proj/Transcricoes/01_audio_wav16k_mono/X01.wav")
commands = channel_wav_commands("ffmpeg", Path("C:/proj/a.mp3"), wav, 2, 16000, force=False)
assert len(commands) == 2
target0, command0 = commands[0]
assert target0.name == "X01.ch0.wav" and commands[1][0].name == "X01.ch1.wav"
assert "pan=mono|c0=c0" in command0 and "-n" in command0 and "16000" in command0
assert len(channel_wav_commands("ffmpeg", Path("s"), wav, 99, 16000, True)) == ch.MAX_CHANNELS
assert "-y" in channel_wav_commands("ffmpeg", Path("s"), wav, 1, 16000, True)[0][1]
print("PASS: channel_wav_commands")

# --- mean_embedding ---
centroid = ch.mean_embedding([[3.0, 0.0], [1.0, 0.0]])
assert centroid == [1.0, 0.0]
assert ch.mean_embedding([]) is None
assert ch.mean_embedding([[1.0], [1.0, 2.0]]) is None
assert ch.mean_embedding([[0.0, 0.0]]) is None
print("PASS: mean_embedding")

# --- channel_speaker_map: casamento 1:1, sobras, sem pyannote ---
mapping = ch.channel_speaker_map(
    2,
    {0: [0.0, 1.0], 1: [1.0, 0.0]},
    {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]},
)
assert mapping == {0: "SPEAKER_01", 1: "SPEAKER_00"}  # cruzado pelo cosseno
mapping = ch.channel_speaker_map(3, {0: [1.0, 0.0]}, {"SPEAKER_00": [1.0, 0.0]})
assert mapping[0] == "SPEAKER_00" and mapping[1] == "SPEAKER_01" and mapping[2] == "SPEAKER_02"
mapping = ch.channel_speaker_map(2, {}, {})
assert mapping == {0: "SPEAKER_00", 1: "SPEAKER_01"}  # ordem do canal
print("PASS: channel_speaker_map")

# --- segments_from_labels: fusao de janelas, minimo, gaps ---
labels = [0, 0, None, 1, 1, 1, None, None, 0]
segments = ch.segments_from_labels(labels, window_seconds=0.5)
assert segments == [
    {"start": 0.0, "end": 1.0, "channel": 0},
    {"start": 1.5, "end": 3.0, "channel": 1},
    {"start": 4.0, "end": 4.5, "channel": 0},
]
assert ch.segments_from_labels([]) == []
print("PASS: segments_from_labels")

try:
    import numpy  # noqa: F401 - so para detectar o ambiente minimo
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

def write_wav(path: Path, blocks: list[tuple[float, int]], rate: int = 16000) -> None:
    """blocks = [(segundos, amplitude int16)] concatenados, mono."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for seconds, amplitude in blocks:
            frames = int(rate * seconds)
            handle.writeframes(amplitude.to_bytes(2, "little", signed=True) * frames)

if HAS_NUMPY:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # canais informativos: ch0 fala 0-5s, ch1 fala 5-10s
        ch0 = root / "X01.ch0.wav"
        ch1 = root / "X01.ch1.wav"
        write_wav(ch0, [(5.0, 9000), (5.0, 0)])
        write_wav(ch1, [(5.0, 0), (5.0, 9000)])
        envelopes = ch.rms_envelopes([ch0, ch1])
        assert len(envelopes) == 2 and len(envelopes[0]) == 20
        decision, correlation, fraction, labels = ch.analyze_envelopes(envelopes)
        assert decision == "informative", (decision, correlation, fraction)
        assert correlation < 0.0 and fraction == 1.0
        segs = ch.segments_from_labels(labels)
        assert [ (s["start"], s["end"], s["channel"]) for s in segs ] == [(0.0, 5.0, 0), (5.0, 10.0, 1)]
        # estereo-ambiente: canais identicos
        same = root / "X02.ch0.wav"
        write_wav(same, [(2.0, 7000), (2.0, 0), (2.0, 7000)])
        envelopes2 = ch.rms_envelopes([same, same])
        decision2, correlation2, _f2, _l2 = ch.analyze_envelopes(envelopes2)
        assert decision2 == "ambience" and correlation2 >= 0.98
    print("PASS: rms_envelopes + analyze_envelopes")

    # --- smoke: run_channel_analysis num projeto temporario, sem pyannote ---
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(None)
        paths = make_paths(config, base_dir=Path(tmp))
        ensure_directories(paths)
        wav_rel = f"{paths.wav_dir.relative_to(paths.project_root)}/X01.wav".replace("\\", "/")
        write_wav(paths.wav_dir / "X01.ch0.wav", [(5.0, 9000), (5.0, 0)])
        write_wav(paths.wav_dir / "X01.ch1.wav", [(5.0, 0), (5.0, 9000)])
        rows = [{"interview_id": "X01", "selected": "true",
                 "wav_path": wav_rel, "source_path": "orig/X01.mp3"}]
        failures = ch.run_channel_analysis(rows, config, paths)
        assert failures == 0
        payload = read_json(ch.channels_json_path(paths, "X01"))
        assert payload["decision"] == "informative" and payload["n_channels"] == 2
        speakers = {s["speaker"] for s in payload["segments"]}
        assert speakers == {"SPEAKER_00", "SPEAKER_01"}  # sem pyannote: ordem do canal
        # render le a fonte channels (contrato {start,end,speaker})
        segments = load_external_diarization(paths, "X01", dict(config, diarization_source="channels"))
        assert len(segments) == 2 and segments[0]["speaker"] == "SPEAKER_00"
        # ambience -> render ignora (fluxo atual)
        write_json(ch.channels_json_path(paths, "X02"),
                   {"decision": "ambience", "segments": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]})
        assert load_external_diarization(paths, "X02", dict(config, diarization_source="channels")) == []
        # mono (sem ch wavs): skip silencioso
        rows_mono = [{"interview_id": "M01", "selected": "true",
                      "wav_path": wav_rel.replace("X01", "M01"), "source_path": "orig/M01.mp3"}]
        assert ch.run_channel_analysis(rows_mono, config, paths) == 0
        assert not ch.channels_json_path(paths, "M01").exists()
    print("PASS: run_channel_analysis (smoke)")
else:
    print("SKIP: partes com numpy (ambiente minimo)")

print("PASS: toy_channels")
