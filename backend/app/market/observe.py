"""Record one observation of a trader's open positions: emit change events, replace the current-position rows and
schedule the next poll (docs/12).

Time semantics: a venue-reported open time (OKX cTime) inside the poll window makes an open "exact"; otherwise the
change happened somewhere between our previous and this poll ("window"). The first poll of a trader only reports
what is open as "first_seen" — those positions were not opened at that moment and are never counted as opens.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.alerts.diff import Pos, size_event

ZERO = Decimal(0)
TIERS = ("hot", "warm", "cold")
INTERVAL = {
    ("hyperliquid", "hot"): timedelta(minutes=30),
    ("hyperliquid", "warm"): timedelta(minutes=60),
    ("hyperliquid", "cold"): timedelta(hours=12),
    ("okx", "hot"): timedelta(minutes=30),
    ("okx", "warm"): timedelta(minutes=60),
    ("okx", "cold"): timedelta(hours=4),
}
STALE_FACTOR = 2  # data older than 2 x the poll interval no longer counts as current
HL_HOT_NOTIONAL = Decimal(100_000)  # Hyperliquid accounts holding this much are polled at the hot cadence


@dataclass
class Change:
    coin: str
    kind: str
    side: str
    szi_before: Decimal
    szi_after: Decimal
    notional_change: Decimal
    mark_px: Decimal | None
    entry_px: Decimal | None
    leverage: int | None
    liq_px: Decimal | None
    event_time: datetime
    time_precision: str
    window_start: datetime | None


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


def mark_of(p: Pos | None) -> Decimal | None:
    if p is None or not p.szi or not p.position_value:
        return None
    return abs(p.position_value / p.szi)


def tier_for(venue: str, base_tier: str, positions: dict[str, Pos]) -> str:
    """Hot for cohort members (base tier) and Hyperliquid accounts with large positions; warm with any position."""
    gross = sum((abs(p.position_value or ZERO) for p in positions.values()), ZERO)
    tier = "warm" if positions else "cold"
    if venue == "hyperliquid" and gross >= HL_HOT_NOTIONAL:
        tier = "hot"
    if venue == "okx" and positions:
        tier = "hot"  # few OKX lead traders, and their changes are what followers act on
    return min(tier, base_tier, key=TIERS.index)


def changes(prev: dict[str, dict], after: dict[str, Pos], opened: dict[str, datetime], mids: dict[str, Decimal],
            prev_obs: datetime | None, now: datetime) -> list[Change]:
    """Pure diff of the stored rows (prev: coin -> trader_position row) against a fresh snapshot."""
    out: list[Change] = []
    for coin in sorted(set(prev) | set(after)):
        b = Decimal(prev[coin]["szi"]) if coin in prev else ZERO
        p = after.get(coin)
        a = p.szi if p else ZERO
        kind = ("first_seen" if a else None) if prev_obs is None else size_event(b, a)
        if not kind:
            continue
        mark = mark_of(p) or mids.get(coin) or (prev[coin]["mark_px"] if coin in prev else None)
        src = opened.get(coin)
        exact = kind in ("open", "flip", "first_seen") and src is not None and (prev_obs is None or src > prev_obs)
        if kind == "first_seen" and not exact:
            event_time, precision, start = now, "window", None
        elif exact:
            event_time, precision, start = src, "exact", prev_obs
        else:
            event_time, precision, start = now, "window", prev_obs
        out.append(Change(coin, kind, "long" if _sign(a or b) > 0 else "short", b, a,
                          abs(a - b) * mark if mark else ZERO, mark, p.entry_px if p else None,
                          p.leverage if p else None, p.liquidation_px if p else None, event_time, precision, start))
    return out


def record_observation(s: Session, wallet_id: int, venue: str, after: dict[str, Pos], account_value: Decimal | None,
                       now: datetime, mids: dict[str, Decimal] | None = None,
                       opened: dict[str, datetime] | None = None) -> list[Change]:
    mids, opened = mids or {}, opened or {}
    after = {c: p for c, p in after.items() if p.szi}
    watch = s.execute(text("SELECT last_observed_at, base_tier FROM position_watch WHERE wallet_id = :w"),
                      {"w": wallet_id}).first()
    prev_obs = watch.last_observed_at if watch else None
    base_tier = watch.base_tier if watch else "cold"
    prev = {r["coin"]: dict(r) for r in s.execute(text("SELECT * FROM trader_position WHERE wallet_id = :w"),
                                                  {"w": wallet_id}).mappings()}
    found = changes(prev, after, opened, mids, prev_obs, now)
    by_coin = {c.coin: c for c in found}
    if found:
        s.execute(text("""
            INSERT INTO market_event (wallet_id, venue, coin, kind, side, szi_before, szi_after, notional_change,
              mark_px, entry_px, leverage, liq_px, event_time, time_precision, window_start, observed_at)
            VALUES (:w, :v, :coin, :kind, :side, :b, :a, :ntl, :mark, :entry, :lev, :liq, :t, :prec, :start, :now)
            ON CONFLICT (wallet_id, coin, observed_at, kind) DO NOTHING"""),
            [{"w": wallet_id, "v": venue, "coin": c.coin, "kind": c.kind, "side": c.side, "b": c.szi_before,
              "a": c.szi_after, "ntl": c.notional_change, "mark": c.mark_px, "entry": c.entry_px, "lev": c.leverage,
              "liq": c.liq_px, "t": c.event_time, "prec": c.time_precision, "start": c.window_start, "now": now}
             for c in found])
    rows = []
    for coin, p in after.items():
        old = prev.get(coin)
        same_side = old is not None and _sign(Decimal(old["szi"])) == _sign(p.szi)
        ch = by_coin.get(coin)
        if opened.get(coin):
            opened_at, source, opened_after = opened[coin], "source", None
        elif same_side:
            opened_at, source, opened_after = old["opened_at"], old["opened_at_source"], old["opened_after"]
        elif ch and ch.kind in ("open", "flip"):
            opened_at, source, opened_after = None, "window", prev_obs
        else:
            opened_at, source, opened_after = None, "unknown", None
        rows.append({"w": wallet_id, "coin": coin, "v": venue, "szi": p.szi,
                     "ntl": abs(p.position_value) if p.position_value else abs(p.szi) * (mids.get(coin) or ZERO),
                     "mark": mark_of(p) or mids.get(coin), "entry": p.entry_px, "lev": p.leverage,
                     "mode": p.leverage_type, "liq": p.liquidation_px, "upl": p.unrealized_pnl, "oa": opened_at,
                     "src": source, "after": opened_after,
                     "fs": old["first_seen_at"] if same_side else now, "now": now})
    s.execute(text("DELETE FROM trader_position WHERE wallet_id = :w"), {"w": wallet_id})
    if rows:
        s.execute(text("""
            INSERT INTO trader_position (wallet_id, coin, venue, szi, notional_usd, mark_px, entry_px, leverage,
              margin_mode, liq_px, upl, opened_at, opened_at_source, opened_after, first_seen_at, observed_at)
            VALUES (:w, :coin, :v, :szi, :ntl, :mark, :entry, :lev, :mode, :liq, :upl, :oa, :src, :after, :fs, :now)"""),
            rows)
    tier = tier_for(venue, base_tier, after)
    every = INTERVAL[(venue, tier)]
    s.execute(text("""
        INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due, last_observed_at,
          stale_after, last_error, failures, account_value, n_positions, gross_notional)
        VALUES (:w, :v, :tier, :base, :now, :due, :now, :stale, NULL, 0, :av, :n, :gross)
        ON CONFLICT (wallet_id) DO UPDATE SET tier = EXCLUDED.tier, next_due = EXCLUDED.next_due,
          last_observed_at = EXCLUDED.last_observed_at, stale_after = EXCLUDED.stale_after, last_error = NULL,
          failures = 0, account_value = coalesce(EXCLUDED.account_value, position_watch.account_value),
          n_positions = EXCLUDED.n_positions, gross_notional = EXCLUDED.gross_notional"""),
        {"w": wallet_id, "v": venue, "tier": tier, "base": base_tier, "now": now, "due": now + every,
         "stale": now + every * STALE_FACTOR, "av": account_value, "n": len(rows),
         "gross": sum((r["ntl"] for r in rows), ZERO)})
    return found


def record_failure(s: Session, wallet_id: int, now: datetime, error: str) -> None:
    """Back off a failing trader (2 min doubling, at most its normal interval); its positions go stale on schedule."""
    s.execute(text("""
        UPDATE position_watch SET failures = failures + 1, last_error = :e,
          next_due = :now + least(interval '2 minutes' * power(2, least(failures, 6)),
                                  CASE tier WHEN 'cold' THEN interval '4 hours' ELSE interval '30 minutes' END)
        WHERE wallet_id = :w"""), {"w": wallet_id, "e": error[:300], "now": now})
