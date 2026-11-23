from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from tests.fakes import OTHER, T0, WHALE, FakeFeed, context, cycle, ev, health, position
from tests.pipeline import pipeline


def events(s):
    return [dict(r) for r in s.execute(text("SELECT * FROM news_event ORDER BY id")).mappings()]


def packet(s, event_id):
    return s.execute(text("""SELECT p.content FROM fact_packet p JOIN news_event e ON e.current_fact_packet_id = p.id
                             WHERE e.id = :e"""), {"e": event_id}).scalar()


def fact(pk, fid):
    return next((f for f in pk["facts"] if f["id"] == fid), None)


def codes(pk):
    return {c["code"] for c in pk["constraints"]}


def test_first_seen_is_never_an_open(sf):
    feed = FakeFeed()
    # already open when first polled, then a large add
    feed.events = [ev(1, "first_seen", 0, 30, 100000), ev(2, "increase", 30, 45, 100000)]
    feed.contexts[WHALE] = context(positions=[position(szi=45)])
    pipeline(sf, feed)
    with sf() as s:
        evs = events(s)
        assert [e["event_class"] for e in evs] == ["whale_add"]
        ep = s.execute(text("SELECT start_kind, start_precision, start_from FROM position_episode")).one()
        assert ep == ("first_seen", "unknown", None)
        pk = packet(s, evs[0]["id"])
        assert "FIRST_SEEN_NOT_OPEN" in codes(pk) and fact(pk, "episode.first_seen_at")["label"] == "first_observed"
        assert fact(pk, "episode.opened_at") is None and fact(pk, "episode.opened_window") is None
        assert "TIME_WINDOW_ONLY" in codes(pk) and fact(pk, "change.time") is None


def test_adds_merge_until_published_then_only_higher_tiers(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000), ev(2, "increase", 30, 41, 100000), ev(3, "increase", 41, 52, 100000)]
    feed.contexts[WHALE] = context(positions=[position(szi=52)])
    pipeline(sf, feed)
    with sf() as s:
        adds = [e for e in events(s) if e["event_class"] == "whale_add"]
        assert len(adds) == 1 and adds[0]["source_event_count"] == 2
        assert adds[0]["escalation_level"] == 2  # crossed $5M
        pk = packet(s, adds[0]["id"])
        assert Decimal(fact(pk, "change.notional_usd")["value"]) == 2200000 and fact(pk, "change.observations")["value"] == 2
        # pretend it was published
        s.execute(text("UPDATE news_event SET state = 'covered' WHERE id = :i"), {"i": adds[0]["id"]})
        s.commit()
    feed.events.append(ev(4, "increase", 52, 58, 100000))   # +$0.6M, same tier: nothing new
    pipeline(sf, feed)
    with sf() as s:
        assert len([e for e in events(s) if e["event_class"] == "whale_add"]) == 1
    feed.events.append(ev(5, "increase", 58, 120, 100000))  # crosses $10M: a follow-up
    feed.contexts[WHALE] = context(positions=[position(szi=120)])
    pipeline(sf, feed)
    with sf() as s:
        adds = [e for e in events(s) if e["event_class"] == "whale_add"]
        assert len(adds) == 2 and adds[1]["escalation_level"] == 3
        roles = s.execute(text("SELECT role FROM story_event ORDER BY seq")).scalars().all()
        assert roles[0] == "origin" and roles[-1] == "follow_up"


def test_unrealized_pnl_and_estimates_are_labelled(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=101000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300, mark=100000, entry=101000, lev=25, liq=97000)])
    pipeline(sf, feed)
    with sf() as s:
        e = events(s)[0]
        pk = packet(s, e["id"])
        upl = fact(pk, "position.unrealized_pnl_usd")
        assert upl["kind"] == "unrealized_pnl" and Decimal(upl["value"]) == -300000
        assert {"PNL_UNREALIZED", "NOTIONAL_NOT_MARGIN", "OBSERVED_SAMPLE", "NO_IDENTITY"} <= codes(pk)
        assert fact(pk, "position.implied_margin_usd")["kind"] == "estimate"
        assert not [f for f in pk["facts"] if f["kind"] == "realized_pnl"]
        assert e["state"] == "candidate" and e["gate_status"] == "pass"
        assert set(e["score_breakdown"]["components"]) >= {"size_abs", "size_vs_account", "trader_record", "risk"}


def test_history_needs_complete_coverage(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000)]
    wins = [cycle(T0 - timedelta(days=10 * i), 5000) for i in range(1, 5)]
    feed.contexts[WHALE] = context(synced=False, cycles=wins)
    pipeline(sf, feed)
    with sf() as s:
        pk = packet(s, events(s)[0]["id"])
        assert "HISTORY_UNAVAILABLE" in codes(pk)
        assert not [f for f in pk["facts"] if f["id"].startswith("history.")]


