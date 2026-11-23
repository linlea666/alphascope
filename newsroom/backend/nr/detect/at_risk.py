"""Near-liquidation events from the feed's at-risk list (fresh positions only, venue-reported liquidation prices)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.detect.rules import Candidate
from nr.events import service
from nr.feed.client import FeedClient, FeedError, FeedUnavailable
from nr.feed.sync import upsert_traders
from nr.jobs.queue import enqueue


def poll_at_risk(sf: sessionmaker, client: FeedClient, now: datetime) -> dict:
    with sf() as s:
        cfg = settings_store.get(s, "detect")
    bands = sorted((Decimal(str(b)) for b in cfg["near_liq_bands"]), reverse=True)
    try:
        data = client.at_risk(max_dist=float(bands[0]), min_notional=cfg["near_liq_min_usd"])
    except (FeedUnavailable, FeedError) as exc:
        return {"error": str(exc)}
    out = {"positions": len(data["rows"]), "created": 0}
    with sf() as s:
        traders = upsert_traders(s, data["rows"])
        for r in data["rows"]:
            tid, szi = traders[r["trader_key"]], Decimal(r["szi"])
            direction = "long" if szi > 0 else "short"
            ep = s.execute(text("""SELECT id, direction FROM position_episode WHERE trader_id = :t AND coin = :c
                                   AND status = 'open'"""), {"t": tid, "c": r["coin"]}).mappings().first()
            if ep is None or ep["direction"] != direction:
                if ep is not None:  # the feed has moved on; the event stream will close it
                    continue
                ep_id = s.execute(text("""
                    INSERT INTO position_episode (trader_id, venue, coin, direction, start_kind, start_precision, start_to,
                      peak_abs_szi, peak_notional, last_szi, last_mark_px, last_entry_px)
                    VALUES (:t, :v, :c, :d, 'reconstructed', 'unknown', :st, :abs, :ntl, :szi, :mark, :entry)
                    RETURNING id"""),
                    {"t": tid, "v": r["venue"], "c": r["coin"], "d": direction, "st": r["first_seen_at"],
                     "abs": abs(szi), "ntl": r["notional_usd"], "szi": szi, "mark": r["mark_px"],
                     "entry": r["entry_px"]}).scalar_one()
            else:
                ep_id = ep["id"]
            dist = Decimal(r["liq_dist"])
            level = sum(1 for b in bands if dist <= b)
            c = Candidate("near_liquidation", level, tid, r["trader_key"], ep_id, r["venue"], r["coin"], direction, None)
            res = service.upsert(s, c)
            if res and res[1] == "created":
                out["created"] += 1
                enqueue(s, "build_facts", {"event_id": res[0]}, dedupe_key=f"facts:{res[0]}", priority=40, now=now)
        s.commit()
    return out
