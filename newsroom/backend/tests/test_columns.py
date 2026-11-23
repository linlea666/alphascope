import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from nr.cards.render import render_for_packet
from nr.columns import schedule
from nr.columns.run import build_column
from nr.jobs.handlers import Deps, run_pending
from tests.fakes import OTHER, T0, WHALE, FakeFeed, context, ev, iso, position
from tests.helpers import NOW, newsroom
from tests.pipeline import pipeline


def board_doc(n=5):
    return {"schema": "feed.v1", "as_of": iso(NOW), "window": "30d",
            "run": {"id": 1, "business_date": "2026-11-20", "completed_at": iso(NOW - timedelta(hours=5))},
            "assumptions": {"params_key": "default-v2"},
            "rows": [{"rank": i, "trader_key": f"okx:10000000{i}", "display_name": f"Trader{i}", "pnl": "50000",
                      "pnl_ratio": str(3 - i * 0.4), "win_rate": "0.6", "max_drawdown": "0.2", "asset": "100000",
                      "copy_ret": str(0.9 - i * 0.2), "copy_mdd": "-0.3", "leader_ret": "1.2", "retention": "0.5",
                      "trades": 20, "liquidations": 0, "approx_equity": True, "backtest_at": iso(NOW)}
                     for i in range(1, n + 1)]}


def feed_with_health():
    feed = FakeFeed()
    feed.events = [ev(1, "first_seen", 0, 1, 100000, observed=NOW - timedelta(minutes=10))]
    return feed


def run(sf, feed, key, params=None):
    pipeline(sf, feed, NOW)  # sync + health so the gate has fresh inputs
    with feed.client() as c:
        return build_column(sf, c, key, NOW, params or {})


def packet_of(sf, event_id):
    with sf() as s:
        return s.execute(text("""SELECT p.id, p.content FROM fact_packet p JOIN news_event e
                                 ON e.current_fact_packet_id = p.id WHERE e.id = :e"""), {"e": event_id}).one()


def test_copy_vs_board_issue_and_chart(sf):
    newsroom(sf)
    feed = feed_with_health()
    feed.research["/copy/board"] = board_doc()
    r = run(sf, feed, "copy_vs_board")
    issue = r["issues"][0]
    assert issue["created"] and issue["gate"] == "pass" and issue["state"] == "candidate"
    pid, pk = packet_of(sf, issue["event_id"])
    codes = {c["code"] for c in pk["constraints"]}
    assert {"COPY_BACKTEST_ASSUMPTIONS", "SURVIVORSHIP"} <= codes and pk["event"]["class"] == "column:copy_vs_board"
    facts = {f["id"]: f for f in pk["facts"]}
    assert facts["row.1.board_roi"]["display"]["zh"][0] == "260%" and facts["row.1.copy_return"]["value"] == "0.7"
    # same period again: the same issue, no new version
    assert run(sf, feed, "copy_vs_board")["issues"][0] == {**issue, "created": False, "changed": False}
    out = render_for_packet(sf, pid, "chart_card", "zh")
    with sf() as s:
        svg = Path(s.execute(text("SELECT file_path FROM media_asset WHERE id = :i"),
                             {"i": out["assets"]["card_svg"]}).scalar()).read_text()
    displays = {d for f in pk["facts"] for d in f["display"].get("zh", [])}
    values = [v for v in re.findall(r'font-size="17" fill="[^"]+">([^<]+)</text>', svg) if "%" in v]
    assert values and all(v in displays for v in values)
    with sf() as s:  # the editor-in-chief gets it like any other candidate
        assert s.execute(text("SELECT count(*) FROM job WHERE kind = 'eic_evaluate'")).scalar() >= 1


def test_thin_column_is_blocked(sf):
    newsroom(sf)
    feed = feed_with_health()
    feed.research["/copy/board"] = board_doc(2)
    issue = run(sf, feed, "copy_vs_board")["issues"][0]
    assert issue["gate"] == "blocked" and issue["state"] == "blocked"


def test_funding_divergence_only_when_triggered(sf):
    newsroom(sf)
    feed = feed_with_health()
    frame = {"bucket_ts": iso(NOW - timedelta(minutes=30)), "venue": "hyperliquid", "smart_n": 12,
             "smart_long": "0.3", "control_n": 12, "control_long": "0.6", "divergence": "-0.3", "sample_n": 400}
    doc = {"schema": "feed.v1", "as_of": iso(NOW), "coin": "BTC", "frames": [frame],
           "funding": {"bucket": iso(NOW), "rate": "0.0004", "source_at": iso(NOW), "level": "high", "percentile": 0.5}}
    feed.research["/research/funding"] = doc
    assert run(sf, feed, "funding_divergence", {"coins": ["BTC"]})["issues"] == []
    doc["funding"]["percentile"] = 0.97  # crowded longs while specialists lean short
    issues = run(sf, feed, "funding_divergence", {"coins": ["BTC"]})["issues"]
    assert len(issues) == 1 and issues[0]["gate"] == "pass"
    pk = packet_of(sf, issues[0]["event_id"])[1]
    assert "SCREEN_NOT_SIGNAL" in {c["code"] for c in pk["constraints"]}


