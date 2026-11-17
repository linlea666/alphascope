"""Owner settings in app_setting. Values are JSON; secrets are Fernet-encrypted with a key file that only
exists on the server (mode 600) and are never returned by the API."""

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings

DEFAULTS: dict[str, dict] = {
    "smtp": {"host": "smtp.gmail.com", "port": 587, "security": "starttls", "username": "", "from_name": "AlphaScope",
             "recipients": [], "public_url": ""},
    "live": {"interval_seconds": 30, "coalesce_seconds": 60, "max_emails_per_hour": 30, "liq_risk_pct": 5.0,
             "timezone": "Asia/Shanghai", "okx_interval_seconds": 60},
    "digest": {"enabled": False, "recipients": []},
}


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


def get(session: Session, key: str) -> dict:
    row = session.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key}).scalar()
    return {**DEFAULTS.get(key, {}), **(row or {})}


def get_secret(session: Session, key: str) -> str | None:
    token = session.execute(text("SELECT secret FROM app_setting WHERE key = :k"), {"k": key}).scalar()
    return decrypt(token) if token else None


def put(session: Session, key: str, value: dict, secret: str | None = None, clear_secret: bool = False) -> None:
    """Merge `value` into the stored JSON. A non-empty `secret` replaces the stored one; empty keeps it."""
    merged = {**get(session, key), **value}
    session.execute(text("""
        INSERT INTO app_setting (key, value, secret, updated_at) VALUES (:k, CAST(:v AS jsonb), :s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
          secret = CASE WHEN :clear THEN NULL WHEN :s IS NULL THEN app_setting.secret ELSE EXCLUDED.secret END"""),
        {"k": key, "v": json.dumps(merged), "s": encrypt(secret) if secret else None, "clear": clear_secret})


def has_secret(session: Session, key: str) -> bool:
    return bool(session.execute(text("SELECT secret IS NOT NULL FROM app_setting WHERE key = :k"), {"k": key}).scalar())
