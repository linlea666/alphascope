"""An in-memory AlphaScope feed (feed.v1) served through httpx.MockTransport, and a scripted AI provider."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from nr.feed.client import FeedClient

T0 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
WHALE = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


def iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def ev(sid: int, kind: str, before, after, mark, *, trader=WHALE, coin="BTC", venue="hyperliquid", observed=None,
       entry=None, lev=20, liq=None, side=None, precision="window", name=None) -> dict:
    observed = observed or T0 + timedelta(minutes=30 * sid)
    b, a, m = Decimal(str(before)), Decimal(str(after)), Decimal(str(mark))
    return {"source_id": sid, "trader_key": trader, "venue": venue, "display_name": name, "handle": None, "coin": coin,
            "kind": kind, "side": side or ("long" if (a if kind != "close" else b) > 0 else "short"),
            "szi_before": str(b), "szi_after": str(a), "notional_change": str(abs(a - b) * m), "mark_px": str(m),
            "entry_px": str(entry if entry is not None else m), "leverage": str(lev) if lev else None,
            "liq_px": str(liq) if liq else None, "event_time": iso(observed), "time_precision": precision,
            "window_start": iso(observed - timedelta(minutes=30)) if kind != "first_seen" else None,
            "observed_at": iso(observed)}


def position(coin="BTC", szi=30, mark=100000, entry=98000, lev=20, liq=91000, upl=None, observed=None,
             source="window"):
    szi, mark = Decimal(str(szi)), Decimal(str(mark))
    ntl = abs(szi) * mark
    return {"coin": coin, "szi": str(szi), "notional_usd": str(ntl), "mark_px": str(mark), "entry_px": str(entry),
            "leverage": str(lev) if lev else None, "margin_mode": "cross", "liq_px": str(liq) if liq else None,
            "upl": str(upl if upl is not None else (mark - Decimal(str(entry))) * szi), "opened_at": None,
            "opened_at_source": source, "opened_after": iso(T0), "first_seen_at": iso(T0),
            "observed_at": iso(observed or T0 + timedelta(hours=2)),
            "implied_margin": str(ntl / Decimal(str(lev))) if lev else None}


def context(trader=WHALE, venue="hyperliquid", positions=None, fresh=True, account_value="20000000", synced=True,
            cycles=None, styles=None, liqs=None, okx=None, flags=None) -> dict:
    return {
        "schema": "feed.v1", "as_of": iso(T0 + timedelta(hours=2)),
        "trader": {"trader_key": trader, "venue": venue, "display_name": None, "handle": None,
                   "first_seen_at": iso(T0 - timedelta(days=90)), "account_value": account_value, "tier": "B",
                   "quality_flags": flags or [], "enrichment_flags": [], "month_pnl": "100000", "alltime_pnl": "900000",
                   "leaderboard_fetched_at": iso(T0)},
        "watch": {"tier": "hot", "last_observed_at": iso(T0 + timedelta(hours=2)),
                  "stale_after": iso(T0 + timedelta(hours=3)), "is_fresh": fresh, "failures": 0,
                  "account_value": account_value, "n_positions": 1, "gross_notional": "3000000"},
        "positions": positions if positions is not None else [position()],
        "recent_events": [], "metrics": [],
        "quality": {"styles": styles or [], "timing": [], "copy": None, "health": None},
        "fill_coverage": {"source": "fills", "status": "synced" if synced else "queued",
                          "earliest_at": iso(T0 - timedelta(days=140)) if synced else None,
                          "latest_at": iso(T0), "n_records": 400, "truncated": False, "last_synced_at": iso(T0),
                          "reconstructed_at": iso(T0), "recon_breaks": 0},
        "cycles": cycles if cycles is not None else [], "confirmed_liquidations": liqs or [], "okx": okx,
    }


def cycle(opened: datetime, net: float, notional=500000, complete=True, status="closed") -> dict:
    return {"coin": "BTC", "direction": "long", "opened_at": iso(opened), "version": "p1",
            "closed_at": iso(opened + timedelta(days=2)), "status": status, "complete": complete, "scale": "swing",
            "holding_hours": "48", "entry_px": "90000", "exit_px": "95000", "max_size": "5",
            "max_notional": str(notional), "realized_pnl": str(net + 100), "fees": "100"}


def health(hl="ok", okx="ok", head=0) -> dict:
    radar = lambda st: {"watched": 10, "fresh": 10 if st == "ok" else 2, "positioned": 5, "latest": iso(T0),  # noqa: E731
                        "oldest": iso(T0), "fresh_ratio": 1.0 if st == "ok" else 0.2, "status": st}
    return {"schema": "feed.v1", "as_of": iso(T0), "rules": {}, "sources": {
        "radar_hyperliquid": radar(hl), "radar_okx": radar(okx), "market_event": {"head_id": head},
        "jobs": [], "fill_archive": {}, "okx_history": {}, "copy_score": {}}}


class FakeFeed:
    def __init__(self):
        self.events: list[dict] = []
        self.contexts: dict[str, dict] = {}
        self.health_doc = None
        self.at_risk_rows: list[dict] = []
        self.down = False
        self.calls: list[str] = []
        self.research: dict[str, dict] = {}  # path -> response for the research column endpoints

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("connection refused")
        if request.headers.get("authorization") != "Bearer test-token":
            return httpx.Response(401)
        path, q = request.url.path.removeprefix("/api/v1/feed"), request.url.params
        if path == "/meta":
            return httpx.Response(200, json={"schema": "feed.v1", "server_time": iso(T0), "settle_seconds": 120,
                                             "limits": {}, "rules": {"liq_tolerance": "0.005", "stale_factor": 2,
                                                                     "poll_minutes": {}, "health": {}}})
        if path == "/events":
            after, limit = int(q["after_id"]), int(q.get("limit", 500))
            rows = sorted((r for r in self.events if r["source_id"] > after), key=lambda r: r["source_id"])
            page = rows[:limit]
            head = max((r["source_id"] for r in self.events), default=None)
            return httpx.Response(200, json={
                "schema": "feed.v1", "as_of": iso(T0), "settled_before": iso(T0), "after_id": after,
                "next_after_id": page[-1]["source_id"] if page else after, "head_id": head,
                "has_more": len(rows) > limit, "rows": page})
        if path.startswith("/traders/"):
            key = path.split("/")[2]
            if key not in self.contexts:
                return httpx.Response(404, json={"detail": "trader not in database"})
            return httpx.Response(200, json=self.contexts[key])
        if path == "/health":
            head = max((r["source_id"] for r in self.events), default=None)
            return httpx.Response(200, json=self.health_doc or health(head=head))
        if path in self.research:
            return httpx.Response(200, json=self.research[path])
        if path == "/positions/at-risk":
            return httpx.Response(200, json={"schema": "feed.v1", "as_of": iso(T0), "max_dist": q["max_dist"],
                                             "rows": self.at_risk_rows})
        return httpx.Response(404)

    def client(self) -> FeedClient:
        return FeedClient("http://alphascope.test", "test-token", transport=httpx.MockTransport(self.handler))


class FakeProvider:
    """Returns scripted outputs (dicts are JSON-encoded) or raises scripted exceptions, in order."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests: list[dict] = []

    def __call__(self, cfg, req):
        from nr.ai.providers.base import ChatResult
        self.requests.append({"model": cfg["model"], "system": req.system, "user": req.user, "max_tokens": req.max_tokens})
        out = self.outputs.pop(0) if self.outputs else {"ok": True}
        if isinstance(out, Exception):
            raise out
        body = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return ChatResult(text=body, input_tokens=1000, output_tokens=500, stop_reason="end", http_status=200,
                          latency_ms=5, model=cfg["model"])
