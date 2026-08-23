"""Toy test para token_vault.retrieve() com keyring quebrado em runtime.

Bug original: keyring importavel mas com backend quebrado (ex.: Linux sem
SecretService levanta NoKeyringError em get_password) crashava retrieve()
e o fallback Fernet nunca era alcancado — token gravado ficava irrecuperavel.
Regra do projeto: token_vault nunca pode crashar.

Sem dependencias pesadas: keyring e simulado via sys.modules; o fallback
Fernet e monkeypatchado (a logica testada e o roteamento, nao a criptografia).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Injetar keyring FAKE que importa OK mas quebra em runtime (antes do import
# do token_vault, que faz import lazy dentro das funcoes).
fake_keyring = types.ModuleType("keyring")


class _FakeNoKeyringError(Exception):
    pass


def _broken_get(service, user):
    raise _FakeNoKeyringError("No recommended backend was available")


def _broken_set(service, user, token):
    raise _FakeNoKeyringError("No recommended backend was available")


fake_keyring.get_password = _broken_get
fake_keyring.set_password = _broken_set
fake_keyring.delete_password = lambda service, user: None
sys.modules["keyring"] = fake_keyring

from transcribe_pipeline import token_vault

# Cenario 1: nao-Windows, keyring quebrado, token existe no fallback Fernet
token_vault._is_windows = lambda: False
token_vault._fernet_retrieve = lambda: "hf_FAKETOKEN123"
result = token_vault.retrieve()
assert result == "hf_FAKETOKEN123", f"esperava fallback Fernet, veio {result!r}"
print("PASS: keyring quebrado (nao-Windows) -> fallback Fernet alcancado, sem crash")

# Cenario 2: nao-Windows, keyring quebrado, fallback vazio -> None, sem crash
token_vault._fernet_retrieve = lambda: None
result = token_vault.retrieve()
assert result is None, f"esperava None, veio {result!r}"
print("PASS: keyring quebrado + fallback vazio -> None, sem crash")

# Cenario 3: Windows, keyring quebrado, sem legacy vault -> None, sem crash
token_vault._is_windows = lambda: True
token_vault._legacy_path = lambda: Path("Z:/nao/existe/hf_token.vault")
result = token_vault.retrieve()
assert result is None, f"esperava None, veio {result!r}"
print("PASS: keyring quebrado (Windows, sem legacy) -> None, sem crash")

# Cenario 4: keyring saudavel continua tendo prioridade
fake_keyring.get_password = lambda service, user: "hf_DOKEYRING"
result = token_vault.retrieve()
assert result == "hf_DOKEYRING", f"esperava token do keyring, veio {result!r}"
print("PASS: keyring saudavel -> token do keyring")

print("PASS: toy_token_vault_fallback")
