from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import text

from app.factors import copy_validation as cv
from app.quant.rank import ranks, spearman
from tests.test_zones_api import client  # noqa: F401

DAY0 = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)


def wallets(sf, n):
    with sf.begin() as s:
        start = s.scalar(text("SELECT count(*) FROM wallet"))
        return [s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
            present_in_latest) VALUES (:a, now(), 't', now(), true) RETURNING id"""), {"a": f"0x{i:040x}"}).scalar_one()
                for i in range(start, start + n)]


def grade(score):
    return "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"


def freeze(s, wid, score, at):
    res = {"status": "scored", "score": score, "grade": grade(score), "components": []}
    cv.record(s, wid, res, at)


def test_grade_is_frozen_per_day_and_never_rewritten_once_measured(sf):
    [wid] = wallets(sf, 1)
    with sf.begin() as s:
        freeze(s, wid, 70, DAY0)
        freeze(s, wid, 90, DAY0 + timedelta(hours=2))  # same UTC day: the later run wins
        freeze(s, wid, 40, DAY0 + timedelta(days=1))
        s.execute(text("""INSERT INTO copy_score_outcome (wallet_id, as_of, horizon_days, since, until, ret)
            VALUES (:w, :d, 7, now(), now(), 0.1)"""), {"w": wid, "d": DAY0.date()})
        freeze(s, wid, 10, DAY0 + timedelta(hours=3))  # already measured: stays as it was
        rows = s.execute(text("SELECT as_of, grade, score::float FROM copy_score_history ORDER BY as_of")).all()
    assert [(r.grade, r.score) for r in rows] == [("A", 90), ("D", 40)]


def test_outcome_is_the_copy_over_the_days_after_the_grade(sf, monkeypatch):
    ids = wallets(sf, 3)
    calls = []

    def backtest(s, wid, p, now, prices):
        calls.append((wid, p.days, now))
        return {"ok": True, "ret": 0.05, "leader_ret": 0.08, "trades": 4} if wid == ids[0] else \
            {"ok": False, "reason": "no_decisions"} if wid == ids[1] else {"ok": False, "reason": "no_leader_equity"}

    monkeypatch.setattr(cv, "backtest", backtest)
    monkeypatch.setattr(cv, "PriceBook", lambda s, a, b: None)
    with sf.begin() as s:
        for wid in ids:
            freeze(s, wid, 70, DAY0)
    with sf() as s:
        assert cv.settle(s, DAY0 + timedelta(days=6)) == 0  # no horizon has passed yet
        assert cv.settle(s, DAY0 + timedelta(days=8)) == 3
        assert cv.settle(s, DAY0 + timedelta(days=8)) == 0  # each outcome is computed once
        out = s.execute(text("""SELECT wallet_id, ret::float, trades, reason FROM copy_score_outcome
            WHERE horizon_days = 7 ORDER BY wallet_id""")).all()
    assert {(w, d, n) for w, d, n in calls} == {(w, 7, DAY0 + timedelta(days=7)) for w in ids}
    assert [(r.ret, r.trades, r.reason) for r in out] == [(0.05, 4, None), (0.0, 0, "no_decisions"),
                                                          (None, None, "no_leader_equity")]


def test_ranks_share_ties_and_overlapping_days_are_skipped():
    assert list(ranks(np.array([3.0, 1.0, 3.0, 2.0]))) == [2.5, 0, 2.5, 1]
    assert spearman([1, 2, 3], [5, 5, 5]) is None
    days = [DAY0.date() + timedelta(days=i) for i in (0, 3, 7, 8, 15)]
    assert cv.independent(days, 7) == [days[0], days[2], days[4]]


def seed_outcomes(sf, days, skill, n=cv.MIN_TRADERS):
    ids = wallets(sf, n)
    rng = np.random.default_rng(3)
    with sf.begin() as s:
        for k in range(days):
            at = DAY0 + timedelta(days=7 * k)
            for wid in ids:
                score = float(rng.uniform(20, 95))
                freeze(s, wid, score, at)
                ret = skill * score / 1000 + float(rng.normal(0, 0.01))
                s.execute(text("""INSERT INTO copy_score_outcome (wallet_id, as_of, horizon_days, since, until, ret)
                    VALUES (:w, :d, 7, :a, :b, :r)"""), {"w": wid, "d": at.date(), "a": at, "b": at, "r": ret})


def test_skill_is_reported_only_after_enough_independent_days(sf):
    seed_outcomes(sf, cv.MIN_DAYS - 1, skill=1)
    with sf() as s:
        early = cv.evaluate(s, 7)
    assert early["status"] == "accumulating" and early["p_value"] is None and len(early["days"]) == cv.MIN_DAYS - 1
    seed_outcomes(sf, cv.MIN_DAYS, skill=1)
    with sf() as s:
        result = cv.evaluate(s, 7)
    assert result["status"] == "tested" and result["p_value"] < 0.01 and result["mean_rho"] > 0.5
    assert result["grades"]["A"]["mean"] > result["grades"]["D"]["mean"]


def test_no_skill_is_not_significant(sf):
    seed_outcomes(sf, cv.MIN_DAYS, skill=0)
    with sf() as s:
        result = cv.evaluate(s, 7)
    assert result["status"] == "tested" and result["p_value"] > 0.05


def test_report_endpoint(sf, client):
    seed_outcomes(sf, 2, skill=1)
    with sf.begin() as s:
        freeze(s, wallets(sf, 1)[0], 70, DAY0 + timedelta(days=60))
    body = client.get("/api/v1/copy-board/validation").json()
    seven, thirty = body["horizons"]
    assert body["first_graded"] == DAY0.date().isoformat() and seven["status"] == "accumulating"
    assert len(seven["days"]) == 2 and seven["need_days"] == cv.MIN_DAYS
    assert thirty["graded_days"] == 0 and thirty["next_due"] == (DAY0 + timedelta(days=30)).date().isoformat()


def active(sf, n, start, days, venue="hyperliquid"):
    """n traders deciding every day from start for `days` days."""
    ids = wallets(sf, n)
    with sf.begin() as s:
        s.execute(text("UPDATE wallet SET venue = :v WHERE id = ANY(:w)"), {"v": venue, "w": ids})
        s.execute(text("""INSERT INTO trade_decision (wallet_id, coin, version, kind, side, started_at, ended_at,
            n_fills, size, notional, vwap, pos_before, pos_after, realized_pnl, fee)
            SELECT w, 'BTC', 'p1', 'add', 'buy', t, t, 1, 1, 100, 100, 1, 2, 0, 0
            FROM unnest(CAST(:w AS bigint[])) w, generate_series(:a, :a + make_interval(days => :d), interval '1 day') t"""),
            {"w": ids, "a": start, "d": days})
    return ids


def test_persistence_ranks_past_against_later_copy_returns(sf, monkeypatch):
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ids = active(sf, 40, start, 120)
    rng = np.random.default_rng(4)
    skill = {w: float(rng.normal(0, 0.05)) for w in ids}
    seen = []

    def backtest(s, wid, p, now, prices, history):
        assert history
        seen.append((wid, p.days, now))
        return {"ok": True, "trades": 3, "ret": skill[wid] + float(rng.normal(0, 0.02))}

    monkeypatch.setattr(cv, "backtest", backtest)
    monkeypatch.setattr(cv, "PriceBook", lambda s, a, b: None)
    with sf() as s:
        out = cv.persistence(s, "hyperliquid", 7, start + timedelta(days=120))
    assert out["status"] == "tested" and out["p_value"] < 0.01 and out["mean_rho"] > 0.5
    assert out["top_minus_bottom"] > 0
    dates = [datetime.fromisoformat(d["as_of"]).replace(tzinfo=UTC) for d in out["dates"]]
    assert dates[0] == start + timedelta(days=31) and all(b - a == timedelta(days=7) for a, b in zip(dates, dates[1:]))
    # the past return ends at the date and the later one starts there
    assert {(days, now in dates) for _, days, now in seen if days == 30} == {(30, True)}
    assert all(now - timedelta(days=7) in dates for _, days, now in seen if days == 7)
    skill.update({w: 0.0 for w in ids})
    with sf() as s:
        flat = cv.persistence(s, "hyperliquid", 7, start + timedelta(days=120))
    assert flat["p_value"] > 0.05


def test_persistence_needs_active_traders_and_reports_by_venue(sf, monkeypatch):
    monkeypatch.setattr(cv, "backtest", lambda *a, **k: {"ok": False, "reason": "no_decisions"})
    monkeypatch.setattr(cv, "PriceBook", lambda s, a, b: None)
    with sf() as s:
        assert cv.persistence(s, "okx", 7, datetime.now(UTC))["dates"] == []
    active(sf, 5, datetime(2026, 7, 1, tzinfo=UTC), 60, venue="okx")
    cv.refresh_persistence(sf, datetime(2026, 9, 1, tzinfo=UTC))
    with sf() as s:
        runs = s.scalar(text("SELECT result FROM copy_persistence_run"))["runs"]
    assert [(r["venue"], r["horizon"]) for r in runs] == [("hyperliquid", 7), ("hyperliquid", 30), ("okx", 7), ("okx", 30)]
    okx = runs[2]
    assert okx["dates"] and all(d["n"] == 0 and d["rho"] is None for d in okx["dates"]) and okx["status"] == "accumulating"


def test_persistence_keeps_the_most_recent_dates(sf, monkeypatch):
    monkeypatch.setattr(cv, "backtest", lambda *a, **k: {"ok": False, "reason": "no_decisions"})
    monkeypatch.setattr(cv, "PriceBook", lambda s, a, b: None)
    monkeypatch.setattr(cv, "PERSIST_DATES", 5)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    active(sf, 2, start, 200)
    now = start + timedelta(days=200)
    with sf() as s:
        dates = [d["as_of"] for d in cv.persistence(s, "hyperliquid", 7, now)["dates"]]
    assert len(dates) == 5
    last = datetime.fromisoformat(dates[-1]).replace(tzinfo=UTC)
    assert now - timedelta(days=14) < last + timedelta(days=7) <= now
    # the grid stays anchored to the first eligible date
    assert (last - (start + timedelta(days=31))).days % 7 == 0


def test_shared_denominator_fools_the_rank_test_but_not_the_direction_test(sf, monkeypatch):
    start = datetime(2026, 7, 1, tzinfo=UTC)
    ids = active(sf, 60, start, 150, venue="okx")
    rng = np.random.default_rng(8)
    # profitable on average, no persistence, each return divided by the same (current) equity in both windows
    scale = {w: float(np.exp(rng.normal(0, 1))) for w in ids}

    def backtest(s, wid, p, now, prices, history):
        return {"ok": True, "trades": 3, "ret": scale[wid] * float(rng.normal(0.02, 0.03))}

    monkeypatch.setattr(cv, "backtest", backtest)
    monkeypatch.setattr(cv, "PriceBook", lambda s, a, b: None)
    with sf() as s:
        out = cv.persistence(s, "okx", 7, start + timedelta(days=150))
    assert out["p_value"] < 0.01 and out["mean_rho"] > 0.1  # spurious
    assert out["sign_p"] > 0.05 and abs(out["sign_mean"]) < 0.1
    assert out["shared_denominator"]


def test_changed_method_is_recomputed_after_deployment(sf, monkeypatch):
    from tests.test_worker import run, schedule
    run(sf, "copy_persistence", datetime.now(UTC) - timedelta(days=1))
    with sf.begin() as s:
        s.execute(text("""INSERT INTO copy_persistence_run (result) VALUES (CAST(:r AS jsonb))"""),
                  {"r": '{"version": 1, "runs": []}'})
    soon = schedule(sf, monkeypatch)["copy_persistence"][1]
    assert soon < datetime.now(UTC) + timedelta(minutes=46)
    with sf.begin() as s:
        s.execute(text("UPDATE copy_persistence_run SET result = jsonb_set(result, '{version}', :v)"),
                  {"v": str(cv.PERSIST_VERSION)})
    assert schedule(sf, monkeypatch)["copy_persistence"][1].weekday() == 6  # back on Sunday
