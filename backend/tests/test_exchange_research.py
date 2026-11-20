from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.research import exchange as ex
from tests.test_zones_api import client  # noqa: F401

BASE = "/api/v1/research/exchange"


def sample():
    return {
        "longTraders": 29,
        "shortTraders": 71,
        "longPositions": 130,
        "shortPositions": 870,
        "longQty": 0.2,
        "shortQty": 1.2,
        "longWhales": 1,
        "shortWhales": 5,
        "longWhalePositions": 70,
        "shortWhalePositions": 812,
        "longWhaleQty": 0.1,
        "shortWhaleQty": 1,
    }


def test_distinct_denominators_and_whale_subset():
    group = ex.stats(sample())
    assert group["traders"]["head_share"] == 0.29
    assert group["traders"]["money_share"] == 0.13
    assert group["whales"]["long"]["n"] == 1
    assert group["traders"]["net"] == -740
    assert ex.stats({key: 0 for key in sample()})["traders"]["head_share"] is None


@pytest.mark.parametrize("rate,payer", [(0.0001, "long"), (-0.0001, "short"), (0.0, "none")])
def test_payer_and_settlement(rate, payer):
    raw = {
        "instId": "ETH-USDT-SWAP",
        "fundingRate": str(rate),
        "fundingTime": "1795100000000",
        "nextFundingTime": "1795130000000",
        "ts": "1795099999000",
    }
    result = ex.okx_fee({"code": "0", "data": [raw]}, "ETH")
    assert result["due"] == ex.timestamp(raw["fundingTime"])
    assert result["payer"] == payer and result["per_10000"] == abs(rate) * 10000
    with pytest.raises(ValueError):
        ex.okx_fee({"code": "0", "data": [raw]}, "BTC")
    with pytest.raises(ValueError):
        ex.fee({"symbol": "BTCUSDT", "lastFundingRate": None}, "BTCUSDT")


@pytest.mark.parametrize("window", ["30m", "1h", "24h", "7D", "ALL"])
def test_detail_cache_page_filter_and_anonymous_count(window, client, monkeypatch):
    ex._cache.clear()
    seen = []

    def fetch(url, params, timeout):
        seen.append(params)
        row = {
            "timeRange": window,
            "side": "SELL",
            "traders": 891,
            "currentQty": 3 if window == "ALL" else None,
            "currentPositions": 7,
            "netPositions": -3,
            "lastTradeTime": 1795100000000,
        }
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"code": "000000", "success": True, "data": [row], "total": 31},
        )

    monkeypatch.setattr(ex.httpx, "get", fetch)
    args = {"coin": "ETH", "window": window, "cohort": "whales", "side": "short", "order": "ASC", "page": 2}
    result = client.get(BASE + "/details", params=args).json()
    assert result["total"] == 31 and result["rows"][0]["n"] == 891 and result["rows"][0]["public_id"] is None
    assert result["rows"][0]["qty"] == (3 if window == "ALL" else None)
    assert client.get(BASE + "/details", params=args).status_code == 200 and len(seen) == 1
    assert seen[0] == {
        "symbol": "ETHUSDT",
        "timeRange": window,
        "sortingField": "TIME",
        "sortingOrder": "ASC",
        "page": 2,
        "rows": 10,
        "positionSide": "SHORT",
        "showAnonymousWhales": "true",
    }
    client.get(BASE + "/details", params={**args, "coin": "BTC"})
    assert seen[-1]["symbol"] == "BTCUSDT"
    assert client.get(BASE + "/details", params={**args, "window": "all"}).status_code == 422
    ex._cache.clear()


def test_archive_gaps_stale_evidence_watch_and_no_future(sf, client):
    now = datetime.now(UTC)
    old = now - timedelta(minutes=12)
    original = {"at": old.isoformat(), "data": ex.stats(sample())}
    with sf.begin() as s:
        ex.publish(s, "BTC", old, {"ALL": original, "24h": original}, {})
        first = s.scalar(text("UPDATE exchange_observation SET published_at=:at RETURNING id"), {"at": old})
        ex.publish(s, "BTC", now, {}, {"ALL": "451", "24h": "451"})
        ex.publish(s, "BTC", now, {"ALL": {"at": now.isoformat(), "data": {}}}, {})  # same slot is immutable
        ex.publish(s, "ETH", now, {"ALL": {**original, "at": now.isoformat()}}, {})
    latest = client.get(BASE + "/summary", params={"coin": "BTC"}).json()
    assert latest["readings"]["ALL"]["stale"] and latest["readings"]["ALL"]["at"] == old.isoformat()
    assert not client.get(BASE + "/summary", params={"coin": "ETH"}).json()["readings"]["ALL"]["stale"]
    assert (
        client.get(
            BASE + "/summary", params={"coin": "BTC", "as_of": (old - timedelta(seconds=1)).isoformat()}
        ).json()
        is None
    )
    archive = client.get(BASE + "/evidence", params={"coin": "BTC", "observation": first}).json()
    assert archive["observation"]["id"] == first and not archive["observation"]["readings"]["ALL"]["stale"]
    assert archive["history"][0]["smart_accounts"] == 0.29 and archive["history"][0]["overall_accounts"] is None
    assert len(archive["history"]) == 1 and archive["history"][0]["share"] == 0.13
    assert client.get(BASE + "/evidence", params={"coin": "ETH", "observation": first}).status_code == 404
    assert client.put(f"{BASE}/{first}/watch", json={"watching": True}).status_code == 200
    live = client.get(BASE + "/evidence", params={"coin": "BTC"}).json()
    assert live["saved"][0]["id"] == first and live["history"][-1]["share"] is None
    assert live["history"][-1]["flow"] is None


def test_ratio_semantics_and_reported_source_clock():
    j = {"symbol": "ETHUSDT", "timestamp": 1795100000000, "longShortRatio": "3"}
    assert ex.ratio([j], "binance", "ETHUSDT")["long_share"] == 0.75
    assert ex.ratio({"code": "0", "data": [[j["timestamp"], "0.25"]]}, "okx", "ETHUSDT")["long_share"] == 0.2
    with pytest.raises(ValueError):
        ex.ratio([j], "binance", "BTCUSDT")
