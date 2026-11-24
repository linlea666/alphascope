"""Binance lead traders through the kolcue relay (protocol kolcue-relay/1, github.com/linlea666/pro relay/): a signed
client, the sync that turns the stars' position snapshots into source events, and the trader context the fact builder
reads, in the same shape as AlphaScope's feed context.

The relay only polls the traders it is subscribed to, so the subscription is the star list. A trader's first snapshot
records its positions as first seen (never as opens); after that, a new position id is an open, a size change an
increase or decrease, a position gone a close. Closed positions with their P&L come from the relay's history."""

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr.feed.sync import payload_hash, upsert_traders

log = logging.getLogger(__name__)
SOURCE = "kolcue_relay"
PREFIX = "binance:"
CLOSES_EVERY = timedelta(minutes=10)


class RelayError(Exception):
    pass


def sign(secret: str, ts: int, body: bytes) -> str:
    """X-Relay-Signature v1: HMAC-SHA256 over "<unix seconds>." + the exact body bytes, the secret as UTF-8."""
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


class RelayClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.secret = secret
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def post(self, path: str, obj: dict | None = None):
        body = json.dumps(obj or {}, separators=(",", ":"), ensure_ascii=False).encode()
        ts = int(time.time())
        try:
            r = self.http.post(path, content=body, headers={"Content-Type": "application/json",
                                                            "X-Relay-Signature": f"t={ts},v1={sign(self.secret, ts, body)}"})
            d = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RelayError(f"{path}: {type(exc).__name__}: {exc}") from exc
        if not d.get("ok"):
            err = d.get("error") or {}
            raise RelayError(f"{path}: {r.status_code} {err.get('code')} {err.get('message')}")
        return d["data"]

    def meta(self) -> dict:
        return self.post("/v1/meta")

    def traders(self, query: str | None = None, page: int = 1, page_size: int = 50) -> dict:
        return self.post("/v1/traders/list", {"page": page, "page_size": page_size, **({"query": query} if query else {})})

    def trader(self, trader_id: str) -> dict:
        return self.post("/v1/traders/get", {"trader_id": trader_id})

    def subscribe(self, trader_ids: list[str]) -> list[str]:
        return self.post("/v1/subscriptions/set", {"trader_ids": trader_ids})["tracking"]

    def snapshots(self, trader_ids: list[str] | None = None) -> list[dict]:
        return self.post("/v1/snapshots", {"trader_ids": trader_ids or []})

    def closed(self, trader_id: str, limit: int = 50, before_ms: int = 0) -> list[dict]:
        return self.post("/v1/positions/closed", {"trader_id": trader_id, "limit": limit, "before_ms": before_ms})


def key_of(trader_id: str) -> str:
    return f"{PREFIX}{trader_id}"


def id_of(trader_key: str) -> str:
    return trader_key.removeprefix(PREFIX)


def coin_of(symbol: str) -> str:
    """ETHUSDT -> ETH, 1000PEPEUSDT -> 1000PEPE, BTCUSDC -> BTC."""
    for q in ("USDT", "USDC", "BUSD", "USD"):
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)]
    return symbol


def ms(v) -> datetime | None:
    return datetime.fromtimestamp(int(v) / 1000, UTC) if v else None


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def ensure_source(s: Session, base_url: str) -> int:
    return s.execute(text("""
        INSERT INTO source_connection (name, base_url, contract_schema) VALUES (:n, :u, 'kolcue-relay/1')
        ON CONFLICT (name) DO UPDATE SET base_url = EXCLUDED.base_url RETURNING id"""),
        {"n": SOURCE, "u": base_url}).scalar_one()


def _event_id(trader_key: str, position_id: str, kind: str, size, taken_ms: int) -> int:
    """Stable 63-bit id per observed change, so a replayed snapshot inserts nothing new."""
    h = hashlib.sha256(f"{trader_key}|{position_id}|{kind}|{size}|{taken_ms}".encode()).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _signed(side: str, size) -> Decimal:
    return D(size) if side == "long" else -D(size)


