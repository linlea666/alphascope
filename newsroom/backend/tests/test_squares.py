"""The squares (docs/16): boards told as people (multiples, small accounts left out, the fallen), personas and story
stages, duels between stars, and the question a post asked answered later."""

import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from nr.columns.run import build_column
from nr.stories import squares
from tests.fakes import iso
from tests.helpers import NOW, newsroom
from tests.test_columns import feed_with_health
from tests.pipeline import pipeline

A, B = "binance:10", "binance:20"


def mover(rank, key, name, ratio, pnl, asset, off=0.01, prev=None, new=False):
    return {"rank": rank, "trader_key": key, "display_name": name, "handle": None, "pnl": str(pnl),
            "pnl_ratio": str(ratio), "win_rate": "0.6", "max_drawdown": "0.2", "asset": str(asset), "prev_rank": prev,
            "rank_change": (prev - rank) if prev else None, "new_entry": new, "multiple": str(round(1 + ratio, 2)),
            "off_peak": off, "days": 8}


def movers_doc(rows, date="2026-11-20"):
    return {"schema": "feed.v1", "as_of": iso(NOW), "window": "7d",
            "run": {"id": 9, "business_date": date, "completed_at": iso(NOW - timedelta(hours=2))},
            "previous_run": {"id": 8, "business_date": "2026-11-19", "completed_at": iso(NOW - timedelta(days=1))},
            "rows": rows, "dropped": []}


def test_multiples_list_leaves_out_tiny_accounts_and_snapshots_the_board(sf):
    newsroom(sf)
    feed = feed_with_health()
    feed.research["/okx/board/movers"] = movers_doc([
        mover(1, "okx:1000000001", "夕阳下的挣扎", 174.67, 34423, 199),  # 175x on a $199 account: not headline material
        mover(2, "okx:1000000002", "中二的天才交易员", 7.37, 145623, 29052, prev=1),
        mover(3, "okx:1000000003", "三年好日子", 6.75, 253000, 8988, prev=2),
        mover(4, "okx:1000000004", "財神爺8", 4.23, 129689, 95943, new=True),
        mover(5, "okx:1000000005", "得水", 3.94, 36213, 28410, prev=3)])
    pipeline(sf, feed, NOW)
    with feed.client() as c:
        out = build_column(sf, c, "okx_multipliers", NOW, {"windows": ["7d"]})["issues"]
    assert len(out) == 1 and out[0]["gate"] == "pass"
    with sf() as s:
        facts = {f["id"]: f for f in s.execute(text(
            "SELECT content->'facts' FROM fact_packet WHERE news_event_id = :e"), {"e": out[0]["event_id"]}).scalar()}
        assert facts["row.1.trader"]["value"] == "中二的天才交易员" and facts["row.1.multiple"]["display"]["zh"] == ["8.4倍"]
        assert facts["small_accounts_skipped"]["value"] == 1 and facts["row.3.new_entry"]["value"] == "新上榜"
        # "OKX 7 天翻倍榜": the 7 is a fact the guard can match
        from nr.guard import numbers
        assert numbers.check("OKX 7 天翻倍榜，中二的天才交易员 8.4 倍", list(facts.values()), []) == []
        # 1st on the previous board, 2nd now: the id itself says which way
        assert facts["row.1.places_down"]["value"] == 1 and "row.1.places_up" not in facts
        assert s.execute(text("SELECT count(*) FROM trader_board_snapshot")).scalar() == 5
        assert s.execute(text("SELECT venue FROM news_event WHERE id = :e"), {"e": out[0]["event_id"]}).scalar() == "okx"


def test_a_board_high_now_far_off_its_peak_is_a_fallen_genius(sf):
    newsroom(sf)
    feed = feed_with_health()
    key = "okx:2000000001"
    with sf() as s:  # ten days ago: #2 on the 30-day board, up 900%
        s.execute(text("""INSERT INTO trader_board_snapshot (trader_key, board, business_date, rank, pnl, pnl_ratio, asset, name)
                          VALUES (:k, '30d', :d, 2, 300000, 9, 50000, '来福')"""), {"k": key, "d": (NOW - timedelta(days=10)).date()})
        s.commit()
    feed.research["/okx/board/movers"] = movers_doc([mover(40, key, "来福", 2.1, 120000, 30000, off=0.62)])
    pipeline(sf, feed, NOW)
    with feed.client() as c:
        out = build_column(sf, c, "okx_fallen", NOW)["issues"]
    assert len(out) == 1
    with sf() as s:
        facts = {f["id"]: f for f in s.execute(text(
            "SELECT content->'facts' FROM fact_packet WHERE news_event_id = :e"), {"e": out[0]["event_id"]}).scalar()}
        assert facts["peak.multiple"]["display"]["zh"] == ["10倍"] and facts["now.off_peak"]["display"]["zh"][0] == "62%"
        assert facts["now.status"]["value"] == "从高点大幅回撤"


