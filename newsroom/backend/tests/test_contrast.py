"""Contrast facts: how a move compares with the trader's own past, the rest of their book and the crowd."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from nr.guard import numbers, phrases
from tests.fakes import T0, WHALE, FakeFeed, context, cycle, ev, iso, position
from tests.pipeline import pipeline
from tests.test_detection import codes, events, fact, packet


def crowd(long_ntl="200000000", long_timing=4, short_timing=3, rank=2, trader=WHALE) -> dict:
    return {"schema": "feed.v1", "as_of": iso(T0 + timedelta(hours=2)), "coin": "BTC", "venue": "hyperliquid",
            "coverage": {"watched": 900, "fresh": 850, "positioned": 600, "latest": iso(T0), "oldest": iso(T0)},
            "sides": [{"side": "long", "traders": 40, "notional": long_ntl, "timing_traders": long_timing,
                       "timing_notional": "9000000"},
                      {"side": "short", "traders": 30, "notional": "150000000", "timing_traders": short_timing,
                       "timing_notional": "4000000"}],
            "largest": [{"side": "long", "rank": 1, "trader_key": "0x" + "99" * 20, "venue": "hyperliquid",
                         "notional_usd": "45000000", "leverage": "10", "entry_px": "97000", "liq_px": None,
                         "timing_significant": False},
                        {"side": "long", "rank": rank, "trader_key": trader, "venue": "hyperliquid",
                         "notional_usd": "30000000", "leverage": "20", "entry_px": "98000", "liq_px": "91000",
                         "timing_significant": False}]}


def test_book_crowd_and_archive_contrasts_carry_their_coverage(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    btc_short = {**cycle(T0 - timedelta(days=20), 900, notional=8000000), "direction": "short"}
    ctx = context(positions=[position(szi=300, mark=100000, entry=98000),
                             position(coin="ETH", szi=-5000, mark=3000, entry=3100),
                             position(coin="SOL", szi=100, mark=150, entry=140)],
                  cycles=[cycle(T0 - timedelta(days=10), 5000, notional=10000000), btc_short,
                          cycle(T0 - timedelta(days=40), -2000, notional=4000000)])
    ctx["fill_coverage"]["truncated"] = True  # the archive starts after the trader's history: no history.* facts
    feed.contexts[WHALE] = ctx
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed)
    with sf() as s:
        pk = packet(s, events(s)[0]["id"])
    assert fact(pk, "book.positions")["value"] == 3 and fact(pk, "book.rank")["value"] == 1
    opp = fact(pk, "book.opposite")
    assert [o["coin"] for o in opp["value"]] == ["ETH"] and opp["display"]["zh"] == ["ETH 空单 1500万美元"]
    assert fact(pk, "crowd.same_side_rank")["value"] == 2 and fact(pk, "crowd.share_of_same_side")["value"] == "0.15"
    assert fact(pk, "crowd.timing_same_side")["value"] == 4 and fact(pk, "crowd.timing_opposite_side")["value"] == 3
    assert fact(pk, "crowd.timing_opposite_side_notional_usd")["display"]["zh"][0] == "400万美元"
    assert fact(pk, "contrast.coin_cycles")["value"] == 3 and fact(pk, "contrast.coin_winning_cycles")["value"] == 2
    assert Decimal(fact(pk, "contrast.vs_largest_prior_cycle")["value"]) == 3
    last = fact(pk, "contrast.last_same_side_net_usd")
    assert last["value"] == "5000" and last["coverage"]["truncated"] is True
    assert fact(pk, "contrast.last_same_side_held")["display"]["zh"] == ["48小时"]
    assert fact(pk, "trader.alltime_pnl_usd")["kind"] == "pnl"
    assert {"HEDGE_POSSIBLE", "CROWD_SAMPLE", "ARCHIVE_PARTIAL", "LEADERBOARD_PNL", "HISTORY_UNAVAILABLE"} <= codes(pk)
    assert fact(pk, "history.complete_cycles") is None
    # a writer may cite every contrast figure the way the display strings give it
    body = ("这笔3000万美元BTC多单是该地址档案中最大BTC周期的3倍，在样本BTC多单中排第2，占同侧名义的15%；"
            "上一笔BTC多单持有48小时，净赚5000美元。同时持有ETH空单1500万美元。择时显著的交易员4人做多、3人做空。")
    assert numbers.check(body, pk["facts"], []) == []
    # the archive start can be cited by month; an ambiguous "高X倍" is stopped
    since = fact(pk, "contrast.archive_since")["value"][5:7].lstrip("0")
    assert numbers.check(f"该地址{since}月以来最大的BTC多单", pk["facts"], []) == []
    assert [f.code for f in numbers.check("2月以来最大", pk["facts"], [])] == ["UNMATCHED_DATE"]
    found = phrases.check("这笔多单比此前最大周期高3倍。", pk, [])
    assert [f.code for f in found] == ["RATIO_AS_INCREASE"]
    assert phrases.check("这笔多单是此前最大周期的3倍。", pk, []) == []


def test_crowd_is_optional_and_never_makes_a_new_packet_version(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    pipeline(sf, feed)  # no coin-positions endpoint: the packet is built and passes without crowd facts
    with sf() as s:
        e = events(s)[0]
        pk = packet(s, e["id"])
        assert e["gate_status"] == "pass" and fact(pk, "crowd.same_side_traders") is None
    feed.research["/market/coin-positions"] = crowd(long_timing=1, short_timing=1)
    later = T0 + timedelta(hours=3)  # the pipeline's clock: only the crowd differs
    from nr.facts.build import build_event_facts
    build_event_facts(sf, feed.client(), e["id"], later)
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM fact_packet")).scalar() == 1  # crowd alone is not material


def test_a_handful_of_timing_traders_is_not_a_split(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    feed.research["/market/coin-positions"] = crowd(long_timing=1, short_timing=1, rank=11, trader="0x" + "77" * 20)
    pipeline(sf, feed)
    with sf() as s:
        pk = packet(s, events(s)[0]["id"])
    assert fact(pk, "crowd.same_side_traders")["value"] == 40 and fact(pk, "crowd.timing_same_side") is None
    assert fact(pk, "crowd.same_side_rank") is None  # not among the largest returned: no rank is claimed


def test_radar_memory_compares_with_earlier_positions_on_the_coin(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "first_seen", 0, 50, 100000, observed=T0 - timedelta(days=5)),
                   ev(2, "close", 50, 0, 100000, observed=T0 - timedelta(days=4)),
                   ev(3, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    pipeline(sf, feed)
    with sf() as s:
        e = next(x for x in events(s) if x["event_class"] == "whale_open")
        pk = packet(s, e["id"])
    assert fact(pk, "radar.prior_positions")["value"] == 1 and fact(pk, "radar.prior_same_side")["value"] == 1
    assert Decimal(fact(pk, "radar.vs_largest_prior_peak")["value"]) == 6
    assert fact(pk, "radar.since")["coverage"]["since"].startswith((T0 - timedelta(days=5)).date().isoformat())
    assert "RADAR_SINCE" in codes(pk)


def test_a_builder_upgrade_rebuilds_the_packet():
    from nr.facts.builder import material_changes
    old = {"builder_version": "facts-2", "event": {}, "gate": {}, "facts": []}
    assert material_changes(old, {**old, "builder_version": "facts-3"}) == ["<builder>"]
    assert material_changes(old, dict(old)) == []
