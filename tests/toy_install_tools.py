"""Toy test para install_tools (canal uv/PyPI, v0.2).

Valida comandos de reparo/atualizacao/CUDA, flag cuda_extra_installed e o
guard do ensure_first_run_setup (nunca levanta; nao roda em modo frozen).

Sem dependencias pesadas; app_settings e redirecionado para um tmpdir para
NAO tocar o app_settings.json real da maquina.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcribe_pipeline import app_settings, install_tools

with tempfile.TemporaryDirectory() as tmp:
    # Isolar as preferencias reais da maquina
    app_settings._settings_path = lambda: Path(tmp) / "app_settings.json"

    # Comandos
    assert install_tools.upgrade_command() == "uv tool upgrade transcritorio"
    assert install_tools.repair_command(cuda=False) == 'uv tool install --reinstall "transcritorio"'
    assert install_tools.repair_command(cuda=True) == 'uv tool install --reinstall "transcritorio[cuda]"'
    assert install_tools.cuda_install_command() == 'uv tool install --reinstall "transcritorio[cuda]"'
    print("PASS: comandos uv corretos")

    # Flag cuda persiste e o default do reparo o segue
    assert install_tools.cuda_extra_installed() is False
    assert install_tools.repair_command() == 'uv tool install --reinstall "transcritorio"'
    install_tools.mark_cuda_extra_installed(True)
    assert install_tools.cuda_extra_installed() is True
    assert install_tools.repair_command() == 'uv tool install --reinstall "transcritorio[cuda]"'
    print("PASS: flag cuda_extra_installed persiste e muda o reparo")

    # app_settings round-trip + arquivo corrompido nunca crasha
    app_settings.save({"diarize_default": False})
    assert app_settings.diarize_default() is False
    app_settings._settings_path().write_text("nao-e-json{", encoding="utf-8")
    assert app_settings.load() == {}
    assert app_settings.diarize_default() is True  # default seguro
    print("PASS: app_settings round-trip + corrompido -> default")

    # ensure_first_run_setup nunca levanta (frozen simulado e nao-frozen)
    _orig_frozen = getattr(sys, "frozen", None)
    try:
        sys.frozen = True  # type: ignore[attr-defined]
        install_tools.ensure_first_run_setup()  # frozen: no-op
        assert not app_settings.load().get("shortcut_created")
        del sys.frozen  # type: ignore[attr-defined]
        # nao-frozen sem shim no PATH: no-op silencioso
        install_tools._gui_launcher_path = lambda: None
        install_tools.ensure_first_run_setup()
        assert not app_settings.load().get("shortcut_created")
    finally:
        if _orig_frozen is not None:
            sys.frozen = _orig_frozen  # type: ignore[attr-defined]
        elif hasattr(sys, "frozen"):
            del sys.frozen  # type: ignore[attr-defined]
    print("PASS: ensure_first_run_setup guards (frozen / sem shim)")

print("PASS: toy_install_tools")
