from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import ResearchFrame
from app.research.context import depth
from app.research.forward import probabilities, register
from app.research.frames import capture, notable, percentile
from app.research.replay import add_event, refresh_events, summarize, wallet_flows
from tests.test_zones_api import client  # noqa: F401

T = datetime(2026, 1, 10, tzinfo=UTC)


def wallet(s):
    return s.scalar(
        text("""INSERT INTO wallet(address,first_seen_at,first_seen_source,last_seen_at,present_in_latest)
        VALUES('test',:t,'test',:t,true) RETURNING id"""),
        {"t": T},
    )


def test_flip_reversal_cutoff_missing_and_coin_isolation(sf, client):
    with sf.begin() as s:
        wid = wallet(s)
        rid = s.scalar(
            text("""INSERT INTO raw_object(source,request,fetched_at,storage_path,sha256,size_bytes)
            VALUES('userFillsByTime','{}',:t,'test','test',1) RETURNING id"""),
            {"t": T + timedelta(days=1)},
        )
        for i, (coin, side, size, before) in enumerate(
            [("BTC", "B", 4, -2), ("BTC", "A", 1, 2), ("BTC", "B", 1, None), ("ETH", "B", 9, 0)]
        ):
            s.execute(
                text("""INSERT INTO fill(wallet_id,tid,side,time,coin,px,sz,dir,start_position,raw_object_id)
                VALUES(:w,:i,:side,:t,:c,100,:sz,'test',:b,:r)"""),
                dict(w=wid, i=i, side=side, t=T - timedelta(minutes=1), c=coin, sz=size, b=before, r=rid),
            )
        assert not wallet_flows(s, "BTC", T, 1, T)
        vintage = T + timedelta(days=2)
        f = summarize(wallet_flows(s, "BTC", T, 1, vintage))
        assert (f["fills"], f["missing"], f["long_add"], f["long_cut"], f["short_cut"], f["net"]) == (
            3,
            1,
            2,
            1,
            2,
            3,
        )
        assert f["without_largest"] == 0 and f["top1_share"] == 1
        assert summarize(wallet_flows(s, "ETH", T, 1, vintage))["net"] == 9
        assert summarize(wallet_flows(s, "BTC", T - timedelta(hours=1), 1, vintage))["net"] is None

    with sf.begin() as s:
        add_event(s, "btc-evidence", "BTC", T, T + timedelta(hours=2), 10, 11, "c", "manual", "evidence")
    eid = client.get("/api/v1/research/events?coin=BTC").json()["events"][0]["id"]
    url = f"/api/v1/research/events/{eid}/sources"
    for coin, count in [("BTC", 3), ("ETH", 1)]:
        params = {"venue": "hyperliquid", "coin": coin, "hours": 1}
        result = client.get(url, params=params).json()
        assert len(result["rows"]) == count and result["rows"][0]["sha256"] == "test"
        assert not client.get(url, params={**params, "mode": "as_observed"}).json()["rows"]
        assert not client.get(url, params={**params, "wallet": "unknown"}).json()["rows"]


def test_catalog_uses_independent_price_history_and_is_idempotent(sf, client):
    with sf.begin() as s:
        for i in range(72):
            px = 110 if i == 63 else 85 if i == 68 else 100
            s.execute(
                text("""INSERT INTO market_candle(source,coin,interval,t,o,h,l,c,v,fetched_at)
                VALUES('binance_um','ETH','1d',:t,100,greatest(100,:px),least(100,:px),:px,1,:t)"""),
                {"t": T + timedelta(days=i), "px": px},
            )
        assert refresh_events(s, "ETH", T + timedelta(days=75)) == 2
        assert refresh_events(s, "ETH", T + timedelta(days=75)) == 0
        assert refresh_events(s, "BTC", T + timedelta(days=75)) == 0
    events = client.get("/api/v1/research/events", params={"coin": "ETH"}).json()["events"]
    assert {r["direction"] for r in events} == {"up", "down"}
    eid = events[0]["id"]
    panel = client.get(f"/api/v1/research/events/{eid}?hours=4&mode=as_observed").json()
    assert panel["coin"] == "ETH" and panel["flow"]["net"] is None
    assert client.get(f"/api/v1/research/events/{eid}?hours=8").status_code == 422
    assert (
        client.get("/api/v1/research/compare", params=[("ids", r["id"]) for r in events]).json()["overlap_clusters"]
        == 2
    )
    assert client.get("/api/v1/research/summary?coin=DOGE").status_code == 422


