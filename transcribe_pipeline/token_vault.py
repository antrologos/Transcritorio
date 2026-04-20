"""Secure HF token storage via the OS credential store.

Uses `keyring` as the primary backend, which chooses the appropriate
credential store per OS:
    - Windows: WinVaultKeyring (DPAPI under the hood)
    - macOS: Keychain
    - Linux: SecretService (GNOME Keyring / KWallet)

Fallbacks:
    - Windows: if keyring is missing, use DPAPI via ctypes (legacy code path)
    - Linux headless ($DISPLAY empty and dbus unavailable): Fernet with key
      derived from machine-id via PBKDF2, stored in app_data_dir()/hf_token.fallback
      with 0600 permissions
    - Nothing available: raise OSError

Migration: on first retrieve() when legacy %LOCALAPPDATA%/Transcritorio/hf_token.vault
exists and the keyring is empty, atomically migrate the token to the keyring
(read DPAPI -> write keyring -> verify -> delete legacy vault). Any failure
rolls back (the legacy vault stays intact).

Public API is stable:
    - store(token: str) -> None
    - retrieve() -> str | None
    - clear() -> None
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import os
import sys
from pathlib import Path

from . import runtime

_SERVICE = "Transcritorio"
_USER = "huggingface"
_LEGACY_FILENAME = "hf_token.vault"
_FALLBACK_FILENAME = "hf_token.fallback"


def _is_windows() -> bool:
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# DPAPI (Windows-only, legacy + fallback when keyring absent)
# ---------------------------------------------------------------------------

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _encrypt_dpapi(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    blob_in = _DataBlob(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(encrypted).decode("ascii")


def _decrypt_dpapi(ciphertext_b64: str) -> str:
    data = base64.b64decode(ciphertext_b64)
    blob_in = _DataBlob(len(data), ctypes.create_string_buffer(data, len(data)))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    plaintext = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return plaintext.decode("utf-8")


def _legacy_path() -> Path:
    return runtime.app_data_dir() / _LEGACY_FILENAME


def _fallback_path() -> Path:
    return runtime.app_data_dir() / _FALLBACK_FILENAME


# ---------------------------------------------------------------------------
# Keyring backend detection
# ---------------------------------------------------------------------------

def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except Exception:
        return False


def _keyring_get() -> str | None:
    import keyring
    return keyring.get_password(_SERVICE, _USER)


def _keyring_set(token: str) -> None:
    import keyring
    keyring.set_password(_SERVICE, _USER, token)


def _keyring_delete() -> None:
    import keyring
    try:
        keyring.delete_password(_SERVICE, _USER)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fernet fallback (Linux headless)
# ---------------------------------------------------------------------------

def _machine_id() -> bytes:
    """Return some stable machine-specific bytes for PBKDF2 salt."""
    candidates = [Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")]
    for c in candidates:
        try:
            raw = c.read_text(encoding="utf-8").strip()
            if raw:
                return raw.encode("utf-8")
        except Exception:
            continue
    # Windows or missing file: fall back to user home path as weak salt
    return str(Path.home()).encode("utf-8")


def _fernet_key() -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt = _machine_id()[:32].ljust(32, b"\0")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(b"transcritorio-hf-token"))


def _fernet_store(token: str) -> None:
    from cryptography.fernet import Fernet
    f = Fernet(_fernet_key())
    ciphertext = f.encrypt(token.encode("utf-8"))
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ciphertext)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _fernet_retrieve() -> str | None:
    path = _fallback_path()
    if not path.exists():
        return None
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_fernet_key())
        return f.decrypt(path.read_bytes()).decode("utf-8")
    except Exception:
        return None


def _fernet_clear() -> None:
    path = _fallback_path()
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store(token: str) -> None:
    """Persist the token using the best available backend."""
    if _keyring_available():
        try:
            _keyring_set(token)
            return
        except Exception:
            pass
    if _is_windows():
        path = _legacy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_encrypt_dpapi(token), encoding="utf-8")
        return
    # Non-Windows, no keyring: Fernet fallback
    _fernet_store(token)


def retrieve() -> str | None:
    """Read the token, migrating from DPAPI legacy if needed."""
    if _keyring_available():
        token = _keyring_get()
        if token:
            return token
        if _is_windows() and _legacy_path().exists():
            # Atomic two-phase migration
            try:
                legacy = _decrypt_dpapi(_legacy_path().read_text(encoding="utf-8").strip())
            except Exception:
                return None
            try:
                _keyring_set(legacy)
                verify = _keyring_get()
                if verify != legacy:
                    raise RuntimeError("keyring verify mismatch")
                # Only now delete the legacy vault
                try:
                    _legacy_path().unlink()
                except Exception:
                    pass
                return legacy
            except Exception:
                # Keyring failed — keep DPAPI legacy intact
                return legacy
        return None
    # No keyring available
    if _is_windows():
        path = _legacy_path()
        if not path.exists():
            return None
        try:
            return _decrypt_dpapi(path.read_text(encoding="utf-8").strip())
        except Exception:
            return None
    return _fernet_retrieve()


def clear() -> None:
    """Erase the token from every backend."""
    if _keyring_available():
        _keyring_delete()
    if _is_windows():
        path = _legacy_path()
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    else:
        _fernet_clear()
