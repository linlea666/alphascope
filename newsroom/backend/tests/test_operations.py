from datetime import timedelta

from sqlalchemy import text

from nr import maintenance, settings_store
from nr.detect.at_risk import poll_at_risk
from nr.jobs.handlers import Deps, run_pending
from nr.jobs.queue import AI_KINDS, enqueue
from tests.fakes import WHALE, FakeFeed, context, position
from tests.helpers import NOW, newsroom
from tests.pipeline import pipeline


def test_authorization_expires_on_schedule(sf):
    nr = newsroom(sf)
    with sf() as s:
        s.execute(text("UPDATE editorial_account SET authorization_expires_at = :t WHERE id = :a"),
                  {"t": NOW - timedelta(minutes=1), "a": nr["en"]})
        s.commit()
    assert maintenance.run(sf, NOW)["expired_accounts"] == 1
    with sf() as s:
        st = dict(s.execute(text("SELECT id, authorization_status FROM editorial_account")).all())
        assert st[nr["en"]] == "expired" and st[nr["zh"]] == "authorized"
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'account.authorization_expired'")).scalar() == 1


def test_emergency_stop_pauses_ai_jobs_only(sf):
    newsroom(sf)
    with sf() as s:
        enqueue(s, "eic_evaluate", {"event_id": 999}, dedupe_key="eic:999", now=NOW)
        settings_store.put(s, "emergency_stop", {"active": True})
        s.commit()
    assert run_pending(sf, Deps(feed=FakeFeed().client, now=lambda: NOW), AI_KINDS) == []
    with sf() as s:
        assert s.execute(text("SELECT status FROM job")).scalar() == "queued"


def test_near_liquidation_bands_and_no_duplicates(sf):
    feed = FakeFeed()
    feed.contexts[WHALE] = context(positions=[position(szi=30, mark=100000, liq=98000)])
    row = {"trader_key": WHALE, "venue": "hyperliquid", "display_name": None, "coin": "BTC", "szi": "30",
           "notional_usd": "3000000", "mark_px": "100000", "entry_px": "101000", "leverage": "40", "liq_px": "98000",
           "upl": "-30000", "opened_at_source": "window", "first_seen_at": NOW.isoformat(),
           "observed_at": NOW.isoformat(), "account_value": "500000", "liq_dist": "0.02"}
    feed.at_risk_rows = [row]
    with feed.client() as c:
        assert poll_at_risk(sf, c, NOW)["created"] == 1
        assert poll_at_risk(sf, c, NOW)["created"] == 0  # same band again: nothing new
        feed.at_risk_rows = [{**row, "liq_dist": "0.004", "liq_px": "99600"}]
        assert poll_at_risk(sf, c, NOW)["created"] == 1  # a deeper band is news
    pipeline(sf, feed, NOW)
    with sf() as s:
        rows = s.execute(text("SELECT escalation_level, gate_status FROM news_event ORDER BY id")).all()
        assert [r[0] for r in rows] == [1, 3]  # 2% is inside the 3% band only; 0.4% is inside all three
        ep = s.execute(text("SELECT start_kind, start_precision FROM position_episode")).one()
        assert ep == ("reconstructed", "unknown")


def test_stuck_evaluations_return_to_the_pool(sf):
    from tests.helpers import candidate_event
    newsroom(sf)
    eid = candidate_event(sf)
    with sf() as s:
        s.execute(text("UPDATE job SET status = 'done' WHERE kind = 'eic_evaluate'"))
        s.execute(text("UPDATE news_event SET state = 'evaluating', state_changed_at = :t WHERE id = :e"),
                  {"t": NOW - timedelta(hours=1), "e": eid})
        s.commit()
    assert maintenance.run(sf, NOW)["unstuck_events"] == 1
    with sf() as s:
        assert s.execute(text("SELECT state FROM news_event")).scalar() == "candidate"


def test_old_observations_build_state_but_are_not_news(sf):
    from tests.fakes import T0, ev
    feed = FakeFeed()
    # a day-old open, then a fresh add on the same position
    feed.events = [ev(1, "open", 0, 300, 100000, observed=T0 - timedelta(days=1)),
                   ev(2, "increase", 300, 320, 100000, observed=T0 + timedelta(hours=2))]
    feed.contexts[WHALE] = context(positions=[position(szi=320)])
    r = pipeline(sf, feed)
    assert r["detect"]["too_old"] == 1 and r["detect"]["events"] == 2
    with sf() as s:
        assert s.execute(text("SELECT event_class FROM news_event")).scalars().all() == ["whale_add"]
        assert s.execute(text("SELECT start_kind FROM position_episode")).scalar() == "open"


def test_untimely_uncovered_events_expire(sf):
    from tests.fakes import T0, ev
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)], fresh=False)
    pipeline(sf, feed)  # blocked: the trader is stale
    assert maintenance.run(sf, T0 + timedelta(hours=12))["expired_events"] == 0
    assert maintenance.run(sf, T0 + timedelta(days=3))["expired_events"] == 1
    with sf() as s:
        assert s.execute(text("SELECT state FROM news_event")).scalar() == "archived"
        assert s.execute(text("SELECT reason FROM event_state_history ORDER BY id DESC LIMIT 1")).scalar().startswith("expired")


def test_ai_jobs_wait_for_a_model_without_dying(sf):
    from tests.helpers import candidate_event
    newsroom(sf, model=False)
    candidate_event(sf)
    with sf() as s:
        jid = s.execute(text("SELECT id FROM job WHERE kind = 'eic_evaluate'")).scalar()
    assert jid is not None
    for _ in range(6):
        run_pending(sf, Deps(feed=FakeFeed().client, now=lambda: NOW), AI_KINDS)
    with sf() as s:
        st = s.execute(text("SELECT status, attempts, last_error FROM job WHERE id = :j"), {"j": jid}).one()
    assert st[0] == "queued" and st[1] == 0 and "AIUnavailable" in st[2]


def test_stories_go_dormant_and_close(sf):
    from tests.helpers import candidate_event
    newsroom(sf)
    candidate_event(sf)
    with sf() as s:
        s.execute(text("UPDATE news_story SET last_event_at = :t"), {"t": NOW - timedelta(days=8)})
        s.commit()
    maintenance.run(sf, NOW)
    with sf() as s:
        assert s.execute(text("SELECT status FROM news_story")).scalar() == "dormant"  # the position is still open
        s.execute(text("UPDATE position_episode SET status = 'closed'"))
        s.commit()
    maintenance.run(sf, NOW)
    with sf() as s:
        assert s.execute(text("SELECT status FROM news_story")).scalar() == "closed"
