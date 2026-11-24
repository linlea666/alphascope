"""Binance lead traders through the kolcue relay: the signature, position snapshots turned into events, star moves
routed to the star reporter on Binance Square, and a close told with its realized P&L."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text

from nr.detect.run import process_pending
from nr.jobs.handlers import Deps, run_pending
from nr.sources import relay
from tests.fakes import FakeFeed
from tests.helpers import NOW, newsroom

SECRET = "test-secret"
KEY = "binance:10"


def test_signature_matches_the_relay_test_vector():
    assert relay.sign("test-secret", 1700000000, b'{"a":1}') == \
        "8cb2c3355fca388e9ac2caec004f4d5d7045d74937ab5faad61dc11682247a9f"


def pos(pid, side="short", size="10", symbol="ETHUSDT", entry="3000", mark="2990", lever="20"):
    return {"position_id": pid, "symbol": symbol, "side": side, "size": size, "entry_px": entry, "mark_px": mark,
            "upl": "100", "lever": lever, "margin_mode": "cross", "margin": "1500", "notional": "29900",
            "opened_ms": 1795400000000}


def snap(positions, taken_ms):
    return {"trader_id": "10", "taken_at_ms": taken_ms, "interval_ms": 60000, "complete": True, "equity": "567461.15",
            "positions": positions}


def test_diff_first_seen_then_open_add_close_flip():
    first = relay.diff({}, snap([pos("a")], 1), first=True)
    assert [c["kind"] for c in first] == ["first_seen"]
    held = {"a": {**pos("a"), "size": Decimal("10"), "last_seen_at": None}}
    assert [c["kind"] for c in relay.diff(held, snap([pos("a", size="15")], 2), first=False)] == ["increase"]
    assert [c["kind"] for c in relay.diff(held, snap([pos("a", size="4")], 2), first=False)] == ["decrease"]
    closed = relay.diff(held, snap([], 2), first=False)
    assert [(c["kind"], c["szi_before"], c["szi_after"]) for c in closed] == [("close", Decimal("-10"), Decimal(0))]
    flip = relay.diff(held, snap([pos("b", side="long")], 2), first=False)
    assert [c["kind"] for c in flip] == ["flip"]  # the short went and a long on the same coin came: one flip


class FakeRelay:
    def __init__(self):
        self.snapshots = [snap([pos("a")], int((NOW - timedelta(minutes=2)).timestamp() * 1000))]
        self.closes: list[dict] = []
        self.subscribed: list[str] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        sig = dict(x.split("=", 1) for x in request.headers["X-Relay-Signature"].split(","))
        want = hmac.new(SECRET.encode(), f"{sig['t']}.".encode() + request.content, hashlib.sha256).hexdigest()
        if sig["v1"] != want:
            return httpx.Response(401, json={"ok": False, "error": {"code": "bad_signature", "message": "x"}})
        body = json.loads(request.content or b"{}")
        path = request.url.path
        self.calls.append(path)
        data = {"/v1/subscriptions/set": lambda: {"tracking": self.subscribed.extend(body["trader_ids"]) or body["trader_ids"]},
                "/v1/snapshots": lambda: self.snapshots,
                "/v1/positions/closed": lambda: self.closes,
                "/v1/traders/get": lambda: {"trader": {"trader_id": "10", "name": "熬鹰", "upstream_stats": {
                    "period": "30d", "roi": 132.836, "pnl": 1050003.17, "win_rate": 71.875, "max_drawdown": 48.83,
                    "trades": 32, "equity": 567461.15}}, "snapshot": None}}[path]()
        return httpx.Response(200, json={"ok": True, "data": data})

    def client(self):
        return relay.RelayClient("http://relay.test", SECRET, transport=httpx.MockTransport(self.handler))


def run(sf, fake, now):
    with sf() as s:
        sid = relay.ensure_source(s, "http://relay.test")
        s.commit()
    with fake.client() as c:
        r = relay.sync(sf, c, sid, now)
    process_pending(sf, now)
    run_pending(sf, Deps(feed=FakeFeed().client, now=lambda: now), ("build_facts",))
    return r


def test_a_star_close_reaches_binance_square_with_its_result(sf):
    newsroom(sf)
    with sf() as s:
        s.execute(text("INSERT INTO star_trader (trader_key, venue, name) VALUES (:k, 'binance', '熬鹰')"), {"k": KEY})
        s.commit()
    fake = FakeRelay()
    first = run(sf, fake, NOW)
    assert first["stars"] == 1 and fake.subscribed == ["10"]
    with sf() as s:  # the first snapshot is a starting point, not news
        assert s.execute(text("SELECT kind FROM source_event")).scalars().all() == ["first_seen"]
        assert s.execute(text("SELECT count(*) FROM news_event")).scalar() == 0
        assert s.execute(text("SELECT stats_30d->>'roi' FROM star_trader")).scalar() == "132.836"
    # the short is gone; the relay's history shows it closed with a profit
    later = NOW + timedelta(minutes=1)
    fake.snapshots = [snap([], int(later.timestamp() * 1000))]
    fake.closes = [{"position_id": "a", "symbol": "ETHUSDT", "side": "short", "opened_ms": int((NOW - timedelta(hours=5)).timestamp() * 1000),
                    "closed_ms": int((later - timedelta(seconds=30)).timestamp() * 1000), "open_px": "3000",
                    "close_px": "2700", "size": "10", "lever": "20", "margin": "1500", "pnl": "3000", "full": True}]
    out = run(sf, fake, later)
    assert out["events"] == 1 and out["closes"] == 1
    with sf() as s:
        e = s.execute(text("SELECT id, event_class, venue, state FROM news_event")).mappings().one()
        assert (e["event_class"], e["venue"]) == ("whale_close", "binance")
        facts = {f["id"]: f for f in s.execute(text(
            "SELECT p.content->'facts' FROM fact_packet p WHERE p.news_event_id = :e"), {"e": e["id"]}).scalar()}
        assert facts["close.realized_pnl_usd"]["value"] == "3000" and "close.pnl_estimate_usd" not in facts
        assert facts["trader.binance_roi_30d"]["value"].startswith("1.32836")
        subject = s.execute(text("SELECT p.content->'subject' FROM fact_packet p WHERE p.news_event_id = :e"),
                            {"e": e["id"]}).scalar()
        assert subject["trader_ref"] == "熬鹰" and subject["star"] is True
        asg = s.execute(text("""SELECT a.source, a.agent_key, a.status, acc.handle FROM assignment a
                                JOIN editorial_account acc ON acc.id = a.account_id""")).mappings().all()
        assert [dict(x) for x in asg] == [{"source": "star", "agent_key": "reporter_star", "status": "accepted",
                                           "handle": "alphascope_binance_square"}]
        assert s.execute(text("SELECT count(*) FROM job WHERE kind = 'draft_generate'")).scalar() == 1


def test_square_drafts_get_the_name_series_and_a_question(sf):
    from nr.desk import squares
    assert squares.mask("熬鹰") == "熬x" and squares.mask("三年好") == "三x好" and squares.mask("中二的天才交易员") == "中xxx员"
    packet = {"event": {"class": "whale_open", "coin": "ETH", "direction": "short"},
              "subject": {"trader_key": KEY, "name": "熬鹰", "star": True}}
    q = squares.question_for(packet)
    assert q[0] == "这笔 $ETH 空单最后是赚是亏？" and q[1] == "position_closed"