def test_stages_follow_plain_rules():
    cfg = {"fall_off_peak": 0.4}
    assert squares.stage_for({"multiple": 4, "off_peak": 0.02}, None, cfg) == "peak"
    assert squares.stage_for({"off_peak": 0.5}, "peak", cfg) == "fall"
    assert squares.stage_for({"off_peak": 0.25}, None, cfg) == "crisis"
    assert squares.stage_for({"win_streak": 5}, None, cfg) == "streak"
    assert squares.stage_for({"multiple": 3.5, "off_peak": 0.01}, "fall", cfg) == "comeback"
    assert squares.stage_for({"pnl_7d": -40000, "equity": 100000}, None, cfg) == "fall"
    tags = {t["tag"] for t in squares.tags_for({"max_lever": 75, "closes": 8, "short_share": 0.8, "top_coin": "ETH",
                                                "top_coin_share": 0.75})}
    assert tags == {"梭哈型", "空军司令", "ETH 专业户"}


def stars(sf):
    with sf() as s:
        for key, name in ((A, "熬鹰"), (B, "大表哥")):
            s.execute(text("INSERT INTO star_trader (trader_key, venue, name, equity) VALUES (:k, 'binance', :n, 100000)"),
                      {"k": key, "n": name})
            s.execute(text("INSERT INTO trader (trader_key, venue, display_name) VALUES (:k, 'binance', :n)"),
                      {"k": key, "n": name})
        s.commit()


def hold(s, key, side, pid, open_=True):
    s.execute(text("""INSERT INTO relay_position (trader_key, position_id, symbol, side, size, entry_px, mark_px, upl, lever,
                                                  notional, margin, first_seen_at, last_seen_at, open)
                      VALUES (:k, :p, 'ETHUSDT', :sd, 10, 3000, 2950, :upl, 20, 29500, 1500, :t, :t, :o)
                      ON CONFLICT (trader_key, position_id) DO UPDATE SET open = EXCLUDED.open"""),
              {"k": key, "p": pid, "sd": side, "upl": 500 if side == "short" else -500, "t": NOW - timedelta(hours=2),
               "o": open_})


def test_two_stars_on_opposite_sides_duel_and_the_result_follows(sf):
    newsroom(sf)
    stars(sf)
    with sf() as s:
        hold(s, A, "short", "a1")
        hold(s, B, "long", "b1")
        s.commit()
    assert squares.detect_duels(sf, NOW) == {"opened": 1, "resolved": 0}
    assert squares.detect_duels(sf, NOW) == {"opened": 0, "resolved": 0}  # one duel per pair and coin
    with sf() as s:
        hold(s, B, "long", "b1", open_=False)
        s.execute(text("""INSERT INTO relay_close (trader_key, position_id, symbol, side, opened_at, closed_at, pnl, full_close)
                          VALUES (:k, 'b1', 'ETHUSDT', 'long', :o, :c, -800, true)"""),
                  {"k": B, "o": NOW - timedelta(hours=2), "c": NOW - timedelta(minutes=5)})
        s.commit()
    assert squares.detect_duels(sf, NOW) == {"opened": 0, "resolved": 1}
    with sf() as s:
        d = s.execute(text("SELECT id, result FROM star_duel")).mappings().one()
        assert d["result"]["winner"] == A and d["result"]["closed"] == [B]
        jobs = s.execute(text("SELECT payload->>'column' FROM job WHERE kind = 'column_build' ORDER BY id")).scalars().all()
        assert jobs == ["duel", "duel_result"]
        # the result piece names both and shows the closed side's realized loss
        from nr.columns.squares import duel_result
        it = duel_result(s, None, NOW, {"duel_id": d["id"]})[0]
        f = {x["id"]: x for x in it.facts.items}
        assert f["b.realized_pnl"]["value"] == "-800" and f["a.status"]["value"] == "还没平仓"
        assert f["duel.leader"]["value"] == "熬鹰"


