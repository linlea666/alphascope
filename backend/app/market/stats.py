"""Aggregates over the observed sample (never "the market"): shared by the API and the hourly snapshots.

Counting rules (docs/12):
- a position is current while its trader's poll is fresh (position_watch.stale_after > now);
- per coin, a trader counts once, long or short by their net position on that coin (OKX hedged legs are netted
  at collection; the two venues are separate identities and never merged);
- across all coins, a trader is net long / short by the sign of their summed signed notional;
- notional = |net size| x mark at the trader's last poll; net exposure = long - short.
"""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

Venue = Literal["all", "hyperliquid", "okx"]
COHORTS = {
    "all": "true",
    "watchlist": "{col} IN (SELECT wallet_id FROM watch_item)",
    "timing": "{col} IN (SELECT wallet_id FROM timing_score WHERE status = 'scored' AND q_value < 0.1)",
    "okx_board": "{col} IN (SELECT wallet_id FROM okx_presence WHERE time_window = '7d' AND currently_seen)",
}
Cohort = Literal["all", "watchlist", "timing", "okx_board"]
MIN_TRADERS_SNAPSHOT = 3  # coins held by fewer traders are not snapshotted (they would only add noise)


def scope(venue: str, cohort: str, col: str = "w.wallet_id", venue_col: str = "w.venue") -> str:
    """SQL condition for a venue + cohort, on a position_watch alias."""
    cond = COHORTS[cohort].format(col=col)
    if venue != "all":
        cond += f" AND {venue_col} = '{'okx' if venue == 'okx' else 'hyperliquid'}'"
    return cond


def fresh_cte(venue: str, cohort: str, min_lev: int = 0) -> str:
    lev = f" AND coalesce(p.leverage, 0) >= {int(min_lev)}" if min_lev else ""
    return f"""
        fresh AS (SELECT p.* FROM trader_position p JOIN position_watch w ON w.wallet_id = p.wallet_id
                  WHERE w.stale_after > :now AND {scope(venue, cohort)}{lev})"""


def coverage(s: Session, venue: str, cohort: str, now: datetime) -> dict:
    r = s.execute(text(f"""
        SELECT count(*) AS watched,
               count(*) FILTER (WHERE w.stale_after > :now) AS fresh,
               count(*) FILTER (WHERE w.stale_after > :now AND w.n_positions > 0) AS positioned,
               max(w.last_observed_at) AS latest, min(w.last_observed_at) FILTER (WHERE w.stale_after > :now) AS oldest
        FROM position_watch w WHERE {scope(venue, cohort)}"""), {"now": now}).mappings().one()
    return dict(r)


COIN_COLUMNS = """
    count(*) FILTER (WHERE szi > 0) AS long_traders, count(*) FILTER (WHERE szi < 0) AS short_traders,
    coalesce(sum(notional_usd) FILTER (WHERE szi > 0), 0) AS long_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE szi < 0), 0) AS short_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE szi > 0 AND leverage >= 10), 0) AS lev10_long_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE szi < 0 AND leverage >= 10), 0) AS lev10_short_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE szi > 0 AND leverage >= 20), 0) AS lev20_long_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE szi < 0 AND leverage >= 20), 0) AS lev20_short_ntl,
    coalesce(sum(notional_usd) FILTER (WHERE liq_px IS NOT NULL), 0) AS liq_known_ntl"""


def coin_rows(s: Session, venue: str, cohort: str, now: datetime, min_traders: int = 1) -> list[dict]:
    rows = s.execute(text(f"""
        WITH {fresh_cte(venue, cohort)}
        SELECT coin, count(*) AS traders, {COIN_COLUMNS}, avg(mark_px) AS mark_px,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY leverage) AS median_leverage,
               min((mark_px - liq_px) / mark_px) FILTER (WHERE szi > 0 AND liq_px > 0 AND liq_px < mark_px)
                 AS near_long_liq_pct,
               min((liq_px - mark_px) / mark_px) FILTER (WHERE szi < 0 AND liq_px > mark_px) AS near_short_liq_pct
        FROM fresh GROUP BY coin HAVING count(*) >= :min ORDER BY sum(notional_usd) DESC"""),
        {"now": now, "min": min_traders}).mappings().all()
    return [dict(r) for r in rows]


