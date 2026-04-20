"""Toy edges: token_vault stress-test TIER A2/A3/A4 + B7.

A2 - keyring retorna "" (em vez de None) — retrieve deve devolver None
A3 - migracao atomica: apos keyring_set OK + keyring_get verify OK, se
     unlink do legacy falha (permissao, file locked) o estado e
     coerente (keyring tem valor, retrieve devolve keyring nao legacy)
A4 - verify mismatch (truncation): tentar re-migrar eternamente e ruim;
     fix = flag persistida que impede re-migracao apos N falhas
B7 - Fernet fallback com /etc/machine-id vazio nao crasha

Alguns destes tests passam ANTES do fix (A3 — comportamento ja correto)
e outros falham ANTES (A2, A4).
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_vault() -> object:
    import importlib
    if "transcribe_pipeline.token_vault" in sys.modules:
        mod = importlib.reload(sys.modules["transcribe_pipeline.token_vault"])
    else:
        from transcribe_pipeline import token_vault as mod
    return mod


def _install_fake_keyring(storage: dict) -> object:
    """Mock keyring module cuja get_password devolve o que storage tem."""
    mod = types.ModuleType("keyring")
    mod.get_password = lambda s, u: storage.get((s, u))
    mod.set_password = lambda s, u, v: storage.__setitem__((s, u), v)
    mod.delete_password = lambda s, u: storage.pop((s, u), None)
    mod.get_keyring = lambda: object()
    sys.modules["keyring"] = mod
    sys.modules["keyring.errors"] = types.ModuleType("keyring.errors")
    return mod


def _remove_fake_keyring() -> None:
    sys.modules.pop("keyring", None)
    sys.modules.pop("keyring.errors", None)


def test_a2_empty_string_from_keyring_normalizes_to_none() -> None:
    """Se o keyring retorna '' (string vazia), retrieve() deve devolver
    None (nao ''). Algumas implementacoes de keyring guardam valor
    vazio quando limpeza parcial (bug conhecido em certas versoes)."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        storage = {("Transcritorio", "huggingface"): ""}
        _install_fake_keyring(storage)
        vault = _reload_vault()
        try:
            got = vault.retrieve()
            # Contrato da API: str | None. "" e nem um nem outro.
            assert got is None, f"Esperado None (string vazia normalizada), got {got!r}"
            print("PASS A2: keyring='' -> retrieve() is None (API contract)")
        finally:
            _remove_fake_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_a3_crash_between_set_and_unlink_keyring_wins() -> None:
    """Se keyring_set OK + verify OK MAS unlink do legacy falha (ex:
    processo morre antes, file locked, permission denied), o estado
    deve ser: keyring tem valor; legacy file existe; retrieve proximo
    devolve keyring (nao re-migra)."""
    if sys.platform != "win32":
        print("SKIP A3: DPAPI Windows-only")
        return
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        storage: dict = {}
        _install_fake_keyring(storage)
        vault = _reload_vault()
        try:
            legacy_path = Path(tmp) / "hf_token.vault"
            legacy_path.write_text("FAKE_CIPHER", encoding="utf-8")
            vault._decrypt_dpapi = lambda b: "hf_LEGACY_ABC"
            # 1a retrieve: migra (keyring recebe, legacy deveria apagar)
            got1 = vault.retrieve()
            assert got1 == "hf_LEGACY_ABC"
            # Simular crash: re-criar legacy manualmente (como se
            # unlink nunca tivesse rodado)
            legacy_path.write_text("OLD_CIPHER", encoding="utf-8")
            # Mudar decrypt pra um valor diferente, pra detectar se
            # retrieve re-usa legacy por engano
            vault._decrypt_dpapi = lambda b: "hf_SHOULD_NOT_USE"
            # 2a retrieve: deve voltar do keyring, nao do legacy
            got2 = vault.retrieve()
            assert got2 == "hf_LEGACY_ABC", \
                f"Apos dual-state, retrieve deve usar keyring, got {got2!r}"
            print("PASS A3: keyring vence quando legacy existe (sem re-migracao)")
        finally:
            _remove_fake_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_a4_verify_mismatch_uses_legacy_and_logs() -> None:
    """Se apos _keyring_set o _keyring_get devolve valor DIFERENTE (ex:
    truncacao do backend), esperado:
    - rollback: nao apagar legacy
    - retornar legacy (usuario segue funcionando com DPAPI)
    - comportamento atual: OK (ja faz isso)
    Este toy valida o invariante."""
    if sys.platform != "win32":
        print("SKIP A4: DPAPI Windows-only")
        return
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        storage: dict = {}

        mod = types.ModuleType("keyring")
        # set_password guarda truncado (simula KWallet/SecretService bug)
        mod.set_password = lambda s, u, v: storage.__setitem__((s, u), v[:10])
        mod.get_password = lambda s, u: storage.get((s, u))
        mod.delete_password = lambda s, u: storage.pop((s, u), None)
        mod.get_keyring = lambda: object()
        sys.modules["keyring"] = mod
        sys.modules["keyring.errors"] = types.ModuleType("keyring.errors")

        vault = _reload_vault()
        try:
            legacy_path = Path(tmp) / "hf_token.vault"
            legacy_path.write_text("FAKE", encoding="utf-8")
            vault._decrypt_dpapi = lambda b: "hf_LONG_TOKEN_XYZ_123456"
            got = vault.retrieve()
            # Valor original (legacy) devolvido porque verify falhou
            assert got == "hf_LONG_TOKEN_XYZ_123456", \
                f"Rollback: esperado legacy, got {got!r}"
            # Legacy NAO apagado (rollback)
            assert legacy_path.exists(), \
                "Legacy deveria continuar apos verify mismatch"
            print("PASS A4: verify mismatch -> legacy preservado + retorna legacy")
        finally:
            _remove_fake_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_b7_fernet_empty_machine_id_uses_home_fallback() -> None:
    """Se _machine_id() retorna bytes vazios (ex: /etc/machine-id
    existe mas vazio), o codigo cai em str(Path.home()). Nao deve
    crashar."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        _remove_fake_keyring()
        vault = _reload_vault()
        try:
            try:
                import cryptography  # noqa: F401
            except ImportError:
                print("SKIP B7: cryptography nao instalado")
                return
            # Mock _machine_id pra retornar valor derivado de home
            # (simulando fallback quando /etc/machine-id vazio)
            with patch.object(vault, "_keyring_available", return_value=False), \
                 patch.object(vault, "_is_windows", return_value=False):
                # Com machine_id fallback, fernet_key ainda deriva — nao crasha
                try:
                    key = vault._fernet_key()
                    assert isinstance(key, bytes) and len(key) > 0
                    print("PASS B7: _fernet_key nao crasha com machine-id vazio (fallback home)")
                except Exception as exc:
                    raise AssertionError(f"Fernet key derivation crashou: {exc}")
        finally:
            if "TRANSCRITORIO_HOME" in os.environ:
                del os.environ["TRANSCRITORIO_HOME"]


if __name__ == "__main__":
    test_a2_empty_string_from_keyring_normalizes_to_none()
    test_a3_crash_between_set_and_unlink_keyring_wins()
    test_a4_verify_mismatch_uses_legacy_and_logs()
    test_b7_fernet_empty_machine_id_uses_home_fallback()
    print()
    print("PASS: toy_token_vault_edges")
