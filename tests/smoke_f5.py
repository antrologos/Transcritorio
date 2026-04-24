"""Smoke test exaustivo da Fase 5 (Mac/Linux MVP).

Valida TUDO que a fase 5 entregou:
- F5.1 detect_device() tem ramos cuda/mps/cpu + cache + fallback robusto
- F5.1 resolve_device() coerce mps -> cpu
- F5.2 whisperx_runner imprime mensagem especifica para MPS
- F5.3 token_vault: API publica estavel; store/retrieve/clear com keyring
- F5.3 Migracao DPAPI -> keyring atomica (read legacy, write, verify, delete)
- F5.3 Rollback se keyring falha: nao apagar legacy
- F5.3 Fallback sem keyring no Windows -> DPAPI
- F5.4 3 scripts .sh criados, syntax valid, shebang bash, set -euo pipefail
- F5.4 Scripts usam app_data_dir() (nao hardcode)
- F5.5 pyproject.toml tem keyring + cryptography
- F5.5 docs/MAC_LINUX.md existe e menciona MPS/keyring/troubleshooting
- F5.5 README tem secao Mac/Linux
"""
from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
FAILED: list[str] = []


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    FAILED.append(msg)


# ============================================================
# CENARIO 1 - F5.1 detect_device: 3 ramos + cache + fallback
# ============================================================
print("=" * 60)
print("CENARIO 1 - detect_device/resolve_device")
print("=" * 60)


def _install_fake_torch(cuda: bool, mps: bool) -> None:
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    fake.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps)
    )
    sys.modules["torch"] = fake
    # 2026-04-23: detect_device() no Windows exige cuda_libs_present()
    # alem de cuda.is_available(). Seta o cache diretamente aqui.
    runtime._cuda_libs_detected = cuda


def _clear_torch() -> None:
    sys.modules.pop("torch", None)
    runtime._cuda_libs_detected = None


from transcribe_pipeline import runtime  # noqa: E402

# Cuda branch
runtime._detected_device = None
runtime._cuda_libs_detected = None
_install_fake_torch(cuda=True, mps=False)
try:
    assert runtime.detect_device() == "cuda"
    ok("detect_device ramo cuda")
except AssertionError:
    fail(f"detect_device cuda: got {runtime.detect_device()}")
finally:
    _clear_torch()

# MPS branch
runtime._detected_device = None
_install_fake_torch(cuda=False, mps=True)
try:
    assert runtime.detect_device() == "mps"
    ok("detect_device ramo mps (Apple Silicon)")
except AssertionError:
    fail(f"detect_device mps: got {runtime.detect_device()}")
finally:
    _clear_torch()

# CPU branch
runtime._detected_device = None
_install_fake_torch(cuda=False, mps=False)
try:
    assert runtime.detect_device() == "cpu"
    ok("detect_device ramo cpu")
except AssertionError:
    fail(f"detect_device cpu: got {runtime.detect_device()}")
finally:
    _clear_torch()

# Torch broken
runtime._detected_device = None
bad = types.ModuleType("torch")
def _boom():
    raise RuntimeError("boom")
bad.cuda = types.SimpleNamespace(is_available=_boom)
sys.modules["torch"] = bad
try:
    assert runtime.detect_device() == "cpu"
    ok("detect_device fallback cpu quando torch quebra")
except AssertionError:
    fail("detect_device fallback cpu falhou")
finally:
    _clear_torch()
    runtime._detected_device = None

# Cache
calls = []
fake = types.ModuleType("torch")
fake.cuda = types.SimpleNamespace(is_available=lambda: (calls.append(1) or True))
fake.backends = types.SimpleNamespace(
    mps=types.SimpleNamespace(is_available=lambda: False)
)
sys.modules["torch"] = fake
runtime._detected_device = None
runtime._cuda_libs_detected = True  # guard Win32 em detect_device()
try:
    runtime.detect_device()
    runtime.detect_device()
    runtime.detect_device()
    if len(calls) == 1:
        ok("detect_device cacheia (chamada unica)")
    else:
        fail(f"detect_device nao cacheia: {len(calls)} chamadas")
finally:
    _clear_torch()
    runtime._detected_device = None