def test_streak_needs_a_known_start(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000)]
    # four wins, all the archive holds: the streak's start is unknown, so no streak fact
    feed.contexts[WHALE] = context(cycles=[cycle(T0 - timedelta(days=10 * i), 5000) for i in range(1, 5)])
    pipeline(sf, feed)
    with sf() as s:
        pk = packet(s, events(s)[0]["id"])
        assert fact(pk, "history.complete_cycles")["value"] == 4 and fact(pk, "history.win_streak") is None
        assert fact(pk, "history.complete_cycles")["coverage"]["complete_only"] is True
    feed2 = FakeFeed()
    feed2.events = [ev(2, "open", 0, 30, 100000, trader=OTHER)]
    cyc = [cycle(T0 - timedelta(days=10 * i), 5000) for i in range(1, 4)] + [cycle(T0 - timedelta(days=50), -2000)]
    cyc.append(cycle(T0 - timedelta(days=60), 3000, complete=False))  # incomplete cycles never count
    feed2.contexts[OTHER] = context(trader=OTHER, cycles=cyc)
    pipeline(sf, feed2)
    with sf() as s:
        e = [x for x in events(s) if x["trader_id"] != events(s)[0]["trader_id"]][0]
        pk = packet(s, e["id"])
        assert fact(pk, "history.win_streak")["value"] == 3 and fact(pk, "history.complete_cycles")["value"] == 4


def test_suspected_liquidation_then_confirmed(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000, liq=91000),
                   ev(2, "close", 30, 0, 90800, entry=100000, liq=91000, side="long")]
    feed.contexts[WHALE] = context(positions=[])
    pipeline(sf, feed)
    with sf() as s:
        liq = [e for e in events(s) if e["event_class"] == "liquidation"][0]
        pk = packet(s, liq["id"])
        assert liq["liquidation_status"] == "suspected" and "LIQ_SUSPECTED_ONLY" in codes(pk)
        assert fact(pk, "close.pnl_estimate_usd")["kind"] == "estimate"
        whale_open = [e for e in events(s) if e["event_class"] == "whale_open"][0]
        assert whale_open["gate_status"] == "blocked"  # the position is gone from the current context
        assert "CONTRADICTED_BY_CONTEXT" in [b["code"] for b in whale_open["blocking_issues"]]
    observed = T0 + timedelta(minutes=60)
    feed.contexts[WHALE] = context(positions=[], liqs=[{
        "time": (observed - timedelta(minutes=10)).isoformat(), "coin": "BTC", "px": "90900", "sz": "30",
        "dir": "Close Long", "closed_pnl": "-273000", "liquidation": {"liquidatedUser": WHALE}}])
    with sf() as s:
        s.execute(text("UPDATE news_event SET facts_refreshed_at = NULL"))
        s.commit()
    from nr.facts.build import refresh_due
    refresh_due(sf, T0 + timedelta(hours=4))
    pipeline(sf, feed, T0 + timedelta(hours=4))
    with sf() as s:
        liq = [e for e in events(s) if e["event_class"] == "liquidation"][0]
        pk = packet(s, liq["id"])
        assert liq["liquidation_status"] == "confirmed" and liq["current_fact_version"] == 2
        assert "LIQ_SUSPECTED_ONLY" not in codes(pk) and fact(pk, "liquidation.closed_pnl_usd")["kind"] == "realized_pnl"
        assert len([e for e in events(s) if e["event_class"] == "liquidation"]) == 1  # same event, new version


def test_gate_blocks_stale_sources_and_traders(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000)]
    feed.contexts[WHALE] = context(fresh=False, positions=[position(szi=300)])
    feed.health_doc = health(hl="stale")
    pipeline(sf, feed)
    with sf() as s:
        e = events(s)[0]
        blocked = {b["code"] for b in e["blocking_issues"]}
        assert e["state"] == "blocked" and {"SOURCE_STALE", "TRADER_STALE"} <= blocked
    # data recovers: the next refresh unblocks it
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    feed.health_doc = health(head=1)
    with sf() as s:
        s.execute(text("UPDATE news_event SET facts_refreshed_at = NULL"))
        s.commit()
    from nr.facts.build import refresh_due
    refresh_due(sf, T0 + timedelta(hours=3))
    pipeline(sf, feed)
    with sf() as s:
        e = events(s)[0]
        assert e["state"] == "candidate" and e["gate_status"] == "pass"
        hist = s.execute(text("SELECT to_state FROM event_state_history ORDER BY id")).scalars().all()
        assert hist == ["detected", "blocked", "candidate"]


def test_notable_move_needs_a_verified_record(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 4, 100000), ev(2, "open", 0, 4, 100000, trader=OTHER)]
    feed.contexts[WHALE] = context(positions=[position(szi=4)])
    feed.contexts[OTHER] = context(trader=OTHER, positions=[position(szi=4)],
                                   styles=[{"style": "trend", "version": "s2", "status": "verified", "confidence": "1",
                                            "n": 40, "oos": None, "computed_at": T0.isoformat()}])
    pipeline(sf, feed)
    with sf() as s:
        st = {r[0]: r[1] for r in s.execute(text("""SELECT t.trader_key, e.state FROM news_event e
                                                    JOIN trader t ON t.id = e.trader_id"""))}
        assert st[WHALE] == "dismissed"
        assert st[OTHER] in ("candidate", "dismissed")
        pk = packet(s, events(s)[1]["id"])
        assert fact(pk, "trader.record")["value"] == ["style trend verified"]
