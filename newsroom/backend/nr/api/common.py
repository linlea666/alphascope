from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


def rows(s: Session, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(r) for r in s.execute(text(sql), params or {}).mappings().all()]


def one(s: Session, sql: str, params: dict | None = None) -> dict | None:
    r = s.execute(text(sql), params or {}).mappings().first()
    return dict(r) if r else None


def now() -> datetime:
    return datetime.now(UTC)
