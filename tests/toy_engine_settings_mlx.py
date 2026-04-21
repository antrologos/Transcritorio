"""Toy test for EngineSettingsDialog device_combo population (case E).

Validates that the MLX option is offered only when detect_device() returns
"mps". On Windows/Linux without MPS, only CUDA and CPU are shown.

Uses offscreen Qt platform so no display is needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _get_dialog_device_options(detected_device: str) -> list[tuple[str, str]]:
    """Build an EngineSettingsDialog with a patched detect_device and return
    the list of (value, label) tuples in its device_combo."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from transcribe_pipeline import runtime, review_studio_qt, model_manager

    with patch.object(runtime, "detect_device", return_value=detected_device):
        # Patch installed_asr_variants so the dialog doesn't try to scan
        # real HF cache (some methods may hit disk).
        with patch.object(model_manager, "installed_asr_variants", return_value={}):
            dialog = review_studio_qt.EngineSettingsDialog(
                parent=None,
                config={"asr_model": "large-v3-turbo", "asr_device": "cuda"},
            )

    combo = dialog.device_combo
    options = []
    for i in range(combo.count()):
        options.append((combo.itemData(i), combo.itemText(i)))
    dialog.deleteLater()
    return options


def test_mps_surfaces_mlx_option() -> None:
    options = _get_dialog_device_options("mps")
    values = [v for v, _ in options]
    labels = [lbl for _, lbl in options]
    assert "mps" in values, f"MLX option missing when MPS detected: {options}"
    assert any("MLX" in lbl or "Metal" in lbl for lbl in labels), labels
    assert "cuda" in values and "cpu" in values, options
    print(f"PASS: MPS detected -> combo has {len(options)} options including MLX")


def test_no_mps_hides_mlx_option() -> None:
    options = _get_dialog_device_options("cuda")
    values = [v for v, _ in options]
    assert "mps" not in values, f"MLX option should be hidden without MPS: {options}"
    assert "cuda" in values and "cpu" in values
    print(f"PASS: non-MPS system -> combo has {len(options)} options, no MLX")


def test_no_mps_on_cpu_only_system() -> None:
    options = _get_dialog_device_options("cpu")
    values = [v for v, _ in options]
    assert "mps" not in values, options
    assert values == ["cuda", "cpu"], f"unexpected options: {options}"
    print("PASS: CPU-only system -> only cuda + cpu offered")


if __name__ == "__main__":
    test_mps_surfaces_mlx_option()
    test_no_mps_hides_mlx_option()
    test_no_mps_on_cpu_only_system()
    print("\nPASS: toy_engine_settings_mlx")