def test_a_persona_turn_and_an_answered_question(sf):
    nr = newsroom(sf)
    stars(sf)
    with sf() as s:
        s.execute(text("UPDATE star_trader SET stats_30d = CAST(:st AS jsonb) WHERE trader_key = :k"),
                  {"k": A, "st": json.dumps({"roi": 150, "win_rate": 70})})
        for i, pnl in enumerate([900, 800, 700, 600, -100]):  # four wins in a row after a loss
            s.execute(text("""INSERT INTO relay_close (trader_key, position_id, symbol, side, opened_at, closed_at, pnl,
                                                       lever, full_close)
                              VALUES (:k, :p, 'ETHUSDT', 'short', :o, :c, :pnl, 60, true)"""),
                      {"k": A, "p": f"c{i}", "o": NOW - timedelta(days=2, hours=i), "c": NOW - timedelta(days=1, hours=i),
                       "pnl": pnl})
        s.execute(text("""INSERT INTO trader_persona (trader_key, venue, name, stage) VALUES (:k, 'binance', '熬鹰', 'quiet')"""),
                  {"k": A})
        s.commit()
    r = squares.refresh_personas(sf, None, NOW)
    assert r["turns"] >= 1
    with sf() as s:
        p = s.execute(text("SELECT stage, prev_stage, tags FROM trader_persona WHERE trader_key = :k"), {"k": A}).mappings().one()
        assert (p["stage"], p["prev_stage"]) == ("peak", "quiet")
        assert {"梭哈型", "空军司令", "ETH 专业户", "翻倍选手"} <= {t["tag"] for t in p["tags"]}
        from nr.columns.squares import story_turn
        it = story_turn(s, None, NOW, {"trader_key": A, "stage": "peak"})[0]
        f = {x["id"]: x for x in it.facts.items}
        assert f["stage.now"]["value"] == "巅峰" and f["traits.win_streak"]["value"] == 4
    # a published post on a star's short asked whether it ends in profit; the relay's history answers
    from nr.articles.versions import Draft, save_version
    from nr.desk import assignment
    from nr.publish import service as pub
    from tests.helpers import candidate_event
    eid = candidate_event(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        save_version(s, art, Draft(body="一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。",
                                   facts_used=["position.notional_usd"]), origin="ai")
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = :x WHERE id = :a"), {"x": NOW + timedelta(hours=1), "a": art})
        pub.mark_published(s, art, nr["chief"], None, NOW, NOW)
        story = s.execute(text("SELECT story_id FROM article WHERE id = :a"), {"a": art}).scalar()
        s.execute(text("""INSERT INTO story_prediction (story_id, article_id, trader_key, question, kind, params, due_at, created_at)
                          VALUES (:s, :a, :k, '这笔 $ETH 空单最后是赚是亏？', 'position_closed', CAST(:p AS jsonb), :due, :c)"""),
                  {"s": story, "a": art, "k": A, "p": json.dumps({"coin": "ETH", "side": "short"}),
                   "due": NOW + timedelta(days=7), "c": NOW - timedelta(hours=3)})
        s.execute(text("""INSERT INTO relay_close (trader_key, position_id, symbol, side, opened_at, closed_at, pnl, full_close)
                          VALUES (:k, 'q1', 'ETHUSDT', 'short', :o, :c, 2500, true)"""),
                  {"k": A, "o": NOW - timedelta(hours=5), "c": NOW - timedelta(hours=1)})
        s.commit()
    assert squares.resolve_predictions(sf, NOW)["resolved"] == 1
    with sf() as s:
        pid = s.execute(text("SELECT id FROM story_prediction")).scalar()
        from nr.columns.squares import prediction_followup
        it = prediction_followup(s, None, NOW, {"prediction_id": pid})[0]
        f = {x["id"]: x for x in it.facts.items}
        assert f["answer"]["value"] == "赚了" and f["result.realized_pnl"]["value"] == "2500"
        assert Decimal(str(f["result.held"]["value"])) == 4


def test_with_autopilot_on_a_square_column_is_taken_whatever_its_priority(sf):
    from nr import settings_store
    from nr.desk.eic import evaluate_event
    from tests.fakes import FakeProvider
    from tests.helpers import eic_output
    newsroom(sf)
    feed = feed_with_health()
    feed.research["/okx/board/movers"] = movers_doc([mover(i, f"okx:300000000{i}", f"T{i}", 5 - i * 0.5, 50000, 20000)
                                                    for i in range(1, 6)])
    pipeline(sf, feed, NOW)
    with feed.client() as c:
        eid = build_column(sf, c, "okx_multipliers", NOW, {"windows": ["7d"]})["issues"][0]["event_id"]
    with sf() as s:
        settings_store.put(s, "desk", {"auto_accept": True, "auto_min_priority": "p1"})
        sq = s.execute(text("SELECT id FROM editorial_account WHERE platform = 'okx_square'")).scalar()
        s.execute(text("UPDATE news_event SET state = 'candidate' WHERE id = :e"), {"e": eid})
        s.commit()
    out = eic_output(eid, [(sq, "翻倍榜")])
    out["priority"] = "p3"  # a routine column to the EIC
    out["key_fact_ids"] = ["row.1.multiple"]
    out["newsworthiness"][0]["fact_ids"] = ["row.1.multiple"]
    r = evaluate_event(sf, eid, NOW, transport=FakeProvider(out))
    assert len(r["auto_accepted"]) == 1
