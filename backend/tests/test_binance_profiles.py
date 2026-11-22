import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app import settings_store
from app.research import binance_profiles as bp
from tests.test_zones_api import client  # noqa: F401

PID = "1234567890123456789"
SETTINGS = "/api/v1/settings/binance-profiles"
MARKET = "/api/v1/market/binance-profiles"
SECRET = "p20t=test-secret; csrftoken=abcdefghijklmnop"


def row(symbol="BTCUSDT", **kwargs):
    return dict({"symbol": symbol, "side": "LONG", "amount": "2", "entryPrice": "63000",
                 "markPrice": "62000", "liqPrice": "59000", "leverage": "10"}, **kwargs)


def test_profile_requires_explicit_sharing():
    def reply(req):
        assert "private" not in req.url.path and "cookie" not in req.headers
        return httpx.Response(200, json={"code": "000000", "data": {"topTraderId": PID, "sharingPosition": False}})
    with httpx.Client(transport=httpx.MockTransport(reply)) as c:
        assert bp.fetch_profile(c, PID, {"cookie": "example"}) == (False, [])


def test_pages_fully_read_before_filtering_and_publication():
    def reply(req):
        if "friendly" in req.url.path:
            assert "cookie" not in req.headers
            data = {"topTraderId": PID, "sharingPosition": True}
        else:
            assert req.headers["cookie"] == "p20t=test-secret"
            data = {"total": 10, "rows": [row("OTHER" + str(i)) for i in range(8)] + [row()]
                    if req.url.params["page"] == "1" else [row("ETHUSDT", liqPrice="0")]}
        return httpx.Response(200, json={"code": "000000", "data": data})
    with httpx.Client(transport=httpx.MockTransport(reply)) as c:
        shared, rows = bp.fetch_profile(c, PID, bp.credentials(SECRET))
    assert shared and [p["coin"] for p in rows] == ["BTC", "ETH"]
    assert rows[0]["liq"] == 59000 and rows[1]["liq"] is None
    with pytest.raises(ValueError):
        bp.position(row(amount="-1"))
    with pytest.raises(ValueError):
        bp.credentials("no credentials")


@pytest.mark.parametrize("status,reason", [(401, "access_denied"), (403, "source_restricted"), (429, "rate_limited")])
def test_rate_and_access_rejections(status, reason):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={"retry-after":"7200"}))) as c:
        with pytest.raises(bp.Unavailable) as err:
            bp.read(c, "/private/future/smart-money/profile/query-positions", {}, {"cookie":"test"})
    assert err.value.reason == reason
    if status == 429:
        assert err.value.resume > datetime.now(UTC) + timedelta(minutes=119)


def test_settings_collection_and_revocation(sf, client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "get_settings", lambda: type("Settings", (), {"secret_key_path": tmp_path/"key"})())
    config = {"enabled": True, "ids": [PID], "minutes": 30, "credential": SECRET}
    assert client.put(SETTINGS, json={**config, "minutes": 1}).status_code == 422
    monkeypatch.setattr(bp, "fetch_profile", lambda *a, **kw: (True, []))
    r = client.put(SETTINGS, json=config)
    assert r.status_code == 200 and r.json()["has_credential"] and "test-secret" not in r.text
    calls = []
    def fetch(c, pid, headers):
        calls.append(pid)
        return True, [bp.position(row()), bp.position(row("ETHUSDT", liqPrice=None))]
    monkeypatch.setattr(bp, "fetch_profile", fetch)
    bp.poll(sf)
    bp.poll(sf)
    assert calls == [PID]
    eth = client.get(MARKET, params={"coin":"ETH"}).json()
    assert eth["fresh"] == 1 and len(eth["positions"]) == 1 and eth["positions"][0]["coin"] == "ETH"
    assert eth["structure"]["liquidations"]["long"]["without_liq"]["positions"] == 1
    assert client.get(MARKET, params={"coin":"SOL"}).status_code == 422
    with sf.begin() as s:
        store = settings_store.get_secret(s, bp.KEY)
        assert "test-secret" in store
        assert "test-secret" not in s.scalar(text("SELECT secret FROM app_setting WHERE key=:k"), {"k":bp.KEY})
        s.execute(text("UPDATE binance_profile_directory SET attempted=NULL"))
        settings_store.put(s, bp.KEY, {"last_attempt":None})
    monkeypatch.setattr(bp, "fetch_profile", lambda *a: (False, []))
    bp.poll(sf)
    assert client.get(MARKET, params={"coin":"BTC"}).json()["positions"] == []
    with sf.begin() as s:
        s.execute(text("UPDATE binance_profile_directory SET attempted=NULL"))
        settings_store.put(s, bp.KEY, {"last_attempt":None})
    def denied(*args):
        calls.append(PID)
        raise bp.Unavailable("access_denied")
    monkeypatch.setattr(bp, "fetch_profile", denied)
    bp.poll(sf)
    bp.poll(sf)
    assert len(calls) == 2 and client.get(SETTINGS).json()["status"] == "access_denied"
    res = client.put(SETTINGS, json={**config, "enabled":False, "credential":None, "clear":True})
    assert res.status_code == 200 and not res.json()["has_credential"]