def test_frozen_frames_do_not_expose_future_or_other_coin(sf, client):
    now = datetime.now(UTC)
    with sf.begin() as s:
        first = capture(s, "BTC", "hyperliquid", now, {"fields": {"mark": 100}, "missing": []})
        assert first and capture(s, "BTC", "hyperliquid", now, {"fields": {"mark": 999}}) is None
        other = capture(s, "ETH", "okx", now, {"fields": {}, "missing": ["mark"]})
        assert other and first != other
        register(s, "BTC")
        register(s, "ETH")
    assert client.get(f"/api/v1/research/frames/{first}?coin=ETH").status_code == 404
    result = client.get(
        "/api/v1/research/summary", params={"coin": "BTC", "as_of": (now - timedelta(hours=1)).isoformat()}
    ).json()
    assert result["frames"] == []
    assert client.get("/api/v1/research/summary?coin=ETH").json()["frames"][0]["metrics"]["ref"] is None
    assert len(client.get("/api/v1/research/archive?coin=ETH").json()["studies"]) == 3


def test_pre_registered_probabilities_and_depth():
    prices = {T + timedelta(hours=i): 100 + (i % 13) for i in range(120)}
    r = probabilities(prices, max(prices), 4)
    assert r["train_n"] == 112
    assert sum(r["p"]) == pytest.approx(1) and sum(r["baseline"]) == pytest.approx(1)
    assert probabilities(prices, max(prices) + timedelta(hours=3), 4) is None
    assert percentile(5, [5] * 24)["rank"] == 0.5
    assert percentile(5, [1] * 23)["rank"] is None
    assert depth([(99.9, 1), (90, 30)], [(100.1, 2)], "BTC")["bid"] == 1


def test_published_report_survives_later_source_corrections(sf, client):
    with sf.begin() as s:
        add_event(s, "eth-up", "ETH", T, T + timedelta(days=1), 100, 120, "eth", "manual", "ETH")
        s.execute(
            text("""INSERT INTO market_candle(source,coin,interval,t,o,h,l,c,v,fetched_at)
            VALUES('binance_um','ETH','1h',:t,100,110,100,105,100,:t)"""),
            {"t": T},
        )
    catalog = client.get("/api/v1/research/events?coin=ETH").json()
    body = dict(event_id=catalog["events"][0]["id"], coin="ETH", hours=4, vintage=catalog["as_of"])
    res = client.post("/api/v1/research/reports", json=body)
    assert res.status_code == 201
    rid = res.json()["id"]
    with sf.begin() as s:
        s.execute(text("UPDATE market_candle SET c=109"))
    url = f"/api/v1/research/reports/{rid}"
    assert float(client.get(url + "?coin=ETH").json()["detail"]["prices"][0][1]) == 105
    assert client.post("/api/v1/research/reports", json=body).json()["id"] == rid
    assert client.get(url + "?coin=BTC").status_code == 404


def test_forecast_settlement_calibration_and_no_future_training(sf, client):
    from app.models import ResearchForecast
    from app.research.forward import settle

    now = datetime.now(UTC)
    issue = now - timedelta(hours=3)
    due = issue + timedelta(hours=1)
    with sf.begin() as s:
        for at, mark in [(issue, 100), (due, 101)]:
            fid = capture(s, "ETH", "hyperliquid", at, {"fields": {"mark": mark}, "missing": []})
            frame = s.get(ResearchFrame, fid)
            frame.available_at = at
            if at == issue:
                s.add(
                    ResearchForecast(
                        coin="ETH",
                        model="test",
                        horizon=1,
                        issued_at=issue,
                        cutoff=issue,
                        due_at=due,
                        frame_id=fid,
                        evidence={"p": [0.2, 0.2, 0.6], "baseline": [0.25, 0.5, 0.25], "band": 0.005},
                    )
                )
            s.flush()
        settle(s, "ETH", due + timedelta(minutes=4))
        assert s.scalar(select(ResearchForecast)).outcome is None
        settle(s, "ETH", now)
    arc = client.get("/api/v1/research/archive?coin=ETH").json()
    assert arc["forecasts"][0]["outcome"]["class"] == "up"
    assert arc["scores"][0]["brier"] == pytest.approx(0.24)
    assert next(x for x in arc["calibration"] if x["cls"] == "up")["observed"] == 1
    prices = {T + timedelta(hours=i): 100 + i % 5 for i in range(120)}
    cutoff = max(prices)
    assert probabilities(prices, cutoff, 1) == probabilities(
        {**prices, cutoff + timedelta(hours=1): 9999999}, cutoff, 1
    )