def diff(old: dict[str, dict], snap: dict, first: bool) -> list[dict]:
    """Changes between the stored open positions of one trader and its new snapshot, as source events."""
    taken = int(snap["taken_at_ms"])
    out = []
    new = {p["position_id"]: p for p in snap["positions"]}

    def ev(p, kind, before, after, prev_seen):
        mark = D(p.get("mark_px")) or D(p.get("entry_px"))
        return {"position_id": p["position_id"], "symbol": p["symbol"], "kind": kind, "side": p["side"],
                "szi_before": before, "szi_after": after, "mark_px": mark, "entry_px": D(p.get("entry_px")),
                "leverage": D(p.get("lever")), "notional_change": abs(after - before) * (mark or 0),
                "taken_ms": taken, "window_start": prev_seen}

    for pid, p in new.items():
        o = old.get(pid)
        size = _signed(p["side"], p["size"])
        if o is None:
            kind = "first_seen" if first else "open"
            # the same coin's opposite side closed in this snapshot: a flip
            if kind == "open" and any(x["symbol"] == p["symbol"] and x["side"] != p["side"] and x["position_id"] not in new
                                      for x in old.values()):
                kind = "flip"
            out.append(ev(p, kind, Decimal(0), size, None))
        elif D(o["size"]) != D(p["size"]):
            before = _signed(o["side"], o["size"])
            out.append(ev(p, "increase" if abs(size) > abs(before) else "decrease", before, size, o["last_seen_at"]))
    for pid, o in old.items():
        if pid not in new and not any(e["kind"] == "flip" and e["symbol"] == o["symbol"] for e in out):
            p = {**o, "mark_px": o.get("mark_px"), "lever": o.get("lever")}
            out.append(ev(p, "close", _signed(o["side"], o["size"]), Decimal(0), o["last_seen_at"]))
    return out


def stars(s: Session) -> list[dict]:
    return [dict(r) for r in s.execute(text("""SELECT trader_key, name FROM star_trader
                                               WHERE active AND venue = 'binance' ORDER BY id""")).mappings()]


