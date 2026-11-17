"""Trading behaviour fingerprint (b1): when someone trades and how they handle wins and losses, from archived
decisions and closed position cycles. Every flag carries its value and sample; small samples raise no flag."""

from datetime import UTC, datetime, timedelta
from statistics import mean, median

from sqlalchemy import text
from sqlalchemy.orm import Session

HOLD_BUCKETS = (1, 4, 24, 72, 168, 504)  # hours: <1h, 1-4h, 4h-1d, 1-3d, 3-7d, 1-3w, >3w
MIN_FLAG = 10  # observations needed before a behaviour flag is raised


def _bucket(h: float) -> int:
    return next((i for i, b in enumerate(HOLD_BUCKETS) if h < b), len(HOLD_BUCKETS))


def fingerprint(session: Session, wallet_id: int, venue: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    opens = session.execute(text("""
        SELECT started_at FROM trade_decision WHERE wallet_id = :w AND kind IN ('open', 'flip')"""),
        {"w": wallet_id}).scalars().all()
    heat = [[0] * 24 for _ in range(7)]  # [weekday][hour], UTC
    for t in opens:
        heat[t.weekday()][t.hour] += 1
    cycles = [dict(r) for r in session.execute(text("""
        SELECT id, opened_at, closed_at, direction, holding_hours::float AS hours,
               (realized_pnl - fees)::float AS net, coalesce(max_notional, 0)::float AS notional
        FROM position_cycle WHERE wallet_id = :w AND status <> 'open' AND complete ORDER BY closed_at"""),
        {"w": wallet_id}).mappings().all()]
    wins = [c for c in cycles if c["net"] > 0]
    losses = [c for c in cycles if c["net"] <= 0]
    hold = {"win": [0] * (len(HOLD_BUCKETS) + 1), "loss": [0] * (len(HOLD_BUCKETS) + 1)}
    for c in cycles:
        hold["win" if c["net"] > 0 else "loss"][_bucket(c["hours"] or 0)] += 1
    out: dict = {"heat": heat, "opens": len(opens), "hold": hold, "hold_buckets": HOLD_BUCKETS,
                 "closed": len(cycles), "flags": []}

    def flag(key: str, value, n: int, good: bool | None) -> None:
        out["flags"].append({"key": key, "value": value, "n": n, "good": good})

    # let profits run vs hold losers: median holding time of winners vs losers
    if len(wins) >= MIN_FLAG and len(losses) >= MIN_FLAG:
        hw, hl = median(c["hours"] for c in wins), median(c["hours"] for c in losses)
        out["hold_win_h"], out["hold_loss_h"] = hw, hl
        if hl > 2 * hw:
            flag("holds_losers", hl / hw, len(losses), False)
        elif hw > 1.5 * hl:
            flag("lets_winners_run", hw / hl, len(wins), True)
    # stop discipline: the biggest loss against the typical one
    if len(losses) >= MIN_FLAG:
        avg = mean(-c["net"] for c in losses)
        big = [c for c in losses if -c["net"] > 3 * avg]
        out["max_loss_ratio"] = max(-c["net"] for c in losses) / avg if avg > 0 else None
        out["tail_loss_share"] = len(big) / len(losses)
        if out["max_loss_ratio"] and out["max_loss_ratio"] > 8:
            flag("fat_tail_losses", out["max_loss_ratio"], len(losses), False)
        elif out["max_loss_ratio"] and out["max_loss_ratio"] < 3:
            flag("cuts_losses", out["max_loss_ratio"], len(losses), True)
    # revenge trading: a much bigger position within 24h after a losing close
    sizes = [c["notional"] for c in cycles if c["notional"] > 0]
    med_size = median(sizes) if sizes else 0
    after_loss = up = 0
    by_open = sorted(cycles, key=lambda c: c["opened_at"])
    starts = [c["opened_at"] for c in by_open]
    for c in losses:
        nxt = next((x for x, t in zip(by_open, starts) if c["closed_at"] <= t <= c["closed_at"] + timedelta(hours=24)), None)
        if nxt is None or not med_size:
            continue
        after_loss += 1
        up += nxt["notional"] > 1.5 * med_size
    if after_loss:
        out["revenge_share"] = up / after_loss
        if after_loss >= MIN_FLAG and up / after_loss >= 0.3:
            flag("sizes_up_after_loss", up / after_loss, after_loss, False)
    # averaging down: adds at a worse price than the running average entry (Hyperliquid: OKX reports whole positions)
    if venue == "hyperliquid":
        worse = adds = 0
        run: dict[int, tuple[float, float]] = {}  # cycle -> (size, average entry)
        for cid, kind, size, vwap, after in session.execute(text("""
            SELECT cycle_id, kind, size::float, vwap::float, pos_after::float FROM trade_decision
            WHERE wallet_id = :w AND cycle_id IS NOT NULL ORDER BY started_at, id"""), {"w": wallet_id}).all():
            if kind in ("open", "flip"):
                run[cid] = (abs(after), vwap)
            elif kind == "add" and cid in run:
                sz, avg = run[cid]
                adds += 1
                worse += (after > 0 and vwap < avg) or (after < 0 and vwap > avg)
                run[cid] = (sz + size, (sz * avg + size * vwap) / (sz + size) if sz + size else vwap)
        if adds:
            out["avg_down_share"] = worse / adds
            if adds >= MIN_FLAG and worse / adds >= 0.6:
                flag("averages_down", worse / adds, adds, False)
    # size / leverage drift: last 30 days against before
    cut = now - timedelta(days=30)
    recent = [c["notional"] for c in cycles if c["opened_at"] >= cut and c["notional"] > 0]
    before = [c["notional"] for c in cycles if c["opened_at"] < cut and c["notional"] > 0]
    if len(recent) >= 5 and len(before) >= MIN_FLAG:
        out["size_drift"] = median(recent) / median(before)
        if out["size_drift"] >= 2:
            flag("size_up", out["size_drift"], len(recent), None)
    if venue == "okx":
        lev = session.execute(text("""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY lever) FILTER (WHERE open_time >= :c),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY lever) FILTER (WHERE open_time < :c),
                   count(*) FILTER (WHERE open_time >= :c)
            FROM okx_position WHERE wallet_id = :w AND lever IS NOT NULL"""), {"w": wallet_id, "c": cut}).one()
        if lev[0] and lev[1]:
            out["lev_recent"], out["lev_before"] = float(lev[0]), float(lev[1])
            if lev[2] >= 5 and float(lev[0]) >= 1.5 * float(lev[1]):
                flag("leverage_up", float(lev[0]) / float(lev[1]), lev[2], False)
    # streaks
    best = worst = cur = 0
    for c in cycles:
        cur = (cur + 1 if cur >= 0 else 1) if c["net"] > 0 else (cur - 1 if cur <= 0 else -1)
        best, worst = max(best, cur), min(worst, cur)
    out["win_streak"], out["loss_streak"] = best, -worst
    return out
