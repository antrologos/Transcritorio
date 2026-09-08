"""Toy: a preparacao de modelos usa o token do cofre mesmo quando nao o pediu — 2026-09-07.

O ModelSetupDialog so PEDE token quando falta um modelo restrito. No
incidente de 2026-09-05 faltava so o pacote de espanhol (aberto): o dialogo
devolveu "" e o lote saiu sem token — mas re-verificava o pyannote
(restrito) tambem. Com a guarda de cache (toy_pre_voo_pula_cache) isso ja
nao vai a rede; ainda assim, quando VAI (force, cache parcial), o token que
a pessoa ja guardou tem de ser usado. E o que show_model_setup faz agora:
dialogo sem token -> token_vault.retrieve().

Testa o ROTEAMENTO do token, nao o download: dialogo, cofre e worker sao
substituidos por dublês. Precisa de PySide6. Roda offscreen.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TRANSCRITORIO_HOME"] = tempfile.mkdtemp()

try:
    from PySide6.QtWidgets import QApplication, QDialog
except ImportError:  # pragma: no cover
    print("SKIP: toy_model_setup_token_cofre (PySide6 ausente)")
    raise SystemExit(0)

app = QApplication.instance() or QApplication([])

tmp = Path(tempfile.mkdtemp())
from transcribe_pipeline.config import (  # noqa: E402
    DEFAULT_CONFIG, ensure_directories, make_paths, write_config)

config = dict(DEFAULT_CONFIG)
config["project_root"] = str(tmp)
paths = make_paths(config, base_dir=tmp)
ensure_directories(paths)
(paths.output_root / "00_project").mkdir(parents=True, exist_ok=True)
with (paths.manifest_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as h:
    csv.DictWriter(h, fieldnames=["interview_id", "source_path", "selected"]).writeheader()
(paths.manifest_dir / "speakers_map.csv").write_text(
    "interview_id,speaker_id,role\n", encoding="utf-8-sig")
write_config(paths.config_dir / "run_config.yaml", config, header=["# toy_token_cofre"])

import transcribe_pipeline.review_studio_qt as rsq  # noqa: E402
from transcribe_pipeline import app_service, token_vault  # noqa: E402

win = rsq.ReviewStudioWindow(project_root=tmp)


class DialogoFalso:
    """Aceita sem pedir token — o caso "so falta um modelo aberto"."""

    def __init__(self, *_a, **_k) -> None:
        pass

    def exec(self):
        return QDialog.DialogCode.Accepted

    def token(self) -> str:
        return ""


capturado: dict = {}


def worker_falso(label, steps, *_a, **_k) -> None:
    # Executa o passo na hora, com progresso e cancelamento falsos.
    capturado["label"] = label
    steps[0][1](lambda _evt: None, lambda: False)


def download_falso(**kw):
    capturado["token"] = kw.get("token")
    return app_service.JobResult("models", 0, "ok")


_orig = (rsq.ModelSetupDialog, token_vault.retrieve, app_service.download_models, win.start_worker)
rsq.ModelSetupDialog = DialogoFalso
app_service.download_models = download_falso
win.start_worker = worker_falso
try:
    # 1. Com token no cofre: ele vai para o download, mesmo sem ter sido pedido.
    token_vault.retrieve = lambda: "hf_do_cofre"
    capturado.clear()
    win.show_model_setup()
    assert capturado.get("label") == "Preparar modelos", capturado
    assert capturado.get("token") == "hf_do_cofre", capturado
    print("PASS: dialogo sem token + cofre com token -> o download recebe o do cofre")

    # 2. Cofre vazio: segue sem token (como antes), sem quebrar.
    token_vault.retrieve = lambda: None
    capturado.clear()
    win.show_model_setup()
    assert capturado.get("token") == "", capturado
    print("PASS: cofre vazio -> download sem token, sem erro")

    # 3. Cofre que levanta (nao deveria, por regra — mas o cinto existe).
    def explode():
        raise RuntimeError("keyring indisponivel")

    token_vault.retrieve = explode
    capturado.clear()
    win.show_model_setup()
    assert capturado.get("token") == "", capturado
    print("PASS: cofre que levanta nao derruba a preparacao")
finally:
    rsq.ModelSetupDialog, token_vault.retrieve, app_service.download_models, win.start_worker = _orig

print("PASS: toy_model_setup_token_cofre")
