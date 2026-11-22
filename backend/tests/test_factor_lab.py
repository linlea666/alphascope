import asyncio
import io
import json
from datetime import UTC, date, datetime, timedelta
from zipfile import ZipFile

import numpy as np
import pytest
from sqlalchemy import text

from app.ingest import binance_metrics as bm
from app.research import factor_lab as lab
from tests.test_zones_api import client  # noqa: F401

START = date(2025, 1, 1)


def seed(sf, n_days, signal=0.0, seed=1):
    """n_days of metrics, closes and funding. With signal > 0 the next day's return follows the top-trader
    position ratio of the day before the decision."""
    rng = np.random.default_rng(seed)
    tpr = np.exp(rng.normal(0, 0.3, n_days))
    noise = rng.normal(0, 0.02, n_days + 2)
    closes, px = {}, 100.0
    for i in range(n_days + 2):
        day = START + timedelta(days=i)
        # day i's metrics are known at the start of day i + 1; that day's return is the outcome
        if 1 <= i <= n_days:
            px *= 1 + noise[i] + signal * float(np.log(tpr[i - 1]))
        else:
            px *= 1 + noise[i]
        closes[day] = px
    with sf.begin() as s:
        for i in range(n_days):
            day = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)
            s.execute(text("""INSERT INTO binance_metric (coin, t, oi, oi_usd, top_account_ratio, top_position_ratio,
                account_ratio, taker_ratio) SELECT 'BTC', :d + g * interval '5 minutes', :oi, 0, 1, :tpr, :ar, 1
                FROM generate_series(0, 287) g"""),
                {"d": day, "oi": 1000 + i, "tpr": float(tpr[i]), "ar": float(np.exp(rng.normal(0, 0.2)))})
        s.execute(text("""INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
            VALUES ('binance_um', 'BTC', '1d', :t, :c, :c, :c, :c, 1, 1, now())"""),
            [{"t": datetime(d.year, d.month, d.day, tzinfo=UTC), "c": c} for d, c in closes.items()])
        months = {}
        for i in range(n_days * 3):
            stamp = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=8 * i)
            months.setdefault(stamp.date().replace(day=1), []).append([int(stamp.timestamp() * 1000) + 1, 1e-4, 8])
        for month, rows in months.items():
            s.execute(text("""INSERT INTO funding_archive_month (venue, coin, month, source, settlements)
                VALUES ('binance', 'BTC', :m, 'test', CAST(:r AS jsonb))"""), {"m": month, "r": json.dumps(rows)})
    return closes


def test_factors_use_only_the_previous_day_and_returns_start_at_the_decision(sf):
    closes = seed(sf, 30)
    with sf() as s:
        rows = lab.build(*lab.load(s, "BTC"))
    first = rows[0]
    assert first["day"] == START + timedelta(days=1)
    # the outcome runs from the close before the decision to the decision day's close
    assert first["fwd_1d"] == pytest.approx(closes[START + timedelta(days=1)] / closes[START] - 1)
    assert first["fwd_3d"] == pytest.approx(closes[START + timedelta(days=3)] / closes[START] - 1)
    assert rows[1]["oi_chg_1d"] == pytest.approx(np.log(1001 / 1000))
    assert rows[1]["mom_1d"] == pytest.approx(np.log(closes[START + timedelta(days=1)] / closes[START]))
    # three settlements a day at 1e-4, the decision's own midnight settlement excluded
    assert rows[5]["funding_1d"] == pytest.approx(3e-4)
    assert rows[5]["top_position_z"] is None  # no 60-day history yet


def test_circular_shift_test_keeps_its_level():
    rng = np.random.default_rng(0)
    false = 0
    for k in range(150):
        x = np.cumsum(rng.normal(size=300)) * 0.1 + rng.normal(size=300)  # persistent factor
        y = rng.normal(size=300)
        false += lab.shift_test(x, y)[1] < 0.05
    assert false < 15
    x = rng.normal(size=300)
    ic, p = lab.shift_test(x, 0.3 * x + rng.normal(size=300))
    assert ic > 0.2 and p < 0.01