def test_streak_breaker_needs_a_known_start_and_full_archive(sf):
    newsroom(sf)
    feed = feed_with_health()
    row = {"trader_key": WHALE, "venue": "hyperliquid", "display_name": None, "coin": "BTC", "direction": "long",
           "opened_at": iso(NOW - timedelta(days=2)), "version": "p1", "closed_at": iso(NOW - timedelta(hours=3)),
           "status": "closed", "scale": "swing", "holding_hours": "45", "entry_px": "100000", "exit_px": "95000",
           "max_notional": "1000000", "realized_pnl": "-48000", "fees": "500", "prior_streak": 6, "prior_known": True,
           "coverage_status": "synced", "truncated": False, "earliest_fill_at": iso(NOW - timedelta(days=120))}
    feed.research["/cycles/closed"] = {"schema": "feed.v1", "as_of": iso(NOW), "hours": 48,
                                       "rows": [row, {**row, "trader_key": OTHER, "prior_known": False}]}
    issues = run(sf, feed, "streak_breaker")["issues"]
    assert len(issues) == 1
    pk = packet_of(sf, issues[0]["event_id"])[1]
    facts = {f["id"]: f for f in pk["facts"]}
    assert facts["streak.before"]["value"] == 6 and facts["cycle.net_realized_usd"]["kind"] == "realized_pnl"
    with sf() as s:  # a trader column joins that trader's story
        assert s.execute(text("SELECT count(*) FROM story_event")).scalar() == 1


def test_recap_after_a_covered_position_closes(sf):
    from tests.test_api import ZH  # noqa: F401  (same draft text as the API flow)
    from nr.articles.versions import Draft, save_version
    from nr.desk import assignment
    from nr.publish import service as pub
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)],
                                   styles=[{"style": "trend", "version": "s2", "status": "verified", "confidence": "1",
                                            "n": 40, "oos": None, "computed_at": iso(T0)}])
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid = s.execute(text("SELECT id FROM news_event")).scalar()
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        save_version(s, art, Draft(body="一个地址开出约3000万美元 BTC 多单。", facts_used=["position.notional_usd"]),
                     origin="human", user_id=nr["chief"])
        pub.submit(s, art, nr["chief"])
        pub.approve(s, art, nr["chief"])
        pub.mark_published(s, art, nr["chief"], "https://x.com/alphascope_cn/status/1860000000000000777", NOW, NOW)
        s.commit()
    feed.events.append(ev(2, "close", 300, 0, 101000, entry=98000, side="long"))
    pipeline(sf, feed, NOW)
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM job WHERE kind = 'column_build'")).scalar() == 1
    run_pending(sf, Deps(feed=feed.client, now=lambda: NOW), ("column_build",))
    with sf() as s:
        recap = s.execute(text("SELECT id, gate_status FROM news_event WHERE column_key = 'recap'")).one()
    pk = packet_of(sf, recap[0])[1]
    facts = {f["id"]: f for f in pk["facts"]}
    assert facts["outcome"]["value"] == "closed" and "chapter.1.position.notional_usd" in facts
    assert "HINDSIGHT" in {c["code"] for c in pk["constraints"]}
    # the weekly scorecard picks up the published story
    with feed.client() as c:
        sc = build_column(sf, c, "weekly_scorecard", NOW)["issues"][0]
    facts = {f["id"]: f for f in packet_of(sf, sc["event_id"])[1]["facts"]}
    assert facts["item.1.position_now"]["value"] == "closed" and facts["items"]["value"] == 1


def test_style_changes_from_own_snapshots(sf):
    import json
    newsroom(sf)
    with sf() as s:
        tid = s.execute(text("INSERT INTO trader (trader_key, venue) VALUES (:k, 'hyperliquid') RETURNING id"),
                        {"k": WHALE}).scalar()
        for days, styles in ((60, [{"style": "steady", "status": "verified"}]),
                             (1, [{"style": "high_risk", "status": "verified"}])):
            s.execute(text("""INSERT INTO trader_context_snapshot (trader_id, fetched_at, payload, payload_hash)
                              VALUES (:t, :f, CAST(:p AS jsonb), :h)"""),
                      {"t": tid, "f": NOW - timedelta(days=days), "p": json.dumps({"quality": {"styles": styles}}),
                       "h": str(days) * 8})
        s.commit()
    feed = feed_with_health()
    issue = run(sf, feed, "style_changes")["issues"][0]
    facts = {f["id"]: f for f in packet_of(sf, issue["event_id"])[1]["facts"]}
    assert facts["row.1.before"]["value"] == "steady" and facts["row.1.after"]["value"] == "high_risk"


def test_schedule_runs_on_the_right_days(sf):
    from datetime import datetime, UTC
    monday = datetime(2026, 11, 23, 1, tzinfo=UTC)
    assert schedule.due("weekly", monday) == "2026-W48" and schedule.due("weekly", monday + timedelta(days=1)) is None
    assert schedule.due("monthly", datetime(2026, 12, 1, tzinfo=UTC)) == "2026-12"
    newsroom(sf)
    queued = schedule.tick(sf, monday)
    assert {"weekly_scorecard", "copy_vs_board", "liq_ladder", "streak_breaker", "funding_divergence"} <= set(queued)
    assert "recap" not in queued and "dossier" not in queued
    assert schedule.tick(sf, monday) == []  # already queued