def test_incomplete_page_is_not_an_empty_portfolio():
    def reply(req):
        data = {"topTraderId": PID, "sharingPosition": True} if "friendly" in req.url.path else {"total": 12, "rows": [row()]}
        return httpx.Response(200, json={"code":"000000","data":data})
    with httpx.Client(transport=httpx.MockTransport(reply)) as c, pytest.raises(ValueError, match="incomplete"):
        bp.fetch_profile(c, PID, {})


def test_stale_observations_do_not_contribute(sf, client):
    with sf.begin() as s:
        settings_store.put(s, bp.KEY, {"ids":[PID], "enabled":True, "minutes":15})
        s.execute(text("""INSERT INTO binance_profile_snapshot(profile_id,observed_at,shared,positions)
            VALUES(:p, now() - interval '31 minutes',true,CAST(:rows AS jsonb))"""),
            {"p": PID, "rows": json.dumps([bp.position(row())])})
    view = client.get(MARKET,params={"coin":"BTC"}).json()
    assert view["fresh"] == 0 and view["structure"] is None


def test_owner_is_told_once_when_collection_stops(sf, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "get_settings", lambda: type("Settings", (), {"secret_key_path": tmp_path/"key"})())
    sent = []
    monkeypatch.setattr(bp, "send", lambda cfg, pw, to, subject, body: sent.append((to, subject, body)))
    with sf.begin() as s:
        settings_store.put(s, "smtp", {"username": "me@example.com", "recipients": ["me@example.com"],
                                       "public_url": "https://scope.example"}, secret="pw")
        settings_store.put(s, bp.KEY, {"enabled": True, "status": "ready"}, secret=json.dumps({"cookie": "p20t=x"}))
        s.execute(text("INSERT INTO binance_profile_directory(profile_id) VALUES(:p)"), {"p": PID})
    def denied(*args):
        raise bp.Unavailable("access_denied")
    monkeypatch.setattr(bp, "fetch_profile", denied)
    bp.poll(sf)
    with sf.begin() as s:
        assert settings_store.get(s, bp.KEY)["alert"] == "sent"
        s.execute(text("UPDATE binance_profile_directory SET attempted=NULL"))
        settings_store.put(s, bp.KEY, {"last_attempt": None, "enabled": True})
    bp.poll(sf)  # already stopped: no second email, no further attempts
    assert len(sent) == 1 and "https://scope.example/zh/settings" in sent[0][2]


def test_result_for_a_replaced_credential_is_discarded(sf, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "get_settings", lambda: type("Settings", (), {"secret_key_path": tmp_path/"key"})())
    with sf.begin() as s:
        settings_store.put(s, bp.KEY, {"enabled": True, "status": "ready"}, secret=json.dumps({"cookie": "p20t=old"}))
        s.execute(text("INSERT INTO binance_profile_directory(profile_id) VALUES(:p)"), {"p": PID})

    def fetch(client, pid, headers):
        # the owner saves a new credential while the old one is still being used upstream
        with sf.begin() as s:
            settings_store.put(s, bp.KEY, {"status": "ready"}, secret=json.dumps({"cookie": "p20t=new"}))
        raise bp.Unavailable("access_denied")

    monkeypatch.setattr(bp, "fetch_profile", fetch)
    bp.poll(sf)
    with sf() as s:
        assert settings_store.get(s, bp.KEY)["status"] == "ready"
        assert s.scalar(text("SELECT attempted IS NOT NULL FROM binance_profile_directory"))
