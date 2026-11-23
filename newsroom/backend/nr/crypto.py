"""Fernet encryption for secrets kept in the database (AI API keys). The key file only exists on the server
(mode 600, created on first use); encrypted values are never returned by the API."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from nr.config import get_settings


def _key(path: Path | None = None) -> bytes:
    path = Path(path or get_settings().secret_key_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return path.read_bytes().strip()


def encrypt(plain: str, key_path: Path | None = None) -> str:
    return Fernet(_key(key_path)).encrypt(plain.encode()).decode()


def decrypt(token: str, key_path: Path | None = None) -> str | None:
    try:
        return Fernet(_key(key_path)).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None  # key rotated or file replaced: treat as not set
