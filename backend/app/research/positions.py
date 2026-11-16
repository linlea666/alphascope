"""Position reconstruction from fills (version p1).

Every Hyperliquid fill carries `startPosition`, so the exact position before and after each fill is
known without full history. From that we build:

- decisions: consecutive same-side fills on one coin less than DECISION_GAP apart (split orders, TWAP
  slices and partial fills count once); kind = open / add / reduce / close / flip from the position
  before and after.
- position cycles: from flat (or the first observed fill) to flat or a flip, with entry/exit prices,
  max size, realized PnL and fees; the holding time assigns the timeframe scale.

A startPosition that does not continue the previous fill's resulting position means fills are missing
(older than the archive, or a gap); the affected cycle is marked incomplete and the break is counted.

Scope: perpetuals only (including HIP-3 `dex:coin`). Spot fills (`@n`, `BASE/QUOTE`) are excluded:
spot balances also move with base-token fees, transfers and dust conversion, so they do not chain and
are not leveraged positions.

Ordering: `tid` is not chronological, and one taker order often produces many fills with the same
millisecond timestamp. Fills sharing a timestamp are therefore ordered by chaining startPosition
(each fill starts where the previous one ended), not by tid.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

RECON_VERSION = "p2"  # p2: per-decision entry/exit legs stored on each cycle
DECISION_GAP = timedelta(minutes=15)
ZERO_TOL = Decimal("1e-9")
# Holding-time boundaries (hours) of the timeframe ladder, see docs/09.
SCALES = (("scalp", 4), ("short", 72), ("swing", 504), ("macro", float("inf")))


def scale_for(hours: float) -> str:
    return next(name for name, limit in SCALES if hours < limit)


def _sign(x: Decimal) -> int:
    return 0 if abs(x) <= ZERO_TOL else (1 if x > 0 else -1)


@dataclass(frozen=True)
class FillRow:
    time: datetime
    tid: int
    side: str  # B | A
    sz: Decimal
    px: Decimal
    start_position: Decimal
    closed_pnl: Decimal
    fee: Decimal


@dataclass
class Cycle:
    direction: int
    opened_at: datetime
    complete: bool
    closed_at: datetime | None = None
    status: str = "open"
    entry_sz: Decimal = Decimal(0)
    entry_val: Decimal = Decimal(0)
    exit_sz: Decimal = Decimal(0)
    exit_val: Decimal = Decimal(0)
    max_size: Decimal = Decimal(0)
    max_notional: Decimal = Decimal(0)
    n_fills: int = 0
    n_decisions: int = 0
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    # decision number -> [first time, size, size * price] for the opening / closing parts of the cycle
    entry_legs: dict = field(default_factory=dict)
    exit_legs: dict = field(default_factory=dict)

    def holding_hours(self, now: datetime) -> float:
        return ((self.closed_at or now) - self.opened_at).total_seconds() / 3600


@dataclass
class Decision:
    side: str
    started_at: datetime
    ended_at: datetime
    pos_before: Decimal
    pos_after: Decimal
    n_fills: int = 0
    size: Decimal = Decimal(0)
    notional: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    cycle: int | None = None  # index into cycles

    @property
    def kind(self) -> str:
        b, a = _sign(self.pos_before), _sign(self.pos_after)
        if b == 0:
            return "open"
        if a == 0:
            return "close"
        if a != b:
            return "flip"
        return "add" if abs(self.pos_after) > abs(self.pos_before) else "reduce"


@dataclass
class Reconstruction:
    cycles: list[Cycle] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    breaks: int = 0


def _after(f: FillRow) -> Decimal:
    return f.start_position + (f.sz if f.side == "B" else -f.sz)


def chain_order(fills: list[FillRow]) -> list[FillRow]:
    """Sort by time; within one timestamp, follow the startPosition chain from the running position."""
    out: list[FillRow] = []
    pos: Decimal | None = None
    by_time: dict[datetime, list[FillRow]] = {}
    for f in fills:
        by_time.setdefault(f.time, []).append(f)
    for t in sorted(by_time):
        group = sorted(by_time[t], key=lambda x: x.tid)
        while group:
            pick = None
            if pos is not None:
                pick = next((g for g in group if abs(g.start_position - pos) <= ZERO_TOL), None)
            if pick is None:
                # gap or first fill: start from a chain head (no other fill in the group ends where it starts)
                ends = [_after(g) for g in group]
                heads = [g for g in group if not any(abs(g.start_position - e) <= ZERO_TOL for e in ends)] or group
                pick = min(heads, key=lambda g: abs(g.start_position - pos)) if pos is not None else heads[0]
            group.remove(pick)
            out.append(pick)
            pos = _after(pick)
    return out


def reconstruct(fills: list[FillRow]) -> Reconstruction:
    out = Reconstruction()
    pos: Decimal | None = None
    cur: int | None = None  # index of the open cycle
    dec: Decision | None = None
    dec_no = 0  # running decision number, keys the per-cycle entry/exit legs

    def close_decision():
        nonlocal dec
        if dec is not None:
            out.decisions.append(dec)
            if dec.cycle is not None:
                out.cycles[dec.cycle].n_decisions += 1
        dec = None

    for f in chain_order(fills):
        sp = f.start_position
        if pos is not None and abs(sp - pos) > ZERO_TOL:
            # fills are missing between the previous fill and this one
            out.breaks += 1
            close_decision()
            if cur is not None:
                c = out.cycles[cur]
                c.complete = False
                if _sign(sp) != c.direction:  # went flat or flipped while we could not see it
                    c.closed_at, c.status = f.time, "closed"
                    cur = None
        delta = f.sz if f.side == "B" else -f.sz
        after = sp + delta
        side = "buy" if f.side == "B" else "sell"

        if cur is None and _sign(sp) != 0:
            # first sight of a position opened before our archive starts (or after a gap)
            out.cycles.append(Cycle(_sign(sp), f.time, complete=False))
            cur = len(out.cycles) - 1

        if dec is None or dec.side != side or f.time - dec.ended_at > DECISION_GAP:
            close_decision()
            dec = Decision(side, f.time, f.time, sp, sp)
            dec_no += 1
        dec.ended_at, dec.pos_after = f.time, after
        dec.n_fills += 1
        dec.size += f.sz
        dec.notional += f.sz * f.px
        dec.realized_pnl += f.closed_pnl
        dec.fee += f.fee

        s_before, s_after = _sign(sp), _sign(after)
        if s_before != 0 and s_after != s_before:  # closed or flipped
            closing = abs(sp)
        elif s_before != 0 and abs(after) < abs(sp):  # reduced
            closing = abs(sp) - abs(after)
        else:
            closing = Decimal(0)
        if s_after != 0 and s_after != s_before:  # opened or flipped
            opening = abs(after)
        elif s_after != 0 and abs(after) > abs(sp):  # added
            opening = abs(after) - abs(sp)
        else:
            opening = Decimal(0)

        if cur is not None and closing:
            c = out.cycles[cur]
            c.exit_sz += closing
            c.exit_val += closing * f.px
            leg = c.exit_legs.setdefault(dec_no, [f.time, Decimal(0), Decimal(0)])
            leg[1] += closing
            leg[2] += closing * f.px
            c.realized_pnl += f.closed_pnl
            c.fees += f.fee * (closing / f.sz)
            c.n_fills += 1
            if s_after != c.direction:
                c.closed_at = f.time
                c.status = "closed" if s_after == 0 else "flipped"
                dec.cycle = cur
                cur = None
        if opening and s_after != 0:
            if cur is None:
                out.cycles.append(Cycle(s_after, f.time, complete=True))
                cur = len(out.cycles) - 1
            c = out.cycles[cur]
            c.entry_sz += opening
            c.entry_val += opening * f.px
            leg = c.entry_legs.setdefault(dec_no, [f.time, Decimal(0), Decimal(0)])
            leg[1] += opening
            leg[2] += opening * f.px
            c.fees += f.fee * (opening / f.sz)
            c.n_fills += 1  # a flip fill counts for both the closed and the new cycle
        if cur is not None:
            c = out.cycles[cur]
            c.max_size = max(c.max_size, abs(after))
            c.max_notional = max(c.max_notional, abs(after) * f.px)
            dec.cycle = cur
        pos = after
    close_decision()
    return out


def _legs_json(legs: dict) -> str:
    return json.dumps([[t.isoformat(), float(val / size), float(size)] for t, size, val in legs.values() if size])


def rebuild_wallet(session: Session, wallet_id: int, now: datetime) -> dict:
    """`now` is the as-of time for open cycles (the present, or the last archived fill if not synced)."""
    rows = session.execute(text("""
        SELECT coin, time, tid, side, sz, px, coalesce(start_position, 0), coalesce(closed_pnl, 0), coalesce(fee, 0)
        FROM fill WHERE wallet_id = :w AND coin NOT LIKE '@%' AND coin NOT LIKE '%/%'
        ORDER BY coin, time, tid"""), {"w": wallet_id}).all()
    by_coin: dict[str, list[FillRow]] = {}
    for coin, *rest in rows:
        by_coin.setdefault(coin, []).append(FillRow(*rest))
    session.execute(text("DELETE FROM trade_decision WHERE wallet_id = :w"), {"w": wallet_id})
    session.execute(text("DELETE FROM position_cycle WHERE wallet_id = :w"), {"w": wallet_id})
    totals = {"coins": len(by_coin), "cycles": 0, "decisions": 0, "breaks": 0}
    for coin, fills in by_coin.items():
        r = reconstruct(fills)
        ids: list[int] = []
        for c in r.cycles:
            hours = c.holding_hours(now)
            ids.append(session.execute(text("""
                INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
                  scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions, entries, exits,
                  realized_pnl, fees)
                VALUES (:w, :coin, :v, :dir, :o, :cl, :st, :cmp, :scale, :h, :ep, :xp, :ms, :mn, :nf, :nd, CAST(:entries AS jsonb), CAST(:exits AS jsonb), :pnl, :fees)
                RETURNING id"""), {
                "w": wallet_id, "coin": coin, "v": RECON_VERSION, "dir": "long" if c.direction > 0 else "short",
                "o": c.opened_at, "cl": c.closed_at, "st": c.status, "cmp": c.complete, "scale": scale_for(hours),
                "h": hours, "ep": c.entry_val / c.entry_sz if c.entry_sz else None,
                "xp": c.exit_val / c.exit_sz if c.exit_sz else None, "ms": c.max_size, "mn": c.max_notional,
                "nf": c.n_fills, "nd": c.n_decisions, "pnl": c.realized_pnl, "fees": c.fees,
                "entries": _legs_json(c.entry_legs), "exits": _legs_json(c.exit_legs)}).scalar_one())
        if r.decisions:
            session.execute(text("""
                INSERT INTO trade_decision (wallet_id, cycle_id, coin, version, kind, side, started_at, ended_at,
                  n_fills, size, notional, vwap, pos_before, pos_after, realized_pnl, fee)
                VALUES (:w, :cid, :coin, :v, :kind, :side, :sa, :ea, :nf, :sz, :nt, :vwap, :pb, :pa, :pnl, :fee)"""),
                [{"w": wallet_id, "cid": ids[d.cycle] if d.cycle is not None else None, "coin": coin,
                  "v": RECON_VERSION, "kind": d.kind, "side": d.side, "sa": d.started_at, "ea": d.ended_at,
                  "nf": d.n_fills, "sz": d.size, "nt": d.notional, "vwap": d.notional / d.size if d.size else 0,
                  "pb": d.pos_before, "pa": d.pos_after, "pnl": d.realized_pnl, "fee": d.fee}
                 for d in r.decisions])
        totals["cycles"] += len(r.cycles)
        totals["decisions"] += len(r.decisions)
        totals["breaks"] += r.breaks
    session.execute(text("UPDATE fill_sync SET reconstructed_at = :t, recon_breaks = :b WHERE wallet_id = :w"),
                    {"t": datetime.now(UTC), "b": totals["breaks"], "w": wallet_id})
    return totals


def rebuild_stale(sf: sessionmaker[Session], limit: int = 200) -> dict:
    """Rebuild wallets whose fills changed since their last reconstruction.

    For a wallet whose sync has not reached the present, open cycles are measured up to the last archived
    fill (as_of) instead of now: they are where our data stops, so their holding time is a lower bound.
    """
    now = datetime.now(UTC)
    with sf() as s:
        rows = s.execute(text("""
            SELECT f.wallet_id, f.status, f.latest_fill_at FROM fill_sync f
            WHERE f.n_fills > 0 AND f.last_synced_at IS NOT NULL
              AND (f.reconstructed_at IS NULL OR f.reconstructed_at < f.last_synced_at
                   OR EXISTS (SELECT 1 FROM position_cycle c WHERE c.wallet_id = f.wallet_id AND c.version <> :v))
            ORDER BY f.last_synced_at LIMIT :n"""), {"n": limit, "v": RECON_VERSION}).all()
    totals = {"wallets": 0, "cycles": 0, "decisions": 0, "breaks": 0}
    for wid, status, latest in rows:
        with sf() as s:
            r = rebuild_wallet(s, wid, now if status == "synced" else latest)
            s.commit()
        totals["wallets"] += 1
        for k in ("cycles", "decisions", "breaks"):
            totals[k] += r[k]
    return totals


def current_positions(session: Session, wallet_id: int) -> dict[str, Decimal]:
    """Position after the last archived fill per coin (for reconciliation against clearinghouseState)."""
    rows = session.execute(text("""
        SELECT DISTINCT ON (coin) coin, pos_after FROM trade_decision WHERE wallet_id = :w
        ORDER BY coin, ended_at DESC"""), {"w": wallet_id}).all()
    return {c: p for c, p in rows if _sign(p) != 0}
