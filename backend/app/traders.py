"""Trader keys across venues: a Hyperliquid address or "okx:<authorId>" (the OKX lead trader's numeric id).

authorId is used rather than uniqueName because every OKX trader we know has one, while a few hundred
imported from the old collector have no uniqueName yet (it is filled in from the profile later).
"""

import re

from app.sources.leaderboard import ADDRESS_RE

OKX_KEY_RE = re.compile(r"^okx:[0-9]{6,24}$")
VENUES = ("hyperliquid", "okx")


def parse_trader_key(key: str) -> str | None:
    """Normalized key, or None when it is neither a Hyperliquid address nor an OKX key."""
    k = (key or "").strip()
    if ADDRESS_RE.match(k.lower()):
        return k.lower()
    if OKX_KEY_RE.match(k.lower()):
        return k.lower()
    return None


def venue_of(key: str) -> str:
    return "okx" if key.startswith("okx:") else "hyperliquid"


def okx_key(author_id: str) -> str:
    return f"okx:{author_id}"


def okx_profile_url(handle: str | None) -> str | None:
    return f"https://www.okx.com/zh-hans/copy-trading/account/{handle}" if handle else None
