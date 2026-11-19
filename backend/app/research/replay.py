"""Retrospective event catalogue and fill attribution. Knowledge time is independent of the event clock."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import quantiles

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import ResearchEvent

WINDOWS = (72, 48, 24, 12, 4, 1)
SOURCE = "binance_um"
REFERENCE = (
    ("btc-aug-rise", "2026-08-18T00:00", "2026-08-25T00:00", "o", "o", "btc-2026-summer"),
    ("btc-sep-rise", "2026-09-15T18:50", "2026-09-21T20:27", "l", "h", "btc-2026-summer"),
    ("btc-drop-peak1", "2026-09-21T20:27", "2026-11-15T01:45", "h", "l", "btc-2026-autumn"),
    ("btc-drop-peak2", "2026-10-02T12:31", "2026-11-15T01:45", "h", "l", "btc-2026-autumn"),
)


def add_event(s, key, coin, start, end, px, end_px, cluster, method, title):
    stmt = insert(ResearchEvent).values(
        event_key=key,
        revision=1,
        coin=coin,
        start_at=start,
        end_at=end,
        start_px=px,
        end_px=end_px,
        direction="up" if end_px > px else "down",
        source=SOURCE,
        cluster_key=cluster,
        method=method,
        title=title,
    )
    return int(
        s.scalar(stmt.on_conflict_do_nothing(index_elements=["event_key", "revision"]).returning(ResearchEvent.id))
        is not None
    )


def refresh_events(s, coin, now):
    count = 0
    if coin == "BTC":
        for key, a, b, col_a, col_b, cluster in REFERENCE:
            start, end = (datetime.fromisoformat(ts).replace(tzinfo=UTC) for ts in (a, b))
            rows = (
                s.execute(
                    text("""
                SELECT t,o,h,l FROM market_candle WHERE coin=:coin AND source=:src AND interval='1m'
                AND t IN (:a,:b) AND fetched_at <= :now AND t + interval '1 minute' <= :now ORDER BY t
            """),
                    dict(coin=coin, src=SOURCE, a=start, b=end, now=now),
                )
                .mappings()
                .all()
            )
            if len(rows) == 2:
                count += add_event(
                    s, key, coin, start, end, rows[0][col_a], rows[1][col_b], cluster, "candidate-minute-v1", key
                )
    bars = s.execute(
        text("""
        SELECT t,o,c FROM market_candle WHERE source=:src AND coin=:coin AND interval='1d'
        AND t + interval '1 day' <= :now AND fetched_at <= :now AND o>0 AND c>0 ORDER BY t
    """),
        dict(coin=coin, src=SOURCE, now=now),
    ).all()
    past = []
    for i, (start, px, close) in enumerate(bars):
        if i and start - bars[i - 1][0] != timedelta(days=1):
            past.clear()
        change = abs(float(close / px - 1))
        if len(past) >= 60 and change > quantiles(past[-60:], n=10, method="inclusive")[8]:
            key = f"{coin.lower()}-daily-{start.date()}"
            count += add_event(
                s,
                key,
                coin,
                start,
                start + timedelta(days=1),
                px,
                close,
                key,
                "prior60-p90-v1",
                f"{coin} · {start.date()} · {float(close / px - 1):+.1%}",
            )
        past.append(change)
    return count


def catalog(s, coin, as_of):
    q = select(ResearchEvent).where(ResearchEvent.coin == coin, ResearchEvent.available_at <= as_of)
    rows = s.scalars(q.order_by(ResearchEvent.start_at.desc(), ResearchEvent.revision.desc())).all()
    versions = maxrev(rows)
    return [event_info(e) for e in rows if e.revision == versions[e.event_key]]


def maxrev(rows):
    versions = {}
    for row in rows:
        versions[row.event_key] = max(versions.get(row.event_key, 0), row.revision)
    return versions


def event_info(e):
    return {
        k: getattr(e, k)
        for k in (
            "id",
            "event_key",
            "revision",
            "coin",
            "title",
            "direction",
            "cluster_key",
            "source",
            "method",
            "start_at",
            "end_at",
            "start_px",
            "end_px",
            "available_at",
        )
    }


def wallet_flows(s, coin, anchor, hours, cutoff):
    """One row per observed wallet. Amounts are coin units; unclassifiable rows never contribute zero flows."""
    rows = s.execute(
        text("""
        WITH f AS (
          SELECT f.wallet_id, f.start_position b, f.start_position + CASE f.side WHEN 'B' THEN f.sz
                 WHEN 'A' THEN -f.sz END a, f.liquidation, f.raw_object_id
          FROM fill f JOIN raw_object r ON r.id=f.raw_object_id
          WHERE f.coin=:coin AND f.time>=:start AND f.time<:anchor AND r.fetched_at<=:cutoff
        )
        SELECT w.address, count(*) fills, count(*) FILTER (WHERE a IS NULL OR b IS NULL) missing,
          sum(greatest(greatest(a,0)-greatest(b,0),0)) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL) long_add,
          sum(greatest(greatest(b,0)-greatest(a,0),0)) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL) long_cut,
          sum(greatest(greatest(-a,0)-greatest(-b,0),0)) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL) short_add,
          sum(greatest(greatest(-b,0)-greatest(-a,0),0)) FILTER (WHERE a IS NOT NULL AND b IS NOT NULL) short_cut,
          count(*) FILTER (WHERE lower(liquidation->>'liquidatedUser')=w.address) liquidation_fills
        FROM f JOIN wallet w ON w.id=f.wallet_id GROUP BY w.address ORDER BY w.address
    """),
        dict(coin=coin, start=anchor - timedelta(hours=hours), anchor=anchor, cutoff=cutoff),
    ).mappings()
    return [dict(r) for r in rows]


FLOW_KEYS = ("long_add", "long_cut", "short_add", "short_cut")


def summarize(rows):
    valid = [r for r in rows if r["long_add"] is not None]
    totals = {key: sum((r[key] for r in valid), Decimal(0)) if valid else None for key in FLOW_KEYS}
    ranked = sorted(
        ({**r, "net": r["long_add"] - r["long_cut"] - r["short_add"] + r["short_cut"]} for r in valid),
        key=lambda r: (-abs(r["net"]), r["address"]),
    )
    net = sum(r["net"] for r in ranked) if ranked else None
    gross = sum(abs(r["net"]) for r in ranked)

    def share(n):
        return sum(abs(r["net"]) for r in ranked[:n]) / gross if gross else None

    return {
        **totals,
        "net": net,
        "without_largest": net - ranked[0]["net"] if ranked else None,
        "top1_share": share(1),
        "top5_share": share(5),
        "top10_share": share(10),
        "wallets": len(rows),
        "fills": sum(r["fills"] for r in rows),
        "missing": sum(r["missing"] for r in rows),
        "contributors": ranked,
        "source": "hyperliquid/userFillsByTime",
    }


def details(s, event, coin, hours, vintage, mode):
    anchor = event.start_at
    cutoff = min(anchor, vintage) if mode == "as_observed" else vintage
    rows = wallet_flows(s, coin, anchor, hours, cutoff)
    okx = (
        s.execute(
            text("""
        SELECT count(*) positions, count(distinct wallet_id) traders FROM okx_position
        WHERE coin=:coin AND open_time>=:start AND open_time<:anchor AND fetched_at<=:cutoff
    """),
            dict(coin=coin, start=anchor - timedelta(hours=hours), anchor=anchor, cutoff=cutoff),
        )
        .mappings()
        .one()
    )
    bars = s.execute(
        text("""
        SELECT t,c FROM market_candle WHERE coin=:coin AND source=:src AND interval='1h'
        AND t>=:a AND t + interval '1 hour'<=:b AND fetched_at<=:cutoff ORDER BY t
    """),
        dict(
            coin=coin,
            src=SOURCE,
            a=anchor - timedelta(hours=hours),
            b=event.end_at + timedelta(hours=24),
            cutoff=vintage,
        ),
    ).all()
    return {
        "event": event_info(event),
        "coin": coin,
        "synchronous": coin != event.coin,
        "hours": hours,
        "mode": mode,
        "evidence_cutoff": cutoff,
        "vintage": vintage,
        "flow": summarize(rows),
        "okx": dict(okx),
        "prices": [[t + timedelta(hours=1), c] for t, c in bars],
        "price_basis": "completed_binance_hourly_bars_retrospective",
        "frame": s.scalar(
            text("""SELECT id FROM research_frame WHERE coin=:c AND venue='hyperliquid'
                AND available_at<=:t AND observed_at>=:t - interval '2 hours' ORDER BY available_at DESC LIMIT 1"""),
            {"c": coin, "t": min(anchor, vintage)},
        ),
    }