def test_eligible_review_is_scoped_to_latest_evaluation(sf, client):
    from app.models import ResearchEvaluation, ResearchExperiment

    with sf.begin() as s:
        register(s, "ETH")
        study = s.scalar(select(ResearchExperiment).limit(1))
        evaluation = ResearchEvaluation(experiment_id=study.id, cutoff=T, result={"review_eligible": False})
        s.add(evaluation)
    url = f"/api/v1/research/experiments/{study.id}/reviews"
    body = {"evaluation_id": evaluation.id, "rationale": "held-out sample too small", "decision": "approve"}
    assert client.post(url, json=body).status_code == 422
    rejected = client.post(url, json={**body, "decision": "reject"}).json()
    assert client.post(url, json={**body, "decision": "reject"}).json() == rejected
    with sf.begin() as s:
        later = ResearchEvaluation(
            experiment_id=study.id, cutoff=T + timedelta(days=30), result={"review_eligible": True}
        )
        s.add(later)
    assert client.post(url, json=body).status_code == 409
    assert (
        client.post(
            url, json={**body, "evaluation_id": later.id, "rationale": "verified held-out evidence"}
        ).status_code
        == 201
    )
    assert client.get("/api/v1/research/archive?coin=BTC").json()["studies"] == []


def test_single_venue_outage_does_not_delay_healthy_markets():
    import asyncio
    from types import SimpleNamespace
    from app.research.context import collect_context

    gate = None

    class HL:
        async def info(self, request):
            if request["type"] == "metaAndAssetCtxs":
                return SimpleNamespace(
                    data=[{"universe": [{"name": "BTC"}, {"name": "ETH"}]}, [{"markPx": "12"}, {"markPx": "34"}]]
                )
            if request["coin"] == "ETH":
                await gate.wait()
                raise TimeoutError
            return SimpleNamespace(data={"levels": [[{"px": "99", "sz": "1"}], [{"px": "101", "sz": "2"}]]})

    class OKX:
        async def get(self, path, params):
            return []

    async def check():
        nonlocal gate
        gate = asyncio.Event()
        ready = []
        async for coin, venue, ctx, _ in collect_context(HL(), OKX(), ("BTC", "ETH")):
            ready.append((coin, venue))
            if (coin, venue) == ("BTC", "hyperliquid"):
                assert ("ETH", "hyperliquid") not in ready and ctx["fields"]["mark"] == 12
                gate.set()
            if (coin, venue) == ("ETH", "hyperliquid"):
                assert "depth" in ctx["missing"] and ctx["fields"]["mark"] == 34
        assert len(ready) == 4

    asyncio.run(asyncio.wait_for(check(), timeout=3))


def test_candle_publication_requires_closed_bar_and_preserves_first_confirmation(sf):
    from app.ingest.market_ingest import store_candles
    from app.research.forward import history

    row = dict(source="hyperliquid", coin="BTC", interval="1h", t=T, o=10, h=11, l=9, c=10, v=1, n=1)
    with sf.begin() as s:
        store_candles(s, [{**row, "f": T + timedelta(minutes=30)}])
        assert not history(s, "BTC", T + timedelta(hours=2))
        store_candles(s, [{**row, "f": T + timedelta(minutes=60)}])
        store_candles(s, [{**row, "f": T + timedelta(minutes=95)}])
        assert history(s, "BTC", T + timedelta(minutes=61)) == {T + timedelta(hours=1): 10.0}


def test_long_breadth_excludes_short_cover_and_holdouts_are_forward():
    import math
    from types import SimpleNamespace
    from app.research.frames import changes
    from app.research.forward import paired_test, prior_risk, treatment

    before = [{"wallet_id": i, "address": str(i), "szi": -4 if i < 3 else 1} for i in range(4)]
    after = [{**r, "szi": r["szi"] + 2} for r in before]
    delta = changes(after, before)
    assert delta["buy_breadth"] == 1 and delta["long_add_breadth"] == 0.25
    assert treatment("broad_long", {"delta": delta}) is False
    samples = []
    for i in range(120):
        at = T + timedelta(days=i * 2)
        samples.append(
            SimpleNamespace(
                issued_at=at,
                evidence={
                    "vol_24h": 0.01,
                    "momentum_4h": 0.01,
                    "divergence": 1 if i % 2 else -1,
                    "btc_beta_pre": 2,
                    "delta": {},
                },
                outcome={"ret": 0.04 if i % 2 else 0.02, "observed_at": (at + timedelta(days=1)).isoformat()},
            )
        )
    btc = {r.issued_at: 0.01 for r in samples}
    result = paired_test(samples, "eth_specialist", btc)
    assert (result["look"], result["pairs"], result["tested_pairs"]) == (1, 60, 30)
    assert result["effect"] == pytest.approx(0.02)
    assert result["folds"][0]["train_pairs"] == 30
    asset = {T + timedelta(hours=i): math.exp(0.002 * (i % 7)) for i in range(96)}
    market = {k: math.sqrt(v) for k, v in asset.items()}
    risk = prior_risk(asset, market, max(asset))
    assert risk["btc_beta_pre"] == pytest.approx(2) and risk["vol_24h"] > 0


