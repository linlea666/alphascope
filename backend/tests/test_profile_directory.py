import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from app import settings_store
from app.research import binance_profiles as bp
from app.research import profile_directory as directory
from tests.test_zones_api import client  # noqa: F401

PID = "1234567890123456789"
URL = "/api/v1/settings/binance-profiles"


def test_discovery_deduplicates_and_preserves_partial_results(sf, monkeypatch):
    calls = []
    def page(coin, window, side, cohort, order, n):
        calls.append((coin, n))
        if n == 2:
            raise httpx.ReadTimeout("timeout")
        rows = [{"public_id":None, "name":None, "side":"SELL"}]
        for i in range(9):
            rows.append({"public_id":str(int(PID) + i),"name": str(i),"side":"BUY"})
        return {"total": 10 if coin == "BTC" else 11, "rows":rows}
    monkeypatch.setattr(directory, "details", page)
    monkeypatch.setattr(directory, "sleep", lambda _: None)
    directory.discover(sf)
    directory.discover(sf)
    assert calls == [("BTC",1),("ETH",1),("ETH",2)]
    with sf() as s:
        assert s.scalar(text("SELECT count(*) FROM binance_profile_directory WHERE btc_seen IS NOT NULL AND eth_seen IS NOT NULL")) == 9
        status = bp.state(s)["discovery"]
        assert status["BTC"]["status"] == "complete" and status["ETH"]["status"] == "partial"


def test_verified_save_preserves_previous_credential_and_backoff(sf, client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store,"get_settings",lambda: type("Settings",(),{"secret_key_path":tmp_path/"key"})())
    original = "p20t=original; csrftoken=abcdefghijklmnop"
    replacement = "p20t=replacement; csrftoken=abcdefghijklmnop"
    def check(client, pid, headers, verify_only):
        assert verify_only and headers["cookie"]=="p20t=original"
        return True, []  # a valid shared profile can have no open positions
    monkeypatch.setattr(bp, "fetch_profile", check)
    verified = client.post(URL+"/verify",json={"credential":original}).json()
    assert verified["verified_at"] and verified["has_credential"] and verified["ids"] == []
    assert client.put(URL,json={"enabled":True,"minutes":30}).status_code == 200
    def fail(*args, **kwargs):
        raise bp.Unavailable("access_denied")
    monkeypatch.setattr(bp,"fetch_profile",fail)
    assert client.post(URL+"/verify",json={"credential":replacement}).json()["detail"] == "access_denied"
    with sf() as s:
        assert json.loads(settings_store.get_secret(s,bp.KEY))["cookie"] == "p20t=original"
        assert bp.state(s)["status"] == "ready"
    monkeypatch.setattr(bp,"fetch_profile",lambda *a,**kw: (False, []))
    assert client.post(URL+"/verify",json={}).json()["detail"] == "no_shared_profile"
    def limited(*a, **kw):
        raise bp.Unavailable("rate_limited", datetime.now(UTC)+timedelta(hours=2))
    monkeypatch.setattr(bp,"fetch_profile",limited)
    assert client.post(URL+"/verify",json={}).status_code == 429
    monkeypatch.setattr(bp,"fetch_profile",lambda *a,**kw: (_ for _ in ()).throw(AssertionError()))
    assert client.post(URL+"/verify",json={}).status_code == 429


def test_queue_has_priority_newcomer_and_cold_slots(sf, client):
    assert client.put(URL+"/priority/"+PID,json={"pinned":True}).status_code == 200
    new = "2234567890123456789"
    cold = "3234567890123456789"
    with sf.begin() as s:
        s.execute(text("""UPDATE binance_profile_directory SET attempted=now()-interval '1 hour'"""))
        s.execute(text("INSERT INTO binance_profile_directory(profile_id,btc_seen) VALUES(:p,now())"),{"p":new})
        s.execute(text("""INSERT INTO binance_profile_directory(profile_id,attempted)
            VALUES(:p,now()-interval '2 days')"""),{"p":cold})
        assert directory.select_due(s,30,0) == PID
        assert directory.select_due(s,30,1) == new
        assert directory.select_due(s,30,3) == cold
    assert client.put(URL+"/priority/"+PID,json={"pinned":False}).json() == {"pinned":False}


def test_large_list_continues_past_first_page_budget(sf, monkeypatch):
    monkeypatch.setattr(directory, "PAGE_LIMIT", 1)
    def details(coin, window, side, cohort, order, page):
        return {"total":11,"rows":[{"public_id":str(int(PID)+i),"name":"n","side":"BUY"}
                                    for i in (range(10) if page==1 else [10])]}
    monkeypatch.setattr(directory, "details", details)
    with sf.begin() as s:
        start = directory.scan_coin(s, "ETH")
        assert start["status"] == "partial" and start["next_page"] == 2
        end = directory.scan_coin(s, "ETH", start["next_page"])
        assert end["next_page"] == 1
        assert s.scalar(text("SELECT count(*) FROM binance_profile_directory WHERE eth_seen IS NOT NULL")) == 11
