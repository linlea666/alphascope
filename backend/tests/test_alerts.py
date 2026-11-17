import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import settings_store
from app.alerts.diff import Pos, diff, size_event
from app.alerts.mailer import Ev, Sub, compose, in_quiet_hours, passes, ready
from app.db import get_session
from app.live import notify_pending, poll_once
from app.main import app
from app.sources.hl_client import HyperliquidInfoClient, WeightBucket

A = "0x" + "a" * 40
T0 = datetime(2026, 11, 17, 12, tzinfo=UTC)


def test_size_events():
    assert size_event(D(0), D(1)) == "open"
    assert size_event(D(1), D(2)) == "increase"
    assert size_event(D(-2), D(-1)) == "decrease"
    assert size_event(D(1), D(0)) == "close"
    assert size_event(D(1), D(-1)) == "flip"
    assert size_event(D(1), D(1)) is None


def test_diff_leverage_and_liquidation_risk_fires_once():
    before = {"BTC": Pos(D(1), leverage=5, liquidation_px=D(50000))}
    after = {"BTC": Pos(D(1), leverage=10, liquidation_px=D(62000))}
    events, risk = diff(before, after, {"BTC": D(64000)}, 5.0, {})
    assert [e.kind for e in events] == ["leverage", "liq_risk"] and risk == {"BTC": True}
    events, _ = diff(after, after, {"BTC": D(64000)}, 5.0, risk)
    assert events == []  # still at risk: no repeat


def ev(kind, before, after, px=100, t=T0, coin="BTC"):
    b, a = D(before), D(after)
    return Ev(1, coin, kind, t, b, a, D(px), abs(a - b) * D(px), {})


def test_subscription_filters():
    sub = Sub(["open", "close", "liq_risk"], 1000, 20, ["BTC"], [], None, None)
    assert passes(sub, ev("open", 0, 20))
    assert not passes(sub, ev("open", 0, 5))  # $500 < $1000
    assert not passes(sub, ev("increase", 20, 40))  # kind not subscribed
    assert not passes(sub, ev("open", 0, 20, coin="ETH"))
    sub2 = Sub(["decrease"], 0, 20, [], [], None, None)
    assert not passes(sub2, ev("decrease", 100, 90))  # only 10%
    assert passes(sub2, ev("decrease", 100, 70))


def test_coalescing_and_quiet_hours():
    burst = [ev("open", 0, 1, t=T0), ev("increase", 1, 2, t=T0 + timedelta(seconds=30))]
    assert not ready(burst, T0 + timedelta(seconds=60), 60)
    assert ready(burst, T0 + timedelta(seconds=91), 60)
    sub = Sub(["open"], 0, 0, [], [], 23, 7)  # 23:00-07:00 local, across midnight
    assert in_quiet_hours(sub, datetime(2026, 11, 17, 16, tzinfo=UTC), "Asia/Shanghai")  # 00:00 local
    assert not in_quiet_hours(sub, datetime(2026, 11, 17, 4, tzinfo=UTC), "Asia/Shanghai")  # 12:00 local


def test_compose_mentions_action_and_link():
    subject, plain, html_body = compose("抄底哥", A, [ev("open", 0, -2, px=86940)], [
        {"coin": "BTC", "szi": D(-2), "entry_px": D(86940), "position_value": D(173880), "unrealized_pnl": D(0),
         "leverage": 5}], D(500000), "http://1.2.3.4:3200", "Asia/Shanghai")
    assert subject.startswith("【AlphaScope】抄底哥 开空 BTC") and "86,940" in subject
    assert f"http://1.2.3.4:3200/zh/trader/{A}#replay" in plain and "<table" in html_body


def test_secret_roundtrip(tmp_path):
    key = tmp_path / "secret.key"
    token = settings_store.encrypt("app-password", key)
    assert settings_store.decrypt(token, key) == "app-password"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    other = tmp_path / "other.key"
    assert settings_store.decrypt(token, other) is None  # wrong key -> treated as unset