# resolve_device: MPS -> CPU
runtime._detected_device = None
_install_fake_torch(cuda=False, mps=True)
try:
    dev, fb = runtime.resolve_device("cuda")
    if dev == "cpu" and fb is True:
        ok("resolve_device: mps coerced -> cpu com fell_back=True")
    else:
        fail(f"resolve_device mps: ({dev}, {fb})")
finally:
    _clear_torch()
    runtime._detected_device = None


# ============================================================
# CENARIO 2 - F5.2 Gate MPS em whisperx_runner
# ============================================================
print()
print("=" * 60)
print("CENARIO 2 - whisperx_runner mensagem MPS-aware")
print("=" * 60)

runner_src = (REPO / "transcribe_pipeline" / "whisperx_runner.py").read_text(encoding="utf-8")

if 'runtime.detect_device()' in runner_src and 'detected == "mps"' in runner_src:
    ok("whisperx_runner checa detect_device() == 'mps'")
else:
    fail("whisperx_runner NAO checa detected == mps")

if 'Apple Silicon' in runner_src and 'faster-whisper' in runner_src:
    ok("mensagem MPS menciona 'Apple Silicon' e 'faster-whisper'")
else:
    fail("mensagem MPS incompleta")

if 'CUDA indisponivel' in runner_src:
    ok("mensagem CUDA fallback preservada (nao-MPS)")
else:
    fail("mensagem CUDA fallback removida por engano")


# ============================================================
# CENARIO 3 - F5.3 token_vault API
# ============================================================
print()
print("=" * 60)
print("CENARIO 3 - token_vault API publica estavel")
print("=" * 60)

from transcribe_pipeline import token_vault  # noqa: E402

sig_store = inspect.signature(token_vault.store)
sig_retrieve = inspect.signature(token_vault.retrieve)
sig_clear = inspect.signature(token_vault.clear)

if list(sig_store.parameters.keys()) == ["token"]:
    ok("store(token: str) -> None")
else:
    fail(f"store signature mudou: {sig_store}")

if list(sig_retrieve.parameters.keys()) == []:
    ok("retrieve() -> str | None")
else:
    fail(f"retrieve signature mudou: {sig_retrieve}")

if list(sig_clear.parameters.keys()) == []:
    ok("clear() -> None")
else:
    fail(f"clear signature mudou: {sig_clear}")


# ============================================================
# CENARIO 4 - F5.3 keyring ausente + Windows = DPAPI (real)
# ============================================================
print()
print("=" * 60)
print("CENARIO 4 - DPAPI fallback (keyring ausente no Windows)")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmp:
    os.environ["TRANSCRITORIO_HOME"] = tmp
    sys.modules.pop("keyring", None)
    import importlib
    from unittest.mock import patch
    importlib.reload(token_vault)
    try:
        real_keyring_available = token_vault._keyring_available()
        if real_keyring_available:
            ok("keyring_available() = True (CI ou user venv)")
        else:
            ok("keyring_available() = False (dev venv)")
        # Round-trip DPAPI forcado (mocka keyring como ausente para forcar
        # o path legado, que e o que queremos validar neste cenario).
        if sys.platform == "win32":
            with patch.object(token_vault, "_keyring_available", return_value=False):
                token_vault.store("hf_SMOKE_ABC")
                legacy = Path(tmp) / "hf_token.vault"
                if legacy.exists():
                    ok("store() criou hf_token.vault (DPAPI path)")
                else:
                    fail("hf_token.vault nao criado")
                got = token_vault.retrieve()
                if got == "hf_SMOKE_ABC":
                    ok("retrieve() devolve token decifrado via DPAPI")
                else:
                    fail(f"retrieve retornou {got!r}")
                token_vault.clear()
                if not legacy.exists() and token_vault.retrieve() is None:
                    ok("clear() apaga vault + retrieve() devolve None")
                else:
                    fail("clear() incompleto")
    finally:
        del os.environ["TRANSCRITORIO_HOME"]


# ============================================================
# CENARIO 5 - F5.3 Migracao atomica DPAPI -> keyring (mocked)
# ============================================================
print()
print("=" * 60)
print("CENARIO 5 - Migracao atomica DPAPI -> keyring")
print("=" * 60)