def test_screen_finds_the_planted_factor_and_little_else(sf):
    seed(sf, 600, signal=0.03)
    with sf() as s:
        result = lab.run(s, ["BTC"], now=datetime(2027, 1, 1, tzinfo=UTC))
    btc = result["coins"]["BTC"]
    assert btc["status"] == "screened" and btc["days"] >= 590
    found = {(t["factor"], t["horizon"]) for t in btc["tests"] if t["status"] == "candidate"}
    assert ("top_position_chg_1d", 1) in found or ("top_position_z", 1) in found
    assert len(found) <= 4
    assert set(btc["regimes"]) == {"long_build", "short_cover", "short_build", "long_unwind", "all"}
    assert btc["today"]["values"]["top_position_z"]["percentile"] is not None


def test_short_history_is_not_screened(sf):
    seed(sf, 120)
    with sf() as s:
        result = lab.run(s, ["BTC"], now=datetime(2027, 1, 1, tzinfo=UTC))
    assert result["coins"]["BTC"]["status"] == "insufficient_history" and result["tests"] == 0


def metrics_zip(day, rows):
    buffer = io.BytesIO()
    header = "create_time,symbol," + ",".join(bm.COLUMNS)
    with ZipFile(buffer, "w") as z:
        z.writestr("m.csv", "\n".join([header, *rows]))
    return buffer.getvalue()


def test_metrics_files_are_parsed_and_recorded_once(sf, monkeypatch):
    day = datetime.now(UTC).date() - timedelta(days=1)
    line = lambda t, v: f"{day} {t},BTCUSDT,{v},1,1,2,1.5,0.9"  # noqa: E731
    body = metrics_zip(day, [line("00:05:00", "10"), line("00:00:00", ""), line("00:05:00", "10")])
    rows = bm.parse_metrics(body, "BTC", day)
    assert [r["t"].minute for r in rows] == [0, 5] and rows[0]["oi"] is None and rows[1]["top_position_ratio"] == 2
    with pytest.raises(ValueError):
        bm.parse_metrics(body, "ETH", day)
    calls = []

    async def fetch(client, path):
        calls.append(path)
        return (body, "sha") if "metrics" in path else None

    async def unpublished(*args):
        raise ValueError("unpublished")

    monkeypatch.setattr(bm, "fetch_verified", fetch)
    monkeypatch.setattr(bm.funding_history, "fetch_month", unpublished)
    first = asyncio.run(bm.sync_metrics(sf, ["BTC"], day))
    assert first["files"] == 1 and first["rows"] == 2
    asyncio.run(bm.sync_metrics(sf, ["BTC"], day))
    assert sum("metrics" in c for c in calls) == 1
    with sf() as s:
        assert s.scalar(text("SELECT count(*) FROM binance_metric")) == 2


def test_latest_run_endpoint(sf, client):
    seed(sf, 120)
    lab.refresh(sf, ["BTC"])
    body = client.get("/api/v1/research/factors", params={"coin": "BTC"}).json()
    assert body["coin"] == "BTC" and body["status"] == "insufficient_history" and body["factors"]
    assert client.get("/api/v1/research/factors", params={"coin": "DOGE"}).status_code == 422


def test_conclusions_label_every_claim_with_its_evidence_tier(sf, client):
    empty = client.get("/api/v1/research/conclusions").json()["items"]
    assert {i["kind"] for i in empty} == {"forecast", "copy_score"}
    assert all(i["tier"] == "watching" for i in empty)
    seed(sf, 600, signal=0.03)
    lab.refresh(sf, ["BTC"])
    items = client.get("/api/v1/research/conclusions").json()["items"]
    regime = next(i for i in items if i["kind"] == "regime")
    factors = next(i for i in items if i["kind"] == "factors")
    assert regime["tier"] == "descriptive" and regime["data"]["regime"] in lab.REGIMES.values()
    # a screening candidate is something to watch, never a verified result
    assert factors["tier"] == "watching" and factors["data"]["candidates"]
    assert not [i for i in items if i["tier"] == "verified"]
