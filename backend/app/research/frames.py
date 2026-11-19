"""Immutable hourly samples. Cohorts, zones and source records travel with the observation."""

from collections import Counter
from datetime import datetime, timedelta
from math import log, log10
from statistics import pstdev

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.market import structure
from app.models import ResearchFrame, ResearchNotice
from app.research.timing import TIMING_VERSION

VERSION = "frame-v1"
VENUES = ("hyperliquid", "okx")


def freeze_accounts(s, coin, venue, observed):
    rows = s.execute(
        text("""
        WITH timing AS (
          SELECT DISTINCT ON(wallet_id) wallet_id, q_value, edge_shrunk, n_scored, as_of
          FROM timing_score WHERE coin_group=:grp AND time_window='all' AND version=:v
          AND status='scored' AND as_of<=:t ORDER BY wallet_id,q_value NULLS LAST,n_scored DESC,scale
        ), verified AS (
          SELECT wallet_id,bool_or(status='verified') AS verified FROM style_tag
          WHERE computed_at<=:t GROUP BY wallet_id
        )
        SELECT p.wallet_id,w.address,p.szi,p.notional_usd,p.leverage,p.mark_px,p.liq_px,p.entry_px,p.margin_mode,
          p.observed_at,h.account_value,h.tier, t.q_value,t.edge_shrunk,t.n_scored,t.as_of AS score_at,
          coalesce(v.verified,false) verified
        FROM trader_position p JOIN position_watch h USING(wallet_id) JOIN wallet w ON w.id=p.wallet_id
        LEFT JOIN timing t USING(wallet_id) LEFT JOIN verified v USING(wallet_id)
        WHERE p.venue=:venue AND p.coin=:coin AND h.stale_after>:t AND p.observed_at<=:t ORDER BY p.wallet_id
    """),
        dict(grp="c:" + coin, t=observed, coin=coin, v=TIMING_VERSION, venue=venue),
    ).mappings()
    accounts = [jsonable_encoder(dict(r)) for r in rows]
    for r in accounts:
        qualified = r["q_value"] is not None and r["q_value"] < 0.1 and (r["edge_shrunk"] or 0) > 0
        r["cohort"] = "specialist" if qualified and r["verified"] else "candidate" if qualified else "ordinary"
    return accounts


def group_stats(group):
    gross = sum(r["notional_usd"] for r in group)
    long = sum(r["notional_usd"] for r in group if r["szi"] > 0)
    return {
        "n": len(group),
        "long_share": sum(r["szi"] > 0 for r in group) / len(group) if group else None,
        "long_notional_share": long / gross if gross else None,
        "long_usd": long,
        "short_usd": gross - long,
    }


