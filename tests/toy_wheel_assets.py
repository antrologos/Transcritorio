"""Toy v0.2.0: icones viajam no wheel e o atalho ganha IconLocation.

Bug de canal: o wheel nao empacotava assets/ — o app instalado por uv
rodava sem icone de janela e o atalho herdava o icone do pythonw.
Cobre: (1) o wheel buildado DO REPO contem
transcribe_pipeline/assets/transcritorio_icon.ico; (2) app_asset_path
resolve no repo (fonte) e cai no fallback do pacote quando a raiz nao
existe; (3) o script do atalho inclui IconLocation quando ha icone.
Marcado como teste LENTO de build: roda uv build de verdade (~10 s);
skip silencioso se o uv nao estiver disponivel (CI minimo).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- pacote
assert (REPO / "transcribe_pipeline" / "assets" / "transcritorio_icon.ico").exists()
assert (REPO / "transcribe_pipeline" / "assets" / "transcritorio_icon.svg").exists()
print("PASS: icones copiados para dentro do pacote")

texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
assert '"assets/*"' in texto, "package-data sem assets/*"
print("PASS: package-data declara assets/*")

# ---------------------------------------------------------------- wheel
import shutil

uv = shutil.which("uv") or r"C:\Users\antro\AppData\Local\Microsoft\WinGet\Links\uv.exe"
if not Path(uv).exists():
    print("SKIP build: uv indisponivel")
else:
    # Buildar de uma COPIA fora do Dropbox: o setuptools usa ./build no
    # cwd e o sync do Dropbox segura arquivos recem-criados (WinError 32
    # visto em 2026-08-31). So o necessario para o wheel viaja.
    stage = Path(tempfile.mkdtemp())
    shutil.copy2(REPO / "pyproject.toml", stage / "pyproject.toml")
    shutil.copy2(REPO / "README.md", stage / "README.md")
    shutil.copytree(REPO / "transcribe_pipeline", stage / "transcribe_pipeline",
                    ignore=shutil.ignore_patterns("__pycache__"))
    outdir = Path(tempfile.mkdtemp())
    completed = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(outdir)],
        cwd=str(stage), capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr[-800:]
    wheel = next(outdir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        nomes = zf.namelist()
    assert "transcribe_pipeline/assets/transcritorio_icon.ico" in nomes, \
        [n for n in nomes if "assets" in n]
    print(f"PASS: wheel contem o .ico ({wheel.name})")

# ------------------------------------------------------- app_asset_path
from transcribe_pipeline.review_studio_qt import app_asset_path

# Rodando da fonte: resolve assets/ da raiz do repo (como sempre)
p = app_asset_path("transcritorio_icon.ico")
assert p == REPO / "assets" / "transcritorio_icon.ico", p
# Fallback do wheel: para um arquivo que SO existe dentro do pacote,
# a funcao devolve o caminho do pacote (raiz nao tem -> parent/assets)
sentinela = REPO / "transcribe_pipeline" / "assets" / "_toy_sentinela.txt"
sentinela.write_text("x", encoding="utf-8")
try:
    p2 = app_asset_path("_toy_sentinela.txt")
    assert p2 == sentinela, p2
finally:
    sentinela.unlink()
print("PASS: app_asset_path (fonte + fallback do pacote)")

# ------------------------------------------------------- atalho c/ icone
from transcribe_pipeline.install_tools import _icon_path, _shortcut_script

icone = _icon_path()
assert icone.endswith("transcritorio_icon.ico"), icone

script = _shortcut_script(r"C:\py\pythonw.exe", "Transcritório",
                          "-m transcribe_pipeline.gui_launcher", icone)
assert "$s.IconLocation = '" in script and icone in script
assert "$s.Arguments = '-m transcribe_pipeline.gui_launcher'" in script
# Sem icone: linha de IconLocation NAO aparece (fallback limpo)
script2 = _shortcut_script(r"C:\x.exe", "Transcritório", "", "")
assert "IconLocation" not in script2
print("PASS: script do atalho com IconLocation (e sem, quando ausente)")

print("PASS: toy_wheel_assets")