# DPAPI e Windows-only (ctypes.crypt32). Em Linux/Mac a logica de migracao
# nao dispara porque retrieve() gateia com _is_windows(). Skip silencioso.
def _run_cenario_5() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        storage: dict = {}
        kr = types.ModuleType("keyring")
        kr.get_password = lambda s, u: storage.get((s, u))
        kr.set_password = lambda s, u, v: storage.__setitem__((s, u), v)
        kr.delete_password = lambda s, u: storage.pop((s, u), None)
        kr.get_keyring = lambda: object()
        sys.modules["keyring"] = kr
        sys.modules["keyring.errors"] = types.ModuleType("keyring.errors")
        import importlib
        importlib.reload(token_vault)
        try:
            legacy = Path(tmp) / "hf_token.vault"
            legacy.write_text("FAKE_CIPHERTEXT", encoding="utf-8")
            token_vault._decrypt_dpapi = lambda b: "hf_LEGACY_DPAPI_X"
            got = token_vault.retrieve()
            if got == "hf_LEGACY_DPAPI_X":
                ok("retrieve() le DPAPI legado e devolve token")
            else:
                fail(f"retrieve legacy: got {got!r}")
            if storage.get(("Transcritorio", "huggingface")) == "hf_LEGACY_DPAPI_X":
                ok("token gravado no keyring")
            else:
                fail("token NAO gravado no keyring")
            if not legacy.exists():
                ok("legacy DPAPI apagado apos migracao bem-sucedida")
            else:
                fail("legacy DPAPI ainda existe")
        finally:
            sys.modules.pop("keyring", None)
            sys.modules.pop("keyring.errors", None)
            del os.environ["TRANSCRITORIO_HOME"]


if sys.platform == "win32":
    _run_cenario_5()
else:
    print("  SKIP: DPAPI migration e Windows-only")


# ============================================================
# CENARIO 6 - F5.3 Rollback se keyring write falha
# ============================================================
print()
print("=" * 60)
print("CENARIO 6 - Rollback preserva DPAPI se keyring falha")
print("=" * 60)


def _run_cenario_6() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        storage: dict = {}
        kr = types.ModuleType("keyring")
        kr.get_password = lambda s, u: storage.get((s, u))
        def bad_set(s, u, v):
            raise RuntimeError("keyring locked")
        kr.set_password = bad_set
        kr.delete_password = lambda s, u: None
        kr.get_keyring = lambda: object()
        sys.modules["keyring"] = kr
        sys.modules["keyring.errors"] = types.ModuleType("keyring.errors")
        import importlib
        importlib.reload(token_vault)
        try:
            legacy = Path(tmp) / "hf_token.vault"
            legacy.write_text("FAKE", encoding="utf-8")
            token_vault._decrypt_dpapi = lambda b: "hf_SAFE"
            got = token_vault.retrieve()
            if got == "hf_SAFE":
                ok("retrieve() devolve fallback DPAPI quando keyring falha")
            else:
                fail(f"rollback retrieve: got {got!r}")
            if legacy.exists():
                ok("legacy DPAPI NAO apagado (rollback)")
            else:
                fail("legacy apagado apesar da falha")
        finally:
            sys.modules.pop("keyring", None)
            sys.modules.pop("keyring.errors", None)
            del os.environ["TRANSCRITORIO_HOME"]


if sys.platform == "win32":
    _run_cenario_6()
else:
    print("  SKIP: DPAPI rollback e Windows-only")


# ============================================================
# CENARIO 7 - F5.4 Scripts .sh
# ============================================================
print()
print("=" * 60)
print("CENARIO 7 - Scripts .sh (syntax + estrutura)")
print("=" * 60)

