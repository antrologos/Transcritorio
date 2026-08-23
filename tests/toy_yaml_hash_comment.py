"""Toy test para config._load_simple_yaml: '#' dentro de valores.

Bug original: raw_line.split("#", 1) truncava QUALQUER '#', inclusive em
paths gravados pela propria GUI (ex.: C:\\audios\\take#3.wav), que sumiam
silenciosamente do projeto no proximo open.

Regra corrigida (paridade com YAML real): '#' so inicia comentario no comeco
da linha ou precedido de espaco.

Sem dependencias pesadas — roda com stdlib pura.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline.config import _load_simple_yaml, write_config

# 1. '#' dentro de valor e preservado
result = _load_simple_yaml("audio: C:\\audios\\take#3.wav\n")
assert result["audio"] == "C:\\audios\\take#3.wav", result

# 2. Comentario de fim de linha (' #') continua removido
result = _load_simple_yaml("batch_size: 4  # comentario\n")
assert result["batch_size"] == 4, result

# 3. Linha so-comentario continua ignorada (com e sem indentacao)
result = _load_simple_yaml("# cabecalho\nkey: valor\n  # indentado\n")
assert result == {"key": "valor"}, result

# 4. Item de lista com '#' no valor e preservado
result = _load_simple_yaml("audio_files:\n  - C:\\pasta\\take#3.wav\n  - normal.wav\n")
assert result["audio_files"] == ["C:\\pasta\\take#3.wav", "normal.wav"], result

# 5. Round-trip write_config -> _load_simple_yaml com '#' no path
with tempfile.TemporaryDirectory() as tmp:
    cfg_path = Path(tmp) / "run_config.yaml"
    original = {
        "language": "pt",
        "batch_size": 4,
        "audio_files": ["C:\\audios\\take#3.wav"],
        "flag": True,
        "nothing": None,
    }
    write_config(cfg_path, original)
    loaded = _load_simple_yaml(cfg_path.read_text(encoding="utf-8"))
    assert loaded["language"] == "pt", loaded
    assert loaded["batch_size"] == 4, loaded
    assert loaded["audio_files"] == ["C:\\audios\\take#3.wav"], loaded
    assert loaded["flag"] is True, loaded
    assert loaded["nothing"] is None, loaded

# 6. Header de write_config (linhas '# ...') continua ignorado no load
with tempfile.TemporaryDirectory() as tmp:
    cfg_path = Path(tmp) / "run_config.yaml"
    write_config(cfg_path, {"key": "valor"}, header=["# gerado pelo Transcritorio"])
    loaded = _load_simple_yaml(cfg_path.read_text(encoding="utf-8"))
    assert loaded == {"key": "valor"}, loaded

print("PASS: toy_yaml_hash_comment")
