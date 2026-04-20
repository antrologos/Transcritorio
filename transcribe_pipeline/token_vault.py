"""Secure HF token storage using Windows DPAPI.

Encrypts the token with the current user's Windows credentials.
Only this user on this machine can decrypt it. No external dependencies.

The vault file is stored at %LOCALAPPDATA%/Transcritorio/hf_token.vault
as a base64-encoded blob of DPAPI-encrypted data.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import sys
from pathlib import Path

from . import runtime


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _encrypt_dpapi(plaintext: str) -> str:
    """Encrypt a string using Windows DPAPI. Returns base64-encoded ciphertext."""
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
    """Decrypt a base64-encoded DPAPI blob. Returns plaintext string."""
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


def _vault_path() -> Path:
    return runtime.app_data_dir() / "hf_token.vault"


def store(token: str) -> None:
    """Encrypt and save token to vault file."""
    if sys.platform != "win32":
        return
    path = _vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_encrypt_dpapi(token), encoding="utf-8")


def retrieve() -> str | None:
    """Retrieve and decrypt token from vault. Returns None if not stored."""
    if sys.platform != "win32":
        return None
    path = _vault_path()
    if not path.exists():
        return None
    try:
        return _decrypt_dpapi(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def clear() -> None:
    """Delete token from vault."""
    path = _vault_path()
    if path.exists():
        path.unlink()