def sync(sf: sessionmaker, client: RelayClient, source_id: int, now: datetime) -> dict:
    """One pass: keep the subscription equal to the star list, store each star's snapshot, emit the changes, and pull
    closed positions every CLOSES_EVERY."""
    with sf() as s:
        star = stars(s)
        state = s.execute(text("SELECT value FROM system_setting WHERE key = 'relay_state'")).scalar() or {}
        held = {}
        for k, pid in s.execute(text("SELECT trader_key, position_id FROM relay_position WHERE open")).all():
            held.setdefault(id_of(k), set()).add(pid)
    ids = [id_of(x["trader_key"]) for x in star]
    out = {"stars": len(ids), "events": 0, "closes": 0}
    try:
        if sorted(state.get("subscribed", [])) != sorted(ids):
            client.subscribe(ids)
            state["subscribed"] = ids
        snaps = client.snapshots(ids) if ids else []
        # every CLOSES_EVERY, and at once for a star whose positions just went: the close post wants its real P&L
        went = {x["trader_id"] for x in snaps if held.get(x["trader_id"], set()) - {p["position_id"] for p in x["positions"]}}
        due = [i for i in ids if i in went or now - datetime.fromisoformat(
            state.get("closes_at", {}).get(i, "2000-01-01T00:00:00+00:00")) >= CLOSES_EVERY]
        closes = {i: client.closed(i, limit=50) for i in due}
        profiles = {i: client.trader(i)["trader"] for i in due}  # the platform's 30-day stats move slowly
    except RelayError as exc:
        with sf() as s:
            s.execute(text("UPDATE source_connection SET last_error = :e, last_error_at = :n WHERE id = :i"),
                      {"e": str(exc)[:500], "n": now, "i": source_id})
            s.commit()
        log.warning("relay sync failed: %s", exc)
        return {**out, "error": str(exc)}
    names = {x["trader_key"]: x["name"] for x in star}
    with sf() as s:
        for snap in snaps:
            key = key_of(snap["trader_id"])
            if key not in names:
                continue
            old = {r["position_id"]: dict(r) for r in s.execute(text(
                "SELECT * FROM relay_position WHERE trader_key = :k AND open"), {"k": key}).mappings()}
            first = not s.execute(text("SELECT 1 FROM relay_position WHERE trader_key = :k LIMIT 1"), {"k": key}).scalar()
            taken = ms(snap["taken_at_ms"])
            changes = diff(old, snap, first)
            rows = [{"source_id": _event_id(key, c["position_id"], c["kind"], c["szi_after"], c["taken_ms"]),
                     "trader_key": key, "venue": "binance", "display_name": names[key], "handle": None,
                     "coin": coin_of(c["symbol"]), "kind": c["kind"], "side": c["side"], "szi_before": c["szi_before"],
                     "szi_after": c["szi_after"], "notional_change": c["notional_change"], "mark_px": c["mark_px"],
                     "entry_px": c["entry_px"], "leverage": c["leverage"], "liq_px": None, "event_time": taken,
                     "time_precision": "window", "window_start": c["window_start"], "observed_at": taken}
                    for c in changes]
            if rows:
                traders = upsert_traders(s, rows)
                res = s.execute(text("""
                    INSERT INTO source_event (source_id, source_event_id, trader_id, venue, coin, kind, side, szi_before,
                      szi_after, notional_change, mark_px, entry_px, leverage, liq_px, event_time, time_precision,
                      window_start, observed_at, payload_hash)
                    VALUES (:src, :source_id, :tid, :venue, :coin, :kind, :side, :szi_before, :szi_after, :notional_change,
                      :mark_px, :entry_px, :leverage, :liq_px, :event_time, :time_precision, :window_start, :observed_at, :h)
                    ON CONFLICT (source_id, source_event_id) DO NOTHING"""),
                    [{**r, "src": source_id, "tid": traders[key], "h": payload_hash(r)} for r in rows])
                out["events"] += res.rowcount
            for p in snap["positions"]:
                s.execute(text("""
                    INSERT INTO relay_position (trader_key, position_id, symbol, side, size, entry_px, mark_px, upl, lever,
                                                notional, margin, opened_at, first_seen_at, last_seen_at, open)
                    VALUES (:k, :pid, :sym, :side, :size, :entry, :mark, :upl, :lever, :ntl, :margin, :opened, :t, :t, true)
                    ON CONFLICT (trader_key, position_id) DO UPDATE SET size = EXCLUDED.size, mark_px = EXCLUDED.mark_px,
                      upl = EXCLUDED.upl, lever = EXCLUDED.lever, notional = EXCLUDED.notional, margin = EXCLUDED.margin,
                      entry_px = EXCLUDED.entry_px, last_seen_at = EXCLUDED.last_seen_at, open = true"""),
                    {"k": key, "pid": p["position_id"], "sym": p["symbol"], "side": p["side"], "size": D(p["size"]),
                     "entry": D(p.get("entry_px")), "mark": D(p.get("mark_px")) or None, "upl": D(p.get("upl")),
                     "lever": D(p.get("lever")), "ntl": D(p.get("notional")), "margin": D(p.get("margin")),
                     "opened": ms(p.get("opened_ms")), "t": taken})
            gone = [pid for pid in old if pid not in {p["position_id"] for p in snap["positions"]}]
            if gone:
                s.execute(text("UPDATE relay_position SET open = false WHERE trader_key = :k AND position_id = ANY(:g)"),
                          {"k": key, "g": gone})
            s.execute(text("UPDATE star_trader SET equity = :e, snapshot_at = :t WHERE trader_key = :k"),
                      {"e": D(snap.get("equity")), "t": taken, "k": key})
        for tid, rows in closes.items():
            key = key_of(tid)
            for c in rows:
                res = s.execute(text("""
                    INSERT INTO relay_close (trader_key, position_id, symbol, side, opened_at, closed_at, open_px, close_px,
                                             size, lever, margin, pnl, full_close)
                    VALUES (:k, :pid, :sym, :side, :o, :c, :op, :cp, :sz, :lv, :mg, :pnl, :full)
                    ON CONFLICT DO NOTHING"""),
                    {"k": key, "pid": c["position_id"], "sym": c["symbol"], "side": c["side"], "o": ms(c.get("opened_ms")),
                     "c": ms(c["closed_ms"]), "op": D(c.get("open_px")), "cp": D(c.get("close_px")), "sz": D(c.get("size")),
                     "lv": D(c.get("lever")), "mg": D(c.get("margin")), "pnl": D(c.get("pnl")), "full": bool(c.get("full"))})
                out["closes"] += res.rowcount
            state.setdefault("closes_at", {})[tid] = now.isoformat()
            if (st := profiles[tid].get("upstream_stats")) is not None:
                s.execute(text("UPDATE star_trader SET stats_30d = CAST(:st AS jsonb), stats_at = :n WHERE trader_key = :k"),
                          {"st": json.dumps(st), "n": now, "k": key})
        s.execute(text("""INSERT INTO system_setting (key, value) VALUES ('relay_state', CAST(:v AS jsonb))
                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""), {"v": json.dumps(state)})
        s.execute(text("UPDATE source_connection SET last_ok_at = :n WHERE id = :i"), {"n": now, "i": source_id})
        s.commit()
    return out


def context(s: Session, trader_key: str, now: datetime) -> dict:
    """A Binance star in the shape of AlphaScope's trader context (feed.v1), from the relay's tables, plus a `binance`
    block with the platform's 30-day stats and the closed positions. Nothing here is estimated."""
    st = s.execute(text("""SELECT coalesce(s.name, t.display_name) AS name, s.equity, s.snapshot_at, s.stats_30d
                           FROM trader t LEFT JOIN star_trader s ON s.trader_key = t.trader_key
                           WHERE t.trader_key = :k"""), {"k": trader_key}).mappings().first() or {}
    seen_at = st.get("snapshot_at")
    fresh = bool(seen_at and now - seen_at <= timedelta(minutes=10))
    positions = []
    for p in s.execute(text("""SELECT * FROM relay_position WHERE trader_key = :k AND open ORDER BY notional DESC NULLS LAST"""),
                       {"k": trader_key}).mappings():
        positions.append({"coin": coin_of(p["symbol"]), "szi": format(_signed(p["side"], p["size"]), "f"),
                          "notional_usd": format(p["notional"] or 0, "f"),
                          "mark_px": format(p["mark_px"], "f") if p["mark_px"] else None,
                          "entry_px": format(p["entry_px"], "f") if p["entry_px"] is not None else None,
                          "leverage": format(p["lever"], "f") if p["lever"] is not None else None,
                          "implied_margin": format(p["margin"], "f") if p["margin"] is not None else None,
                          "liq_px": None, "upl": format(p["upl"], "f") if p["mark_px"] and p["upl"] is not None else None,
                          "observed_at": p["last_seen_at"].isoformat(),
                          "opened_at": p["opened_at"].isoformat() if p["opened_at"] else None,
                          "opened_at_source": "source" if p["opened_at"] else None})
    closes = [dict(r) for r in s.execute(text("""
        SELECT position_id, symbol, side, opened_at, closed_at, open_px, close_px, size, lever, pnl, full_close
        FROM relay_close WHERE trader_key = :k AND closed_at > :since ORDER BY closed_at DESC LIMIT 200"""),
        {"k": trader_key, "since": now - timedelta(days=30)}).mappings()]
    return {
        "as_of": now.isoformat(),
        "trader": {"display_name": st.get("name"), "handle": None, "quality_flags": [], "enrichment_flags": [], "account_value": None},
        "watch": {"is_fresh": fresh, "last_observed_at": seen_at.isoformat() if seen_at else None, "account_value": None},
        "quality": {"styles": [], "timing": [], "copy": None},
        "positions": positions, "recent_events": [], "metrics": [], "fill_coverage": None, "cycles": [],
        "confirmed_liquidations": [], "okx": None,
        "binance": {"equity": format(st["equity"], "f") if st.get("equity") is not None else None,
                    "stats_30d": st.get("stats_30d"),
                    "closes": [{**c, "coin": coin_of(c["symbol"]),
                                **{k: c[k].isoformat() if c[k] else None for k in ("opened_at", "closed_at")},
                                **{k: format(c[k], "f") if c[k] is not None else None
                                   for k in ("open_px", "close_px", "size", "lever", "pnl")}} for c in closes]},
    }
