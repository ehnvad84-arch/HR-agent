"""Шифрование токенов внешних сервисов ключом SECRETS_KEY (Fernet)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import get_config
from .jobs import JobNotConfigured


def _fernet() -> Fernet:
    key = get_config().secrets_key
    if not key:
        raise JobNotConfigured("SECRETS_KEY не задан: сгенерируйте python -m hr.cli gen-key и внесите в .env")
    return Fernet(key.encode())


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(blob: bytes | memoryview) -> str:
    try:
        return _fernet().decrypt(bytes(blob)).decode()
    except InvalidToken:
        raise JobNotConfigured("токен не расшифровывается: сменился SECRETS_KEY — "
                               "пройдите авторизацию заново") from None


def new_key() -> str:
    return Fernet.generate_key().decode()
