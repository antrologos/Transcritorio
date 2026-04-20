"""Toy test: token_vault refatorado para keyring (com mocks).

Valida:
- store/retrieve/clear usam keyring quando disponivel
- Migracao atomica DPAPI -> keyring na primeira chamada de retrieve():
  1. Le DPAPI legacy
  2. Escreve no keyring
  3. Le de volta e compara
  4. Apaga DPAPI so se tudo ok
- Se keyring falha: fallback fernet (Linux headless) / DPAPI (Windows)
- API publica nao muda: store(str)->None, retrieve()->str|None, clear()->None
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_vault() -> object:
    """Importa (ou reimporta) token_vault, retorna o modulo."""
    import importlib
    if "transcribe_pipeline.token_vault" in sys.modules:
        mod = importlib.reload(sys.modules["transcribe_pipeline.token_vault"])
    else:
        from transcribe_pipeline import token_vault as mod
    return mod


def _fake_keyring() -> tuple[object, dict]:
    """Instala um fake keyring no sys.modules. Retorna (module, storage_dict)."""
    storage: dict = {}
    mod = types.ModuleType("keyring")

    class _BackendOk:
        pass

    def get_password(service, user):
        return storage.get((service, user))

    def set_password(service, user, value):
        storage[(service, user)] = value

    def delete_password(service, user):
        if (service, user) in storage:
            del storage[(service, user)]
        else:
            raise Exception("not found")

    def get_keyring():
        return _BackendOk()

    mod.get_password = get_password
    mod.set_password = set_password
    mod.delete_password = delete_password
    mod.get_keyring = get_keyring
    sys.modules["keyring"] = mod

    errors_mod = types.ModuleType("keyring.errors")

    class PasswordDeleteError(Exception):
        pass

    errors_mod.PasswordDeleteError = PasswordDeleteError
    sys.modules["keyring.errors"] = errors_mod
    return mod, storage


def _remove_keyring() -> None:
    sys.modules.pop("keyring", None)
    sys.modules.pop("keyring.errors", None)


def test_store_retrieve_clear_via_keyring() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        _, storage = _fake_keyring()
        try:
            vault = _reload_vault()
            vault.store("hf_TOKEN_123")
            assert ("Transcritorio", "huggingface") in storage
            assert vault.retrieve() == "hf_TOKEN_123"
            vault.clear()
            assert vault.retrieve() is None
            print("PASS token_vault: store/retrieve/clear via keyring")
        finally:
            _remove_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_dpapi_legacy_migration() -> None:
    """Se ha vault DPAPI legado e keyring vazio, migrar atomicamente."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        _, storage = _fake_keyring()
        # Criar vault legado: arquivo hf_token.vault com valor "mock"
        legacy_path = Path(tmp) / "hf_token.vault"
        legacy_path.write_text("LEGACY_BLOB_BASE64", encoding="utf-8")
        # Mockar _decrypt_dpapi para retornar um token conhecido
        vault = _reload_vault()
        original_decrypt = getattr(vault, "_decrypt_dpapi", None)
        vault._decrypt_dpapi = lambda b: "hf_LEGACY_DPAPI"
        try:
            got = vault.retrieve()
            assert got == "hf_LEGACY_DPAPI", f"got {got!r}"
            # DPAPI legado deve ter sido apagado
            assert not legacy_path.exists(), "legacy vault nao foi apagado apos migracao"
            # Deve ter sido escrito no keyring
            assert storage.get(("Transcritorio", "huggingface")) == "hf_LEGACY_DPAPI"
            print("PASS token_vault: migracao DPAPI -> keyring atomica (apaga legacy)")
        finally:
            if original_decrypt is not None:
                vault._decrypt_dpapi = original_decrypt
            _remove_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_migration_rollback_on_keyring_failure() -> None:
    """Se keyring falha durante write, DPAPI legacy NAO pode ser apagado."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        mod, storage = _fake_keyring()
        # Fazer set_password falhar
        def bad_set(service, user, value):
            raise RuntimeError("keyring is locked")
        mod.set_password = bad_set
        legacy_path = Path(tmp) / "hf_token.vault"
        legacy_path.write_text("LEGACY_BLOB_BASE64", encoding="utf-8")
        vault = _reload_vault()
        vault._decrypt_dpapi = lambda b: "hf_LEGACY_DPAPI"
        try:
            # Deve retornar o valor decifrado do DPAPI (fallback) mas manter o arquivo
            got = vault.retrieve()
            assert got == "hf_LEGACY_DPAPI", f"esperava fallback DPAPI, got {got!r}"
            assert legacy_path.exists(), "legacy foi apagado indevidamente apos falha de migracao"
            print("PASS token_vault: rollback preserva DPAPI legacy se keyring falha")
        finally:
            _remove_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


def test_fallback_fernet_when_keyring_unavailable() -> None:
    """Se keyring nao disponivel, cair em fernet local (Linux headless).

    Mocka _keyring_available=False (nao basta popar do sys.modules se o
    pacote keyring estiver instalado no ambiente — ele seria re-importado
    dentro de _keyring_available)."""
    _remove_keyring()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        try:
            vault = _reload_vault()
            try:
                import cryptography  # noqa: F401
            except ImportError:
                print("SKIP token_vault fallback fernet: cryptography nao instalado")
                return
            # Forcar: nao-Windows (evita DPAPI) + keyring ausente (forca fernet)
            with patch.object(vault, "_is_windows", return_value=False), \
                 patch.object(vault, "_keyring_available", return_value=False):
                vault.store("hf_FERNET_TOKEN")
                got = vault.retrieve()
                assert got == "hf_FERNET_TOKEN", f"got {got!r}"
                fb = Path(tmp) / "hf_token.fallback"
                assert fb.exists(), "arquivo fallback nao criado"
                vault.clear()
                assert vault.retrieve() is None
                assert not fb.exists()
                print("PASS token_vault: fallback fernet quando keyring indisponivel")
        finally:
            if "TRANSCRITORIO_HOME" in os.environ:
                del os.environ["TRANSCRITORIO_HOME"]


def test_api_compat_noop_when_nothing_stored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRANSCRITORIO_HOME"] = tmp
        _, _storage = _fake_keyring()
        try:
            vault = _reload_vault()
            assert vault.retrieve() is None
            vault.clear()  # No-op, nao lanca
            print("PASS token_vault: retrieve()=None quando vazio; clear() no-op")
        finally:
            _remove_keyring()
            del os.environ["TRANSCRITORIO_HOME"]


if __name__ == "__main__":
    test_store_retrieve_clear_via_keyring()
    # DPAPI migration e feature Windows-only (ctypes.crypt32).
    # Em Linux/Mac a logica de migracao nao dispara (is_windows=False
    # bloqueia o branch em retrieve()). Skip silencioso.
    if sys.platform == "win32":
        test_dpapi_legacy_migration()
        test_migration_rollback_on_keyring_failure()
    else:
        print("SKIP test_dpapi_legacy_migration: DPAPI e Windows-only")
        print("SKIP test_migration_rollback_on_keyring_failure: DPAPI e Windows-only")
    test_fallback_fernet_when_keyring_unavailable()
    test_api_compat_noop_when_nothing_stored()
    print()
    print("PASS: toy_token_vault_keyring")