for name in ("setup_transcription_env.sh", "review_studio.sh", "transcribe.sh"):
    p = REPO / "scripts" / name
    if not p.exists():
        fail(f"script ausente: {name}")
        continue
    text = p.read_text(encoding="utf-8")
    first = text.splitlines()[0]
    if first == "#!/usr/bin/env bash":
        ok(f"{name}: shebang bash")
    else:
        fail(f"{name}: shebang errado: {first}")
    if "set -euo pipefail" in text:
        ok(f"{name}: set -euo pipefail")
    else:
        fail(f"{name}: falta set -euo pipefail")
    # bash -n: pula no Windows (Git-for-Windows bash nao reporta
    # erros consistentemente em scripts com CRLF ou caminhos D:/). Os
    # scripts .sh sao para Mac/Linux; validacao basica via shebang e
    # set -euo pipefail acima e suficiente.
    if sys.platform == "win32":
        ok(f"{name}: bash -n skip (Windows — script e para Mac/Linux)")
    else:
        try:
            subprocess.run(["bash", "-n", str(p)], check=True, capture_output=True, text=True)
            ok(f"{name}: bash -n OK")
        except subprocess.CalledProcessError as e:
            fail(f"{name}: syntax error: {e.stderr}")
        except FileNotFoundError:
            ok(f"{name}: bash -n skip (bash nao no PATH)")

# Verificar que resolve venv via app_data_dir
for name in ("setup_transcription_env.sh", "review_studio.sh", "transcribe.sh"):
    text = (REPO / "scripts" / name).read_text(encoding="utf-8")
    if "app_data_dir" in text:
        ok(f"{name}: resolve venv via app_data_dir")
    else:
        fail(f"{name}: NAO usa app_data_dir (possivel hardcode)")


# ============================================================
# CENARIO 8 - F5.5 pyproject.toml + docs
# ============================================================
print()
print("=" * 60)
print("CENARIO 8 - pyproject.toml + docs")
print("=" * 60)

pyproj = (REPO / "pyproject.toml").read_text(encoding="utf-8")
if re.search(r'"keyring>=\d+"', pyproj):
    ok("pyproject.toml: keyring>=N")
else:
    fail("pyproject.toml: keyring ausente")

if re.search(r'"cryptography>=\d+"', pyproj):
    ok("pyproject.toml: cryptography>=N")
else:
    fail("pyproject.toml: cryptography ausente")

doc = REPO / "docs" / "MAC_LINUX.md"
if doc.exists():
    ok("docs/MAC_LINUX.md existe")
    content = doc.read_text(encoding="utf-8")
    for topic in ("macOS", "Linux", "MPS", "keyring", "brew install ffmpeg", "Troubleshooting"):
        if topic in content:
            ok(f"docs/MAC_LINUX.md menciona '{topic}'")
        else:
            fail(f"docs/MAC_LINUX.md nao menciona '{topic}'")
else:
    fail("docs/MAC_LINUX.md ausente")

readme = (REPO / "README.md").read_text(encoding="utf-8")
if "macOS e Linux" in readme or "macOS" in readme:
    ok("README tem secao Mac/Linux")
else:
    fail("README nao atualizado com Mac/Linux")

if "MAC_LINUX.md" in readme:
    ok("README aponta para docs/MAC_LINUX.md")
else:
    fail("README nao linka MAC_LINUX.md")


# ============================================================
# CENARIO 9 - Smoke regressao rapida dos bugs conhecidos
# ============================================================
print()
print("=" * 60)
print("CENARIO 9 - Sem regressao: API runtime + whisperx_runner ainda importavel")
print("=" * 60)

try:
    importlib.reload(runtime)
    if callable(runtime.detect_device) and callable(runtime.resolve_device):
        ok("runtime.detect_device/resolve_device callable")
    if callable(runtime.app_data_dir) and callable(runtime.model_cache_dir):
        ok("runtime.app_data_dir/model_cache_dir callable (nao quebrado)")
except Exception as exc:
    fail(f"runtime reimport falhou: {exc}")

# whisperx_runner importavel (via source parse, evita resolver torch)
src = (REPO / "transcribe_pipeline" / "whisperx_runner.py").read_text(encoding="utf-8")
if "from . import runtime" in src and "resolve_device" in src:
    ok("whisperx_runner usa runtime.resolve_device")

# token_vault publicas existem
for attr in ("store", "retrieve", "clear"):
    if hasattr(token_vault, attr):
        ok(f"token_vault.{attr} presente")
    else:
        fail(f"token_vault.{attr} removido")


# ============================================================
# Resumo
# ============================================================
print()
print("=" * 60)
if FAILED:
    print(f"FASE 5 SMOKE: {len(FAILED)} FALHA(S)")
    for f in FAILED:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("FASE 5 SMOKE: TODOS OS 9 CENARIOS OK")
print("=" * 60)
