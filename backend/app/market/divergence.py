"""Smart money vs the crowd: per coin, the share of long traders among timing-significant traders (q < 0.1) vs the
whole observed sample. History comes from the hourly market_snapshot; the forward return is shown next to it as
a historical relation only, never as a forecast."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.market import stats

MIN_SMART = 3  # coins with fewer significant traders holding them are too noisy to compare


def _share(r: dict) -> float | None:
    n = (r.get("long_traders") or 0) + (r.get("short_traders") or 0)
    return (r["long_traders"] / n) if n else None


def current(session: Session, venue: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    crowd = {r["coin"]: r for r in stats.coin_rows(session, venue, "all", now, 2)}
    out = []
    for r in stats.coin_rows(session, venue, "timing", now, 1):
        c = crowd.get(r["coin"])
        smart_n = (r["long_traders"] or 0) + (r["short_traders"] or 0)
        if c is None or smart_n < MIN_SMART:
            continue
        s, a = _share(r), _share(c)
        if s is None or a is None:
            continue
        out.append({"coin": r["coin"], "smart_long_share": s, "crowd_long_share": a, "divergence": s - a,
                    "smart_traders": smart_n, "crowd_traders": (c["long_traders"] or 0) + (c["short_traders"] or 0),
                    "smart_net_ntl": float(r["long_ntl"] or 0) - float(r["short_ntl"] or 0)})
    out.sort(key=lambda x: -abs(x["divergence"]))
    return out


def history(session: Session, venue: str, coin: str, days: int = 60, forward_days: int = 7) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(text("""
        SELECT DISTINCT ON (bucket_ts::date, cohort) bucket_ts::date AS day, cohort, long_traders, short_traders
        FROM market_snapshot WHERE venue = :v AND coin = :coin AND cohort IN ('all', 'timing') AND bucket_ts >= :s
        ORDER BY bucket_ts::date, cohort, bucket_ts DESC"""), {"v": venue, "coin": coin, "s": since}).mappings().all()
    closes = {}
    for src in ("hyperliquid", "binance_um"):
        closes = {t.date(): float(c) for t, c in session.execute(text("""
            SELECT t, c FROM market_candle WHERE source = :src AND coin = :coin AND interval = '1d' AND t >= :s"""),
            {"src": src, "coin": coin, "s": since - timedelta(days=1)}).all()}
        if closes:
            break
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["cohort"]] = dict(r)
    out = []
    for day in sorted(by_day):
        d = by_day[day]
        if "all" not in d or "timing" not in d:
            continue
        smart_n = d["timing"]["long_traders"] + d["timing"]["short_traders"]
        s, a = _share(d["timing"]), _share(d["all"])
        if s is None or a is None:
            continue
        px, fwd = closes.get(day), closes.get(day + timedelta(days=forward_days))
        out.append({"day": str(day), "smart_long_share": s, "crowd_long_share": a, "divergence": s - a,
                    "smart_traders": smart_n, "close": px,
                    "forward_return": (fwd / px - 1) if px and fwd else None})
    return out
