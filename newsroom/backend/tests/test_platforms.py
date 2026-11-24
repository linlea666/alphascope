from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from nr.articles.versions import Draft, save_version
from nr.columns.run import build_column
from nr.desk import assignment
from nr.desk.eic import evaluate_event
from nr.publish import service as pub
from tests.fakes import FakeFeed, FakeProvider, context, ev, health, iso, position
from tests.helpers import NOW, eic_output, newsroom
from tests.pipeline import pipeline
from tests.test_columns import board_doc, feed_with_health

OKX = "okx:1234567890"


def accounts(sf):
    with sf() as s:
        return {r[0]: r[1] for r in s.execute(text("SELECT handle, id FROM editorial_account"))}


def test_okx_material_only_goes_to_okx_square(sf):
    nr = newsroom(sf)
    acc = accounts(sf)
    with sf() as s:
        assert set(s.execute(text("SELECT platform || ':' || array_to_string(venues, ',') FROM editorial_account"))
                   .scalars()) == {"x:hyperliquid", "okx_square:okx", "binance_square:binance"}
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000, trader=OKX, venue="okx", precision="exact")]
    feed.contexts[OKX] = context(trader=OKX, venue="okx", positions=[position(szi=30)], synced=False,
                                 styles=[{"style": "trend", "version": "s2", "status": "verified", "confidence": "1",
                                          "n": 40, "oos": None, "computed_at": iso(NOW)}])
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid, state = s.execute(text("SELECT id, state FROM news_event")).one()
        assert state == "candidate"
        with pytest.raises(HTTPException) as e:  # an X account cannot carry OKX material
            assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        assert "venues" in e.value.detail
        # OKX Square tells a plain open only through a star (docs/16): nobody else carries it, so no EIC call
        with pytest.raises(HTTPException) as e:
            assignment.create(s, eid, acc["alphascope_okx_square"], source="editor", user_id=nr["chief"])
        assert "star" in e.value.detail
    prov0 = FakeProvider()
    assert evaluate_event(sf, eid, NOW, transport=prov0)["skipped"] and prov0.requests == []
    with sf() as s:  # once the trader is a star, OKX Square takes the move
        s.execute(text("INSERT INTO star_trader (trader_key, venue, name) VALUES (:k, 'okx', '测试明星')"), {"k": OKX})
        s.execute(text("UPDATE news_event SET state = 'candidate'"))
        s.commit()
    prov = FakeProvider(eic_output(eid, [(acc["alphascope_okx_square"], "带单员视角")]))
    evaluate_event(sf, eid, NOW, transport=prov)
    assert '"alphascope_okx_square"' in prov.requests[0]["user"] and "alphascope_cn" not in prov.requests[0]["user"]
    with sf() as s:
        assert s.execute(text("SELECT agent_key FROM assignment WHERE source = 'eic_ai'")).scalar() == "reporter_star"
    # with the OKX Square account paused the editor-in-chief is not asked at all
    with sf() as s:
        s.execute(text("UPDATE editorial_account SET status = 'paused' WHERE platform = 'okx_square'"))
        s.execute(text("UPDATE news_event SET state = 'candidate'"))
        s.commit()
    prov2 = FakeProvider()
    assert evaluate_event(sf, eid, NOW, transport=prov2)["skipped"] and prov2.requests == []


def test_okx_columns_and_outages_stay_separate(sf):
    nr = newsroom(sf)
    acc = accounts(sf)
    feed = feed_with_health()
    feed.health_doc = health(hl="ok", okx="stale", head=1)
    feed.research["/copy/board"] = board_doc()
    ladder = {"schema": "feed.v1", "as_of": iso(NOW), "venue": "hyperliquid", "coin": "BTC", "positions": 40,
              "coverage": {"latest": iso(NOW - timedelta(minutes=5))}, "ref": {"px": 100000, "observed_at": iso(NOW)},
              "liquidations": {"long": {"known_share": 0.8, "ladder": [{"move": -5, "notional": 2500000, "positions": 4}]},
                               "short": {"known_share": 0.7, "ladder": [{"move": 5, "notional": 900000, "positions": 2}]}}}
    feed.research["/market/structure"] = ladder
    pipeline(sf, feed, NOW)
    with feed.client() as c:
        okx_issue = build_column(sf, c, "copy_vs_board", NOW)["issues"][0]
        hl_issue = build_column(sf, c, "liq_ladder", NOW, {"coins": ["BTC"]})["issues"][0]
    assert okx_issue["gate"] == "blocked" and hl_issue["gate"] == "pass"  # an OKX outage never blocks Hyperliquid
    with sf() as s:
        s.execute(text("UPDATE news_event SET state = 'candidate', gate_status = 'pass' WHERE id = :e"),
                  {"e": okx_issue["event_id"]})
        with pytest.raises(HTTPException):
            assignment.create(s, okx_issue["event_id"], nr["en"], source="editor", user_id=nr["chief"])
        assert assignment.create(s, okx_issue["event_id"], acc["alphascope_okx_square"], source="editor",
                                 user_id=nr["chief"])
        assert assignment.create(s, hl_issue["event_id"], nr["zh"], source="editor", user_id=nr["chief"])


def test_platform_urls_and_lengths(sf):
    nr = newsroom(sf)
    acc = accounts(sf)
    feed = FakeFeed()
    trader = "okx:5555555555"
    feed.events = [ev(1, "open", 0, 300, 100000, trader=trader, venue="okx", precision="exact")]
    feed.contexts[trader] = context(trader=trader, venue="okx", positions=[position(szi=300)], synced=False)
    pipeline(sf, feed, NOW)
    long_zh = "一个 OKX 带单员开出约3000万美元 BTC 多单。" + "数据来自 AlphaScope 观测样本。" * 12
    with sf() as s:
        eid = s.execute(text("SELECT id FROM news_event")).scalar()
        s.execute(text("INSERT INTO star_trader (trader_key, venue, name) VALUES (:k, 'okx', '测试明星')"), {"k": trader})
        s.execute(text("UPDATE editorial_account SET status = 'active', authorization_status = 'authorized'"))
        aid = assignment.create(s, eid, acc["alphascope_okx_square"], source="editor", status="accepted",
                                user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        v = save_version(s, art, Draft(body=long_zh, facts_used=["position.notional_usd"]), origin="human",
                         user_id=nr["chief"])
        assert v["guard"]["length_limit"] == 1000 and not [f for f in v["guard"]["findings"] if f["code"] == "TOO_LONG"]
        pub.submit(s, art, nr["chief"])
        pub.approve(s, art, nr["chief"])
        with pytest.raises(HTTPException):
            pub.mark_published(s, art, nr["chief"], "https://x.com/alphascope_cn/status/186000000000000", NOW, NOW)
        pid = pub.mark_published(s, art, nr["chief"], "https://www.okx.com/zh-hans/square/post/9876543210123", NOW, NOW)
        assert s.execute(text("SELECT x_post_id FROM publication_record WHERE id = :p"), {"p": pid}).scalar() == \
            "9876543210123"
    # the same text on X is too long
    from nr.guard.run import run
    r = run({"body": long_zh}, {"event": {"class": "x"}, "facts": [], "constraints": []}, facts_used=[],
            version_packet_id=1, current_packet_id=1, long_post_allowed=False, allow=[], forbidden=[], duplicate=None,
            dup_block=0.45, dup_warn=0.3, platform={"limit": 280, "weighted": True})
    assert any(f["code"] == "TOO_LONG" for f in r["findings"])