def test_daily_review_opposite_coins_and_unavailable_venue(sf):
    from app.research.frames import daily_review

    now = datetime.now(UTC)
    with sf.begin() as s:
        for coin, size in [("ETH", 20), ("BTC", -8)]:
            fid = capture(s, coin, "okx", now, {"fields": {"mark": 10}, "missing": []})
            frame = s.get(ResearchFrame, fid)
            frame.metrics = {**frame.metrics, "windows": {"24": {"common_n": 10, "net_units": size}}}
    with sf() as s:
        review = daily_review(s, ["BTC", "ETH"], datetime.now(UTC))
    assert review["comparison"] == {"okx": "opposite", "hyperliquid": "insufficient"}


def test_notices_control_false_discoveries_across_a_frames_metrics():
    history = list(range(30))
    assert percentile(100, history)["p"] == pytest.approx(2 / 31)
    assert percentile(15, history)["p"] == 1.0
    # a month of history: only a value beyond everything seen survives the correction for eight metrics
    month = list(range(720))
    ranks = {k: percentile(360, month) for k in "abcdefg"} | {"h": percentile(5000, month)}
    assert notable(ranks) == {"h": pytest.approx(8 * 2 / 721)}
    # with one day of history even the most extreme value is not enough
    assert notable({"h": percentile(5000, list(range(24))), "a": percentile(3, list(range(24)))}) == {}
    assert notable({"a": {"n": 3, "rank": None, "p": None}}) == {}


def test_specialists_record_the_scale_corrected_q(sf):
    from app.research.frames import freeze_accounts
    from app.research.timing import TIMING_VERSION
    with sf.begin() as s:
        wid = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
            present_in_latest) VALUES ('0xspecialist', now(), 't', now(), true) RETURNING id""")).scalar_one()
        s.execute(text("""INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due,
            last_observed_at, stale_after, failures, n_positions, account_value)
            VALUES (:w, 'hyperliquid', 'hot', 'hot', now(), now(), now(), now() + interval '1 hour', 0, 1, 5000)"""),
            {"w": wid})
        s.execute(text("""INSERT INTO trader_position (wallet_id, coin, venue, szi, notional_usd, upl, opened_at_source,
            first_seen_at, observed_at) VALUES (:w, 'ETH', 'hyperliquid', 1, 3000, 0, 'source', now(), now())"""),
            {"w": wid})
        for scale, q in (("swing", 0.04), ("day", 0.3), ("trend", 0.5)):
            s.execute(text("""INSERT INTO timing_score (wallet_id, scale, time_window, coin_group, version, as_of,
                status, n_decisions, n_scored, pivot_hits, pivots, pivots_hit, cycles, q_value, edge_shrunk)
                VALUES (:w, :sc, 'all', 'c:ETH', :v, now() - interval '1 hour', 'scored', 10, 10, 0, 0, 0, 1, :q, 0.1)"""),
                {"w": wid, "sc": scale, "q": q, "v": TIMING_VERSION})
        [a] = freeze_accounts(s, "ETH", "hyperliquid", datetime.now(UTC))
    assert a["q_value"] == pytest.approx(0.04) and a["scales"] == 3
    assert a["q_adjusted"] == pytest.approx(0.12) and a["cohort"] == "candidate"


def test_study_decision_is_fixed_within_a_look(sf, monkeypatch):
    from app.models import ResearchEvaluation
    from app.research import forward
    with sf.begin() as s:
        register(s, "BTC")
        s.execute(text("UPDATE research_experiment SET registered_at = now() - interval '30 days'"))
    look = {"n": 1}
    monkeypatch.setattr(forward, "paired_test", lambda *a: {
        "status": "evaluated", "look": look["n"], "alpha": 0.025, "p": 0.001, "folds": [{"effect": 0.01}]})
    qs = iter([0.001, 0.5, 0.5])
    monkeypatch.setattr(forward, "benjamini_hochberg", lambda ps: [next(qs)] * len(ps))
    day = datetime.now(UTC).replace(hour=1, minute=0, second=0, microsecond=0)
    results = []
    for i, n in enumerate((1, 1, 2)):
        look["n"] = n
        with sf.begin() as s:
            forward.evaluate(s, day + timedelta(days=i))
            results.append([r.result for r in s.scalars(select(ResearchEvaluation).where(
                ResearchEvaluation.cutoff == (day + timedelta(days=i)).replace(hour=0)))])
    first, same_look, next_look = (r[0] for r in results)
    assert first["review_eligible"] and first["q"] == 0.001
    assert same_look["review_eligible"] and same_look["q"] == 0.001 and same_look["decided_at"] == first["decided_at"]
    assert not next_look["review_eligible"] and next_look["q"] == 0.5
