"""HTTP client for AlphaScope's read-only feed. Every response is checked against the feed.v1 contract."""

import httpx

from nr.feed.contract import SCHEMA, ContractError, check, check_all, check_context


class FeedUnavailable(Exception):
    """Network error, timeout or 5xx: the source is down, data must be treated as stale."""


class FeedError(Exception):
    """4xx or a contract violation: a configuration or compatibility problem, not an outage."""


class FeedClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {token}", "User-Agent": "alphascope-newsroom"})

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self._http.get(f"/api/v1/feed{path}", params=params)
        except httpx.HTTPError as exc:
            raise FeedUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if r.status_code >= 500:
            raise FeedUnavailable(f"HTTP {r.status_code}")
        if r.status_code == 404 and path == "/meta":
            raise FeedError("feed disabled on AlphaScope (ALPHASCOPE_FEED_TOKEN empty)")
        if r.status_code >= 400:
            raise FeedError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as exc:
            raise FeedError("response is not JSON") from exc

    def meta(self) -> dict:
        m = self._get("/meta")
        try:
            check("meta", m)
            check("meta.rules", m["rules"])
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        if m["schema"] != SCHEMA:
            raise FeedError(f"unsupported feed schema {m['schema']}")
        return m

    def events(self, after_id: int, limit: int = 1000, scan: int = 5000) -> dict:
        r = self._get("/events", {"after_id": after_id, "limit": limit, "scan": scan, "include_first_seen": True})
        try:
            check("events", r)
            check_all("events", r["rows"], "events.row")
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        return r

    def context(self, trader_key: str, coin: str | None = None) -> dict:
        r = self._get(f"/traders/{trader_key}/context", {"coin": coin} if coin else None)
        try:
            check_context(r)
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        return r

    def at_risk(self, max_dist: float, min_notional: float, limit: int = 500) -> dict:
        r = self._get("/positions/at-risk", {"max_dist": max_dist, "min_notional": min_notional, "limit": limit})
        try:
            check("at_risk", r)
            check_all("at_risk", r["rows"], "at_risk.row")
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        return r

    def health(self) -> dict:
        r = self._get("/health")
        try:
            check("health", r)
            check("health.sources", r["sources"])
            check("health.radar", r["sources"]["radar_hyperliquid"])
            check("health.radar", r["sources"]["radar_okx"])
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        return r

    def research(self, name: str, path: str, params: dict | None = None, rows_field: str | None = None,
                 row_name: str | None = None) -> dict:
        """Research column endpoints, checked against the contract like every other response."""
        r = self._get(path, params)
        try:
            check(name, r)
            if rows_field and row_name:
                check_all(name, r[rows_field], row_name)
        except ContractError as exc:
            raise FeedError(str(exc)) from exc
        return r
