import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from nr.feed.contract import REQUIRED
from nr.feed.sync import sync_events
from tests.fakes import T0, FakeFeed, ev
from tests.pipeline import pipeline, setup_source

ALPHASCOPE_CONTRACT = Path(__file__).resolve().parents[3] / "backend" / "tests" / "fixtures" / "feed_v1_contract.json"


def test_contract_matches_alphascope_fixture():
    f = json.loads(ALPHASCOPE_CONTRACT.read_text())
    f.pop("_comment")
    assert f == REQUIRED


def test_repeated_polling_and_replay_create_no_duplicates(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000), ev(2, "increase", 30, 40, 100000)]
    r1 = pipeline(sf, feed)
    with sf() as s:
        before = s.execute(text("SELECT count(*) FROM news_event")).scalar()
    assert before == 2  # an open and a $1M add
    # the same pages again, and a replay from an old cursor
    r2 = pipeline(sf, feed)
    with sf() as s:
        s.execute(text("INSERT INTO ingestion_checkpoint_history (source_id, stream, after_id, recorded_at) "
                       "VALUES (1, 'events', 0, :t)"), {"t": T0 - timedelta(hours=5)})
        s.commit()
        with feed.client() as c:
            rep = sync_events(sf, c, 1, T0 + timedelta(hours=3), mode="replay")
        assert rep["inserted"] == 0 and rep["fetched"] == 2
        assert s.execute(text("SELECT count(*) FROM source_event")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM news_event")).scalar() == before
        assert s.execute(text("SELECT after_id FROM ingestion_checkpoint")).scalar() == 2
    assert r1["sync"]["inserted"] == 2 and r2["sync"]["inserted"] == 0


def test_outage_is_recorded_and_cursor_kept(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 30, 100000)]
    sid = setup_source(sf)
    feed.down = True
    with feed.client() as c:
        r = sync_events(sf, c, sid, T0)
    assert "error" in r
    with sf() as s:
        assert s.execute(text("SELECT status FROM sync_run")).scalar() == "failed"
        assert s.execute(text("SELECT last_error IS NOT NULL FROM source_connection")).scalar()
        assert s.execute(text("SELECT after_id FROM ingestion_checkpoint")).scalar() == 0
