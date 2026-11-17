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


def short_key(key: str) -> str:
    """Short form of a trader key, the same as the web app's shortAddr."""
    return f"OKX·{key[-6:]}" if key.startswith("okx:") else f"{key[:6]}…{key[-4:]}"


def trader_label(name: str | None, key: str) -> str:
    """Name used in emails: the owner's alias or the public nickname (callers pass coalesce(alias, display_name)),
    else "未命名 0xfc76…9ef9" so several unnamed traders can still be told apart."""
    name = (name or "").strip()
    return name if name else f"未命名 {short_key(key)}"


def okx_profile_url(handle: str | None) -> str | None:
    return f"https://www.okx.com/zh-hans/copy-trading/account/{handle}" if handle else None
