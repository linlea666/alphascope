"""Editable settings in system_setting: detection thresholds, AI budgets, the emergency stop. Values are JSON merged
over these defaults, so a new default applies until someone overrides it."""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.crypto import decrypt, encrypt

DEFAULTS: dict[str, dict] = {
    # detection rules d1 (docs/05-detection-rules.md); USD thresholds per venue
    "detect": {
        "open_usd": {"hyperliquid": 1_000_000, "okx": 300_000},
        "add_usd": {"hyperliquid": 1_000_000, "okx": 300_000},
        "close_usd": {"hyperliquid": 1_000_000, "okx": 300_000},
        "underwater_add_usd": 500_000, "liq_usd": 250_000, "notable_usd": 250_000,
        "tiers_usd": [1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000],
        "near_liq_min_usd": 1_000_000, "near_liq_bands": [0.03, 0.015, 0.005],
        "candidate_score": 30, "auto_eic_score": 50, "refresh_hours": 48,
        # older observations still build position state but are no longer news (e.g. the first full sync)
        "max_event_age_hours": 6,
    },
    "gate": {"max_backlog": 5000, "max_sync_age_minutes": 10, "max_health_age_minutes": 10},
    "budgets": {"global_daily_usd": 5.0, "agent_daily_usd": {"eic": 2.0, "reporter": 3.0}},
    "emergency_stop": {"active": False, "reason": "", "since": None},
    "ai": {"enabled": True, "auto_eic": True},
    # four_eyes: the author of the current version may not approve it (turn on once there is more than one editor)
    "review": {"four_eyes": False},
    "columns": {"disabled": []},
    # per publishing platform: length limit (X counts CJK double and links as 23) and the hosts a post URL may use.
    # OKX Square publishes no official limit we could find: 1000 characters is a conservative default to adjust.
    "platforms": {"x": {"limit": 280, "weighted": True, "hosts": ["x.com", "twitter.com"]},
                  "okx_square": {"limit": 1000, "weighted": False, "hosts": ["okx.com", "www.okx.com"]}},
    "guard": {"allow_numbers": ["24h", "7d", "30d", "90d", "24小时", "7天", "30天", "90天"],
              "duplicate_block": 0.45, "duplicate_warn": 0.30, "duplicate_hours": 72},
}


def get(s: Session, key: str) -> dict:
    row = s.execute(text("SELECT value FROM system_setting WHERE key = :k"), {"k": key}).scalar()
    return {**DEFAULTS.get(key, {}), **(row or {})}


def put(s: Session, key: str, value: dict, user_id: int | None = None) -> dict:
    merged = {**get(s, key), **value}
    s.execute(text("""
        INSERT INTO system_setting (key, value, updated_at, updated_by) VALUES (:k, CAST(:v AS jsonb), now(), :u)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by"""),
        {"k": key, "v": json.dumps(merged), "u": user_id})
    return merged


def emergency_stopped(s: Session) -> bool:
    return bool(get(s, "emergency_stop").get("active"))


def put_secret(s: Session, key: str, secret: str) -> None:
    s.execute(text("""
        INSERT INTO system_setting (key, secret, updated_at) VALUES (:k, :s, now())
        ON CONFLICT (key) DO UPDATE SET secret = EXCLUDED.secret, updated_at = now()"""),
        {"k": key, "s": encrypt(secret)})


def get_secret(s: Session, key: str) -> str | None:
    token = s.execute(text("SELECT secret FROM system_setting WHERE key = :k"), {"k": key}).scalar()
    return decrypt(token) if token else None
