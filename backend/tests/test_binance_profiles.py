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


@pytest.mark.parametrize("status,reason", [(401, "access_denied"), (403, "access_denied"), (429, "rate_limited")])
def test_rate_and_access_rejections(status, reason):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={"retry-after":"7200"}))) as c:
        with pytest.raises(bp.Unavailable) as err:
            bp.read(c, "/friendly/future/smart-money/profile", {})
    assert err.value.reason == reason
    if status == 429:
        assert err.value.resume > datetime.now(UTC) + timedelta(minutes=119)


def test_settings_collection_and_revocation(sf, client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "get_settings", lambda: type("Settings", (), {"secret_key_path": tmp_path/"key"})())
    config = {"enabled": True, "ids": [PID], "minutes": 30, "credential": SECRET}
    assert client.put(SETTINGS, json={**config, "minutes": 1}).status_code == 422
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
        settings_store.put(s, bp.KEY, {"attempts": {}, "last_attempt":None})
    monkeypatch.setattr(bp, "fetch_profile", lambda *a: (False, []))
    bp.poll(sf)
    assert client.get(MARKET, params={"coin":"BTC"}).json()["positions"] == []
    with sf.begin() as s:
        settings_store.put(s, bp.KEY, {"attempts": {}, "last_attempt":None})
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