def matched_groups(accounts):
    elites = [r for r in accounts if r["cohort"] == "specialist" and (r["account_value"] or 0) > 0]
    ordinary = [r for r in accounts if r["cohort"] == "ordinary" and (r["account_value"] or 0) > 0]

    # Same exchange (frame), activity cadence and equity decade; match without replacement.
    def stratum(r):
        return r["tier"], int(log10(r["account_value"]) // 1)

    chosen, controls = [], []
    pool = {}
    for r in ordinary:
        pool.setdefault(stratum(r), []).append(r)
    for r in sorted(elites, key=lambda x: x["wallet_id"]):
        peers = pool.get(stratum(r), [])
        if peers:
            i = min(
                range(len(peers)),
                key=lambda j: (abs(log10(peers[j]["account_value"] / r["account_value"])), peers[j]["wallet_id"]),
            )
            ctrl = peers.pop(i)
            r["matched_to"] = ctrl["wallet_id"]
            chosen.append(r)
            controls.append(ctrl)
    return group_stats(chosen), group_stats(controls)


def changes(current, prior):
    """Change in observed sizes of accounts present in both frames, excluding sample entry and exit."""
    before = {r["wallet_id"]: r["szi"] for r in prior}
    deltas = [(r, r["szi"] - before[r["wallet_id"]]) for r in current if r["wallet_id"] in before]
    ds = [d for _, d in deltas]
    adds = [max(r["szi"], 0) - max(before[r["wallet_id"]], 0) for r, _ in deltas]
    shorts = [max(-r["szi"], 0) - max(-before[r["wallet_id"]], 0) for r, _ in deltas]
    gross = sum(abs(x) for x in ds)
    long_adds = [max(x, 0) for x in adds]
    total_adds = sum(long_adds)
    return {
        "common_n": len(ds),
        "net_units": sum(ds) if ds else None,
        "long_change": sum(adds) if ds else None,
        "short_change": sum(shorts) if ds else None,
        "long_add_breadth": sum(x > 0 for x in adds) / len(ds) if ds else None,
        "long_add_top1_share": max(long_adds) / total_adds if total_adds else None,
        "buy_breadth": sum(x > 0 for x in ds) / len(ds) if ds else None,
        "largest_share": max(map(abs, ds)) / gross if gross else None,
        "contributors": [
            {"address": r["address"], "delta": d} for r, d in sorted(deltas, key=lambda p: -abs(p[1]))[:10] if d
        ],
    }


def percentile(value, history):
    xs = [x for x in history if x is not None]
    return {
        "n": len(xs),
        "rank": (sum(x < value for x in xs) + 0.5 * sum(x == value for x in xs)) / len(xs)
        if value is not None and len(xs) >= 24
        else None,
    }


def capture(s, coin, venue, now, context):
    bucket = now.replace(minute=0, second=0, microsecond=0)
    previous = s.scalar(
        select(ResearchFrame).where(
            ResearchFrame.coin == coin,
            ResearchFrame.venue == venue,
            ResearchFrame.bucket_ts == bucket - timedelta(hours=1),
            ResearchFrame.version == VERSION,
        )
    )
    accounts = freeze_accounts(s, coin, venue, now)
    cov = (
        s.execute(
            text("""SELECT count(*) watched,count(*) FILTER(WHERE stale_after>:t AND last_observed_at<=:t) fresh
        FROM position_watch WHERE venue=:v AND added_at<=:t"""),
            dict(t=now, v=venue),
        )
        .mappings()
        .one()
    )
    smart, control = matched_groups(accounts)
    fresh_marks = [r for r in accounts if r["mark_px"]]
    latest = max(fresh_marks, key=lambda r: r["observed_at"]) if fresh_marks else None
    ref = context.get("fields", {}).get("mark") or (latest["mark_px"] if latest else None)
    sizes = sorted((r["notional_usd"] for r in accounts), reverse=True)
    gross = sum(sizes)
    m = {
        **group_stats(accounts),
        **dict(cov),
        "ref": ref,
        "smart": smart,
        "control": control,
        "ref_kind": "venue_context" if context.get("fields", {}).get("mark") else "wallet_observation",
        "cohorts": dict(Counter(r["cohort"] for r in accounts)),
        "top5_share": sum(sizes[:5]) / gross if gross else None,
        "top10_share": sum(sizes[:10]) / gross if gross else None,
        "leverage_usd": {
            str(x): sum(r["notional_usd"] for r in accounts if (r["leverage"] or 0) >= x) for x in (10, 20, 30)
        },
        "delta": changes(accounts, previous.accounts if previous else []),
    }
    m["divergence"] = smart["long_share"] - control["long_share"] if smart["n"] else None
    m["zones"] = {}
    if ref:
        positions = [
            structure.Row(
                r["wallet_id"],
                1 if r["szi"] > 0 else -1,
                r["notional_usd"],
                r["entry_px"],
                r["liq_px"],
                r["leverage"],
                r["margin_mode"],
            )
            for r in accounts
        ]
        for side, sign in (("long", 1), ("short", -1)):
            subset = [p for p in positions if p.side == sign]
            m["zones"][side] = structure.liq_side(subset, ref, 0.3, 60, 0)["zones"]
    m["zone_followup"] = zone_followup(s, previous, now) if previous else []
    history_rows = list(
        s.execute(
            select(ResearchFrame.bucket_ts, ResearchFrame.metrics)
            .where(
                ResearchFrame.coin == coin,
                ResearchFrame.venue == venue,
                ResearchFrame.available_at <= now,
                ResearchFrame.bucket_ts < bucket,
                ResearchFrame.version == VERSION,
            )
            .order_by(ResearchFrame.bucket_ts.desc())
            .limit(720)
        )
    )
    history = [m for _, m in history_rows]
    refs = [(t, x.get("ref")) for t, x in reversed(history_rows) if t >= bucket - timedelta(hours=24)] + [
        (bucket, ref)
    ]
    moves = [
        log(b / a)
        for (ta, a), (tb, b) in zip(refs, refs[1:])
        if tb - ta == timedelta(hours=1) and a and b and a > 0 and b > 0
    ]
    m["windows"] = {}
    for hours in (1, 4, 12, 24, 72, 168):
        earlier = (
            previous
            if hours == 1
            else s.scalar(
                select(ResearchFrame).where(
                    ResearchFrame.coin == coin,
                    ResearchFrame.venue == venue,
                    ResearchFrame.version == VERSION,
                    ResearchFrame.bucket_ts == bucket - timedelta(hours=hours),
                    ResearchFrame.available_at <= now,
                )
            )
        )
        m["windows"][str(hours)] = (
            {
                "from_frame": earlier.id,
                "observed_from": earlier.observed_at.isoformat(),
                **changes(accounts, earlier.accounts),
            }
            if earlier
            else None
        )
    vol = pstdev(moves) if len(moves) >= 12 else None
    distances = [
        abs(r["liq_px"] / ref - 1)
        for r in accounts
        if r["liq_px"] and ref and ((r["szi"] > 0 and r["liq_px"] < ref) or (r["szi"] < 0 and r["liq_px"] > ref))
    ]
    m["nearest_distance"] = min(distances, default=None)
    m["hourly_volatility"] = vol
    m["distance_in_hourly_sigma"] = min(distances) / vol if distances and vol else None
    m["high_leverage_share"] = m["leverage_usd"]["20"] / gross if gross else None
    book = context.get("fields", {}).get("depth")
    m["depth"] = book["bid"] + book["ask"] if book else None
    m["net_change"] = m["delta"]["net_units"]
    m["cohort_baselines"] = {}
    for group in ("specialist", "candidate", "ordinary"):
        values = group_stats([r for r in accounts if r["cohort"] == group])
        values["long_rank"] = percentile(
            values["long_share"], [x.get("cohort_baselines", {}).get(group, {}).get("long_share") for x in history]
        )
        m["cohort_baselines"][group] = values
    m["ranks"] = {
        k: percentile(m.get(k), [x.get(k) for x in history])
        for k in (
            "long_share",
            "divergence",
            "top5_share",
            "high_leverage_share",
            "nearest_distance",
            "hourly_volatility",
            "depth",
            "net_change",
        )
    }
    result = s.execute(
        insert(ResearchFrame)
        .values(
            coin=coin,
            venue=venue,
            bucket_ts=bucket,
            observed_at=now,
            version=VERSION,
            accounts=accounts,
            metrics=m,
            context=context,
        )
        .on_conflict_do_nothing()
        .returning(ResearchFrame.id)
    ).scalar()
    if result is not None:
        for k, stat in m["ranks"].items():
            rank = stat["rank"]
            if rank is not None and (rank >= 0.95 or rank <= 0.05):
                contributors = []
                if k == "net_change":
                    contributors = m["delta"]["contributors"]
                elif k not in ("depth", "hourly_volatility"):
                    eligible = accounts
                    if k == "divergence":
                        matched = {a["matched_to"] for a in accounts if "matched_to" in a}
                        eligible = [a for a in accounts if "matched_to" in a or a["wallet_id"] in matched]
                    elif k == "high_leverage_share":
                        eligible = [a for a in accounts if (a["leverage"] or 0) >= 20]
                    if k == "nearest_distance":
                        eligible = sorted(
                            (a for a in accounts if a["liq_px"] and ref and (a["liq_px"] - ref) * a["szi"] < 0),
                            key=lambda a: abs(a["liq_px"] / ref - 1),
                        )
                    else:
                        eligible = sorted(eligible, key=lambda a: -a["notional_usd"])
                    contributors = [
                        {
                            key: a.get(key)
                            for key in ("address", "szi", "notional_usd", "leverage", "liq_px", "cohort")
                        }
                        for a in eligible[:10]
                    ]
                evidence = {
                    "rank": rank,
                    "history_n": stat["n"],
                    "value": m[k],
                    "cohort": "matched" if k == "divergence" else "all",
                    "contributors": contributors,
                    "reference_px": ref,
                    "n": len(accounts),
                    "counterevidence": {
                        "largest_share": m["delta"]["largest_share"],
                        "common_n": m["delta"]["common_n"],
                    },
                    "track": "same_venue_mark_24h",
                }
                s.add(ResearchNotice(frame_id=result, kind=k, evidence=evidence, due_at=now + timedelta(hours=24)))
    return result


def coverage(s, coin, now):
    out = []
    for v in VENUES:
        r = (
            s.execute(
                text("""SELECT min(bucket_ts) first,max(bucket_ts) last,max(available_at) updated,count(*) n
            FROM research_frame WHERE coin=:c AND venue=:v AND version=:ver AND available_at<=:t"""),
                dict(c=coin, v=v, ver=VERSION, t=now),
            )
            .mappings()
            .one()
        )
        last_due = (now - timedelta(minutes=55)).replace(minute=0, second=0, microsecond=0)
        expected = int((max(last_due, r["last"]) - r["first"]).total_seconds() // 3600) + 1 if r["first"] else 0
        out.append({"venue": v, **dict(r), "missing_hours": expected - r["n"] if expected else None})
    return out


def zone_followup(s, prior, now):
    """OHLC touch, position changes and venue liquidation evidence use distinct clocks and denominators."""
    venue, coin, start = prior.venue, prior.coin, prior.available_at
    iv, step = ("1m", 1) if venue == "hyperliquid" else ("5m", 5)
    params = dict(v=venue, c=coin, a=start, b=now, iv=iv, step=step)
    bars = s.execute(
        text("""SELECT t,h,l,c FROM market_candle WHERE source=:v AND coin=:c AND interval=:iv
        AND t>=:a AND t+make_interval(mins=>:step)<=fetched_at AND fetched_at<=:b ORDER BY t"""),
        params,
    ).all()
    result = []
    for side, zones in prior.metrics["zones"].items():
        for zone in zones:
            lo, hi = zone["lo"], zone["hi"]
            members = [
                r
                for r in prior.accounts
                if r["liq_px"] is not None and lo <= r["liq_px"] <= hi and (r["szi"] > 0) == (side == "long")
            ]
            ids = [r["wallet_id"] for r in members]
            touched = next((b for b in bars if float(b.h) >= lo and float(b.l) <= hi), None)
            cutoff = touched[0] if touched else now
            exits = s.execute(
                text("""SELECT count(*) reductions, count(*) FILTER(WHERE kind='close' AND liq_px>0
                    AND ((szi_before>0 AND mark_px<=liq_px*1.005) OR (szi_before<0 AND mark_px>=liq_px*.995))) suspected
                FROM market_event WHERE coin=:c AND venue=:v AND wallet_id=ANY(:ids) AND window_start>=:a
                AND observed_at<=:b AND kind IN ('decrease','close','flip')"""),
                {**params, "ids": ids, "b": cutoff},
            ).one()
            if venue == "hyperliquid":
                proof = s.scalar(
                    text("""SELECT count(*) FROM fill f JOIN wallet w ON w.id=f.wallet_id
                    JOIN raw_object raw ON raw.id=f.raw_object_id WHERE f.coin=:c AND f.wallet_id=ANY(:ids)
                    AND f.time>=:a AND f.time<=:b AND raw.fetched_at<=:b
                    AND lower(f.liquidation->>'liquidatedUser')=w.address"""),
                    {**params, "ids": ids},
                )
            else:
                proof = s.scalar(
                    text("""SELECT count(*) FROM okx_position WHERE coin=:c AND wallet_id=ANY(:ids)
                    AND close_time>=:a AND close_time<=:b AND fetched_at<=:b AND liquidated"""),
                    {**params, "ids": ids},
                )
            result.append(
                {
                    "side": side,
                    "lo": lo,
                    "hi": hi,
                    "frozen_usd": zone["notional"],
                    "frozen_frame": prior.id,
                    "bars": len(bars),
                    "interval": iv,
                    "touch_bar": touched[0].isoformat() if touched else None,
                    "reductions_before_touch_or_cutoff": exits.reductions,
                    "suspected_closes": exits.suspected,
                    "venue_liquidation_records": proof,
                    "wallets": len(ids),
                    "return_from_touch_close": float(bars[-1].c / touched.c - 1) if touched and touched.c else None,
                }
            )
    return result


def notice_rows(s, coin, cutoff, since):
    rows = s.execute(
        select(ResearchNotice, ResearchFrame.venue, ResearchFrame.available_at)
        .join(ResearchFrame)
        .where(
            ResearchFrame.coin == coin, ResearchFrame.available_at <= cutoff, ResearchFrame.available_at >= since
        )
        .order_by(ResearchFrame.available_at.desc())
        .limit(40)
    )
    out = []
    for n, v, t in rows:
        outcome = n.outcome if n.outcome and datetime.fromisoformat(n.outcome["evaluated_at"]) <= cutoff else None
        out.append(
            {
                "id": n.id,
                "frame_id": n.frame_id,
                "coin": coin,
                "venue": v,
                "at": t,
                "kind": n.kind,
                "evidence": n.evidence,
                "due_at": n.due_at,
                "outcome": outcome,
                "watching": n.watching,
            }
        )
    return out


def daily_review(session, coins, now):
    """One read cutoff for today's sample, yesterday's matured predictions and the cross-asset comparison."""
    since = now - timedelta(days=1)
    result = {}
    for coin in coins:
        by_venue = {}
        for venue in VENUES:
            r = session.execute(
                select(ResearchFrame.id, ResearchFrame.metrics, ResearchFrame.context, ResearchFrame.available_at)
                .where(
                    ResearchFrame.coin == coin,
                    ResearchFrame.venue == venue,
                    ResearchFrame.available_at.between(now - timedelta(hours=2), now),
                )
                .order_by(ResearchFrame.available_at.desc())
                .limit(1)
            ).first()
            if not r:
                continue
            change = (r.metrics.get("windows") or {}).get("24")
            by_venue[venue] = {
                "frame": r.id,
                "at": r.available_at,
                "holders": r.metrics["n"],
                "funding": r.context["fields"].get("funding"),
                "long_share": r.metrics["long_share"],
                "specialist_gap": r.metrics["divergence"],
                "change": change,
            }
        settled = session.execute(
            text("""
            SELECT horizon,count(*) n,avg((outcome->>'p_brier')::float) brier,
                avg((outcome->>'baseline_brier')::float) baseline
            FROM research_forecast WHERE coin=:c AND outcome->>'status'='settled'
            AND (outcome->>'evaluated_at')::timestamptz BETWEEN :a AND :b GROUP BY horizon ORDER BY horizon
        """),
            dict(c=coin, a=since, b=now),
        ).mappings()
        result[coin] = {"venues": by_venue, "outcomes": [dict(r) for r in settled]}
    comparison = {}
    for venue in VENUES:
        directions = []
        for coin in ("BTC", "ETH"):
            d = result.get(coin, {}).get("venues", {}).get(venue, {}).get("change") or {}
            net = d.get("net_units")
            if net is not None and d.get("common_n", 0) > 0:
                directions.append(1 if net > 0 else -1 if net < 0 else 0)
        comparison[venue] = (
            "insufficient"
            if len(directions) < 2
            else "opposite"
            if directions[0] * directions[1] < 0
            else "flat"
            if 0 in directions
            else "aligned"
        )
    return jsonable_encoder({"markets": result, "comparison": comparison})
