"""Official leaderboard file: fetch (conditional GET) and strict parsing.

Verified format (2026-11-15): {"leaderboardRows": [{ethAddress, accountValue, displayName, prize,
windowPerformances: [["day"|"week"|"month"|"allTime", {pnl, roi, vlm}], ...]}]} — ~47.7k rows, ~39 MB.
Any structural drift raises LeaderboardSchemaError so the ingest run fails loudly instead of storing garbage.
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
WINDOW_MAP = {"day": "day", "week": "week", "month": "month", "allTime": "alltime"}


class LeaderboardSchemaError(ValueError):
    pass


class _WindowPerf(BaseModel):
    model_config = ConfigDict(extra="allow")
    pnl: str
    roi: str
    vlm: str


class _Row(BaseModel):
    model_config = ConfigDict(extra="allow")
    ethAddress: str
    accountValue: str
    displayName: str | None = None
    prize: float | int | None = None
    windowPerformances: list[tuple[str, _WindowPerf]]

    @field_validator("ethAddress")
    @classmethod
    def _addr(cls, v: str) -> str:
        v = v.strip().lower()
        if not ADDRESS_RE.match(v):
            raise ValueError(f"invalid address {v!r}")
        return v


@dataclass(frozen=True)
class LeaderboardEntry:
    address: str
    display_name: str | None
    account_value: Decimal
    # window -> (pnl, roi, vlm); windows: day, week, month, alltime
    windows: dict[str, tuple[Decimal, Decimal, Decimal]]


@dataclass
class ParseResult:
    entries: list[LeaderboardEntry]
    duplicates: int
    unknown_windows: set[str]
    extra_fields: set[str]


def _dec(v: str) -> Decimal:
    try:
        d = Decimal(v)
    except InvalidOperation as exc:
        raise LeaderboardSchemaError(f"non-numeric value {v!r}") from exc
    if not d.is_finite():
        raise LeaderboardSchemaError(f"non-finite value {v!r}")
    return d


def parse_leaderboard(body: bytes) -> ParseResult:
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise LeaderboardSchemaError("leaderboard body is not JSON") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("leaderboardRows"), list):
        raise LeaderboardSchemaError("missing leaderboardRows list")

    entries: dict[str, LeaderboardEntry] = {}
    duplicates = 0
    unknown: set[str] = set()
    extra: set[str] = set()
    for i, raw in enumerate(doc["leaderboardRows"]):
        try:
            row = _Row.model_validate(raw)
        except ValidationError as exc:
            raise LeaderboardSchemaError(f"row {i} failed validation: {exc.errors()[:2]}") from exc
        if row.model_extra:
            extra.update(row.model_extra)
        windows: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
        for name, perf in row.windowPerformances:
            key = WINDOW_MAP.get(name)
            if key is None:
                unknown.add(name)
                continue
            windows[key] = (_dec(perf.pnl), _dec(perf.roi), _dec(perf.vlm))
        missing = set(WINDOW_MAP.values()) - windows.keys()
        if missing:
            raise LeaderboardSchemaError(f"row {i} ({row.ethAddress}) missing windows {sorted(missing)}")
        if row.ethAddress in entries:
            duplicates += 1  # keep the first occurrence; upstream has had none so far
            continue
        name = row.displayName.strip() if row.displayName else None
        entries[row.ethAddress] = LeaderboardEntry(row.ethAddress, name or None, _dec(row.accountValue), windows)
    if not entries:
        raise LeaderboardSchemaError("leaderboard contains no rows")
    return ParseResult(list(entries.values()), duplicates, unknown, extra)


@dataclass
class FetchResult:
    not_modified: bool
    body: bytes | None
    etag: str | None
    last_modified: str | None


def fetch_leaderboard(url: str, etag: str | None = None, timeout: float = 120.0) -> FetchResult:
    headers = {"Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
    if resp.status_code == 304:
        return FetchResult(True, None, etag, None)
    resp.raise_for_status()
    return FetchResult(False, resp.content, resp.headers.get("etag"), resp.headers.get("last-modified"))