def totals(s: Session, venue: str, cohort: str, now: datetime) -> dict:
    """All coins: per-position sums, and traders net long / short by their summed signed notional."""
    r = s.execute(text(f"""
        WITH {fresh_cte(venue, cohort)},
        per_trader AS (SELECT wallet_id, sum(sign(szi) * notional_usd) AS net FROM fresh GROUP BY wallet_id)
        SELECT (SELECT count(*) FROM per_trader) AS traders,
               (SELECT count(*) FROM per_trader WHERE net > 0) AS long_traders,
               (SELECT count(*) FROM per_trader WHERE net < 0) AS short_traders,
               (SELECT count(*) FROM fresh) AS positions,
               {COIN_COLUMNS.replace("AS long_traders", "AS long_positions")
                            .replace("AS short_traders", "AS short_positions")}
        FROM fresh"""), {"now": now}).mappings().one()
    return dict(r)


def event_counts(s: Session, venue: str, cohort: str, since: datetime, until: datetime,
                 coin: str | None = None) -> dict[str, dict[str, int]]:
    """Per coin: opens (open + flip) and closes between since and until; first sightings are never opens."""
    cond = scope(venue, cohort, col="e.wallet_id", venue_col="e.venue")
    rows = s.execute(text(f"""
        SELECT e.coin, count(*) FILTER (WHERE e.kind IN ('open', 'flip')) AS opens,
               count(*) FILTER (WHERE e.kind = 'close') AS closes
        FROM market_event e
        WHERE e.event_time >= :a AND e.event_time < :b AND {cond} {"AND e.coin = :coin" if coin else ""}
        GROUP BY e.coin"""), {"a": since, "b": until, "coin": coin}).all()
    return {c: {"opens": o, "closes": cl} for c, o, cl in rows}


def snapshot_hour(s: Session, now: datetime) -> int:
    """Write this hour's market_snapshot rows for every venue and cohort (idempotent: re-running updates them)."""
    bucket = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for venue in ("all", "hyperliquid", "okx"):
        for cohort in COHORTS:
            cov = coverage(s, venue, cohort, now)
            if not cov["watched"]:
                continue
            ev = event_counts(s, venue, cohort, now - timedelta(hours=1), now)  # the hour up to this snapshot
            t = totals(s, venue, cohort, now)
            rows = [("*", t, {"opens": sum(e["opens"] for e in ev.values()),
                              "closes": sum(e["closes"] for e in ev.values())})]
            rows += [(r["coin"], r, ev.get(r["coin"], {"opens": 0, "closes": 0}))
                     for r in coin_rows(s, venue, cohort, now, MIN_TRADERS_SNAPSHOT)]
            for coin, r, e in rows:
                out.append({"b": bucket, "v": venue, "c": cohort, "coin": coin, "obs": cov["fresh"],
                            **{k: r[k] for k in ("long_traders", "short_traders", "long_ntl", "short_ntl",
                                                 "lev10_long_ntl", "lev10_short_ntl", "lev20_long_ntl",
                                                 "lev20_short_ntl", "liq_known_ntl")}, **e})
    if out:
        s.execute(text("""
            INSERT INTO market_snapshot (bucket_ts, venue, cohort, coin, observed, long_traders, short_traders,
              long_ntl, short_ntl, lev10_long_ntl, lev10_short_ntl, lev20_long_ntl, lev20_short_ntl, liq_known_ntl,
              opens, closes)
            VALUES (:b, :v, :c, :coin, :obs, :long_traders, :short_traders, :long_ntl, :short_ntl, :lev10_long_ntl,
              :lev10_short_ntl, :lev20_long_ntl, :lev20_short_ntl, :liq_known_ntl, :opens, :closes)
            ON CONFLICT (bucket_ts, venue, cohort, coin) DO UPDATE SET observed = EXCLUDED.observed,
              long_traders = EXCLUDED.long_traders, short_traders = EXCLUDED.short_traders,
              long_ntl = EXCLUDED.long_ntl, short_ntl = EXCLUDED.short_ntl, lev10_long_ntl = EXCLUDED.lev10_long_ntl,
              lev10_short_ntl = EXCLUDED.lev10_short_ntl, lev20_long_ntl = EXCLUDED.lev20_long_ntl,
              lev20_short_ntl = EXCLUDED.lev20_short_ntl, liq_known_ntl = EXCLUDED.liq_known_ntl,
              opens = EXCLUDED.opens, closes = EXCLUDED.closes"""), out)
    return len(out)