def seed_wallet(sf):
    with sf() as s:
        wid = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
            present_in_latest) VALUES (:a, now(), 't', now(), true) RETURNING id"""), {"a": A}).scalar_one()
        s.commit()
    return wid


def ch_state(szi: str):
    pos = [] if szi == "0" else [{"position": {"coin": "BTC", "szi": szi, "entryPx": "86000", "positionValue": "86000",
                                                "unrealizedPnl": "0", "leverage": {"type": "cross", "value": 5},
                                                "liquidationPx": "40000"}}]
    return {"marginSummary": {"accountValue": "100000", "totalNtlPos": "86000"}, "assetPositions": pos}


def test_live_cycle_baseline_then_event_then_email(sf, tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings_store.get_settings",
                        lambda: type("S", (), {"secret_key_path": tmp_path / "k"})())
    wid = seed_wallet(sf)
    with sf() as s:
        s.execute(text("""INSERT INTO alert_subscription (owner, wallet_id, enabled, events, min_notional, min_change_pct)
                          VALUES ('default', :w, true, ARRAY['open','close','flip','increase','decrease'], 0, 0)"""),
                  {"w": wid})
        settings_store.put(s, "smtp", {"host": "smtp.example", "username": "me@example.com",
                                       "recipients": ["me@example.com"]}, secret="pw")
        s.commit()
    states = iter([ch_state("0"), ch_state("-1"), ch_state("-1")])

    def handler(request):
        body = json.loads(request.content)
        if body["type"] == "allMids":
            return httpx.Response(200, json={"BTC": "86100"})
        return httpx.Response(200, json=next(states))

    async def cycle():
        async with HyperliquidInfoClient("https://x.invalid/info", 1e6, transport=httpx.MockTransport(handler),
                                         bucket=WeightBucket(1e6)) as c:
            return await poll_once(sf, c, 5.0)

    assert asyncio.run(cycle())["events"] == 0  # baseline
    assert asyncio.run(cycle())["events"] == 1  # opened a short
    sent = []
    r = notify_pending(sf, now=datetime.now(UTC), sender=lambda *a: sent.append(a))
    assert r["held"] == 1 and not sent  # coalescing window still open
    r = notify_pending(sf, now=datetime.now(UTC) + timedelta(seconds=120), sender=lambda *a: sent.append(a))
    assert r["sent"] == 1 and sent[0][2] == ["me@example.com"] and "开空 BTC" in sent[0][3]
    assert f"未命名 {A[:6]}…{A[-4:]}" in sent[0][3]  # no alias, no nickname: still tells traders apart
    with sf() as s:
        assert s.execute(text("SELECT notify_status FROM position_event")).scalar_one() == "sent"
        assert s.execute(text("SELECT status FROM notification")).scalar_one() == "sent"


def test_notify_failure_and_skip(sf, tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings_store.get_settings",
                        lambda: type("S", (), {"secret_key_path": tmp_path / "k"})())
    wid = seed_wallet(sf)
    with sf() as s:
        s.execute(text("""INSERT INTO position_event (wallet_id, coin, kind, t, szi_before, szi_after, px,
            notional_change, details, notify_status) VALUES (:w, 'BTC', 'open', :t, 0, 1, 100, 100, '{}', 'pending')"""),
            {"w": wid, "t": T0})
        s.commit()
    r = notify_pending(sf, now=T0 + timedelta(hours=1))
    assert r["skipped"] == 1  # no subscription / SMTP -> skipped, never sent later in a flood

    with sf() as s:
        s.execute(text("""INSERT INTO alert_subscription (owner, wallet_id, enabled, events, min_notional, min_change_pct)
                          VALUES ('default', :w, true, ARRAY['open'], 0, 0)"""), {"w": wid})
        settings_store.put(s, "smtp", {"host": "smtp.example", "username": "me@example.com",
                                       "recipients": ["me@example.com"]}, secret="pw")
        s.execute(text("""INSERT INTO position_event (wallet_id, coin, kind, t, szi_before, szi_after, px,
            notional_change, details, notify_status) VALUES (:w, 'BTC', 'open', :t, 0, 1, 100, 100, '{}', 'pending')"""),
            {"w": wid, "t": T0})
        s.commit()

    def boom(*a):
        raise OSError("smtp down")

    r = notify_pending(sf, now=T0 + timedelta(hours=1), sender=boom)
    assert r["failed"] == 1
    with sf() as s:
        st, attempts, err = s.execute(text("SELECT status, attempts, error FROM notification")).one()
        assert st == "failed" and attempts == 3 and "smtp down" in err


@pytest.fixture
def client(sf, tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings_store.get_settings",
                        lambda: type("S", (), {"secret_key_path": tmp_path / "k"})())
    seed_wallet(sf)

    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_settings_never_return_password(client):
    r = client.put("/api/v1/settings/smtp", json={"host": "smtp.gmail.com", "port": 587, "username": "me@gmail.com",
                                                   "password": "abcd efgh ijkl mnop", "recipients": ["me@gmail.com"]})
    assert r.status_code == 200
    body = json.dumps(r.json())
    assert "abcd" not in body and r.json()["smtp"]["password_set"] is True
    # empty password keeps the stored one
    r = client.put("/api/v1/settings/smtp", json={"host": "smtp.gmail.com", "username": "me@gmail.com", "password": ""})
    assert r.json()["smtp"]["password_set"] is True
    assert client.put("/api/v1/settings/smtp", json={"host": "x", "username": "u", "recipients": ["bad"]}).status_code == 422
    assert client.put("/api/v1/settings/live", json={"timezone": "Mars/Base"}).status_code == 422


def test_subscription_api(client):
    r = client.get(f"/api/v1/alerts/wallet/{A}").json()
    assert r["subscribed"] is False
    r = client.put(f"/api/v1/alerts/wallet/{A}", json={"events": ["open", "close"], "min_notional": 10000,
                                                        "coins": ["BTC"], "quiet_start": 23, "quiet_end": 7}).json()
    assert r["subscribed"] and r["subscription"]["events"] == ["open", "close"]
    assert len(client.get("/api/v1/alerts").json()["subscriptions"]) == 1
    assert client.put(f"/api/v1/alerts/wallet/{A}", json={"events": ["moon"]}).status_code == 422
    assert client.put(f"/api/v1/alerts/wallet/{A}", json={"recipients": ["nope"]}).status_code == 422
    assert client.delete(f"/api/v1/alerts/wallet/{A}").status_code == 200


def test_trader_label():
    from app.traders import trader_label
    assert trader_label("抄底哥", A) == "抄底哥"
    assert trader_label("  ", A) == f"未命名 {A[:6]}…{A[-4:]}"
    assert trader_label(None, "okx:123456789") == "未命名 OKX·456789"
