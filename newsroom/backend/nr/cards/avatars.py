"""Traders' public avatars for square cards (docs/16): fetched once from the platform's own image URL, kept on disk,
embedded as data URIs. Only https PNG/JPEG under 1 MB from a public host; anything else falls back to an initial."""

import base64
import hashlib
import ipaddress
import logging
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from nr.config import get_settings

log = logging.getLogger(__name__)
MAX_BYTES = 1_000_000
RETRY_AFTER = 86_400  # a failed URL is tried again a day later


def kind_of(blob: bytes) -> str | None:
    """PNG or JPEG by their first bytes: some CDNs serve avatars as application/octet-stream."""
    return "png" if blob.startswith(b"\x89PNG\r\n\x1a\n") else "jpeg" if blob.startswith(b"\xff\xd8\xff") else None


def _public(host: str) -> bool:
    try:
        return all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(host, 443))
    except (OSError, ValueError):
        return False


def data_uri(url: str | None, fetch: bool = True) -> str | None:
    """The avatar as a data URI, from the disk cache or fetched now; None when it can't be had safely."""
    if not url or not url.startswith("https://"):
        return None
    d = Path(get_settings().asset_dir) / "avatars"
    key = hashlib.sha256(url.encode()).hexdigest()
    for ext in ("png", "jpeg"):
        if (d / f"{key}.{ext}").exists():
            return f"data:image/{ext};base64," + base64.b64encode((d / f"{key}.{ext}").read_bytes()).decode()
    failed = d / f"{key}.none"
    if not fetch or (failed.exists() and time.time() - failed.stat().st_mtime < RETRY_AFTER):
        return None
    host = urlparse(url).hostname or ""
    try:
        if not _public(host):
            raise ValueError(f"{host} is not a public host")
        r = httpx.get(url, timeout=6, follow_redirects=False, headers={"User-Agent": "AlphaScope-Newsroom/1"})
        kind = kind_of(r.content)
        if r.status_code != 200 or kind is None or len(r.content) > MAX_BYTES:
            raise ValueError(f"{r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes")
    except (httpx.HTTPError, ValueError) as exc:
        log.info("avatar %s unavailable: %s", url, exc)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.none").write_text(str(exc)[:200])  # don't ask again for this URL
        return None
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.{kind}").write_bytes(r.content)
    return f"data:image/{kind};base64," + base64.b64encode(r.content).decode()


def drawable(name: str | None) -> str:
    """A nickname as the card font can draw it: emoji and other symbols outside the basic plane are dropped."""
    s = "".join(ch for ch in (name or "") if ord(ch) <= 0xFFFF and not 0xFE00 <= ord(ch) <= 0xFE0F
                and not 0x2600 <= ord(ch) <= 0x27BF)
    return s.strip() or (name or "")[:1]
