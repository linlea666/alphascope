"""Forward-only copy simulation (S3): target-position mirroring at live prices.

Every live cycle, for each running simulation:
1. Followed coins: by default only positions the leader opens (or flips) after the simulation starts are copied;
   positions already open at the start are ignored until the leader closes or flips them (copy_existing=True
   mirrors them immediately instead).
2. Target size per coin from the sizing mode (equity_ratio / fixed_multiple / fixed_cap) x size_factor, scaled down
   so gross exposure stays within max_leverage x equity.
3. Orders = target - current; small rebalances (< min_trade_usd or < rebalance_pct of the target) are skipped,
   closes and flips always go through. With delay_seconds the order waits and fills at the later mid.
4. Fill at mid +/- slippage_bps, taker fee_bps on notional; realized PnL on the closed part; average entry on adds.
5. Hourly funding at Hyperliquid's live rate; maintenance-margin liquidation; optional drawdown stop.
6. leverage_mode: "account" = one cross-margined account (gross <= max_leverage x equity, account liquidation stops the
   simulation); "leader" = each position isolated at the leader's leverage on that coin (capped by max_leverage and the
   coin's maximum): margins must fit in equity, and a position whose isolated liquidation price is crossed is
   liquidated alone while the simulation keeps running.
7. When the leader's position disappears at or beyond their reported liquidation price, the close is attributed to a
   (inferred) leader liquidation; in "leader" mode our copy is liquidated with it.
8. Per position the worst / best unrealized PnL while held is tracked; the closing trade records the worst (MAE).
No order is ever sent anywhere.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

ZERO = Decimal(0)
EQUITY_SNAPSHOT_EVERY = timedelta(minutes=5)
DEFAULT_COIN_MAX_LEVERAGE = 20
# Highest leverage a simulation may be set to (OKX allows up to 125x). In "leader" mode a cap this high means "follow
# the leader's leverage": only the coin's own maximum applies, so margins and liquidation prices match the leader's.
MAX_LEVERAGE = 125


def _sign(x: Decimal) -> int:
    return 0 if x == 0 else (1 if x > 0 else -1)


@dataclass(frozen=True)
class Params:
    sizing: str = "equity_ratio"
    size_factor: Decimal = Decimal(1)
    cap_per_coin: Decimal | None = None
    max_leverage: Decimal = Decimal(10)
    coins_include: tuple[str, ...] = ()
    coins_exclude: tuple[str, ...] = ()
    follow_adjust: bool = True
    fee_bps: Decimal = Decimal("4.5")
    slippage_bps: Decimal = Decimal(3)
    min_trade_usd: Decimal = Decimal(10)
    rebalance_pct: Decimal = Decimal(5)
    leverage_mode: str = "account"  # account | leader


LEADER_LIQ_TOLERANCE = Decimal("0.005")  # the leader's position vanished within 0.5% of their liquidation price


@dataclass
class Fill:
    size_after: Decimal
    entry_after: Decimal
    px: Decimal
    fee: Decimal
    slippage_cost: Decimal
    realized: Decimal


def allowed(p: Params, coin: str) -> bool:
    return (not p.coins_include or coin in p.coins_include) and coin not in p.coins_exclude


def coin_leverage(p: Params, coin: str, leader_lev: dict[str, int | None], max_lev: dict[str, int]) -> Decimal:
    """Leverage of an isolated copy: the leader's on that coin, capped by our max leverage and the coin's maximum."""
    cap = Decimal(max_lev.get(coin) or DEFAULT_COIN_MAX_LEVERAGE)
    # leader's leverage unknown: our cap, but no more than the unknown-coin default (a "follow" cap of 125x must not
    # turn an unknown into the coin's maximum)
    lev = Decimal(leader_lev.get(coin) or min(p.max_leverage, Decimal(DEFAULT_COIN_MAX_LEVERAGE)))
    return max(Decimal(1), min(lev, p.max_leverage, cap))


def isolated_liq_px(entry: Decimal, size: Decimal, lev: Decimal, coin_max_lev: int | None) -> Decimal:
    """Isolated-margin liquidation price: the move that eats the margin down to maintenance (half the initial margin
    at the coin's max leverage, the same rule as `maintenance`)."""
    mmr = Decimal(1) / (2 * Decimal(coin_max_lev or DEFAULT_COIN_MAX_LEVERAGE))
    return entry * (1 - 1 / lev + mmr) if size > 0 else entry * (1 + 1 / lev - mmr)


def target_sizes(p: Params, leader: dict[str, Decimal], leader_equity: Decimal | None, equity: Decimal,
                 mids: dict[str, Decimal], followed: set[str], current: dict[str, Decimal],
                 leader_lev: dict[str, int | None] | None = None,
                 max_lev: dict[str, int] | None = None) -> dict[str, Decimal]:
    """Target position (coin units) for every coin that is followed or currently held.

    equity_ratio is proportional copying: our notional = size_factor x (leader notional / leader equity) x our equity,
    i.e. the same share of the account the leader uses, times the copy ratio (size_factor 1 = 100%)."""
    notional: dict[str, Decimal] = {}
    for coin in set(current) | followed:
        mid = mids.get(coin)
        szi = leader.get(coin, ZERO)
        if mid is None or mid <= 0:
            continue  # no live price: leave the position alone this cycle
        if coin not in followed or not allowed(p, coin) or szi == 0:
            notional[coin] = ZERO
            continue
        lead_ntl = szi * mid
        ratio_ntl = lead_ntl / leader_equity * equity * p.size_factor if leader_equity and leader_equity > 0 else ZERO
        if p.sizing == "fixed_multiple":
            tn = lead_ntl * p.size_factor
        elif p.sizing == "fixed_cap":
            tn = _sign(szi) * min(p.cap_per_coin or ZERO, abs(ratio_ntl))
        else:
            tn = ratio_ntl
        cur = current.get(coin, ZERO)
        if not p.follow_adjust and _sign(cur) == _sign(szi) != 0:
            tn = cur * mid  # follow opens / closes / flips only; keep the size we opened with
        notional[coin] = tn
    if p.leverage_mode == "leader":  # isolated margins (notional / leverage) must fit in equity
        margin = sum(abs(v) / coin_leverage(p, c, leader_lev or {}, max_lev or {}) for c, v in notional.items())
        limit = equity if equity > 0 else ZERO
        scale = (limit / margin) if margin > limit and margin > 0 else Decimal(1)
    else:
        gross = sum(abs(v) for v in notional.values())
        limit = p.max_leverage * equity if equity > 0 else ZERO
        scale = (limit / gross) if gross > limit and gross > 0 else Decimal(1)
    return {c: (v * scale / mids[c]) for c, v in notional.items()}


def plan_orders(p: Params, targets: dict[str, Decimal], current: dict[str, Decimal],
                mids: dict[str, Decimal]) -> dict[str, Decimal]:
    """coin -> size delta worth executing."""
    orders: dict[str, Decimal] = {}
    for coin, tgt in targets.items():
        cur = current.get(coin, ZERO)
        delta = tgt - cur
        if delta == 0:
            continue
        ntl = abs(delta) * mids[coin]
        if tgt == 0 or _sign(tgt) != _sign(cur):  # close or flip (or open): always
            if ntl >= Decimal("0.01"):
                orders[coin] = delta
            continue
        if ntl >= p.min_trade_usd and ntl >= abs(tgt) * mids[coin] * p.rebalance_pct / 100:
            orders[coin] = delta
    return orders


def fill(size: Decimal, entry: Decimal, delta: Decimal, mid: Decimal, p: Params) -> Fill:
    slip = mid * p.slippage_bps / Decimal(10_000)
    px = mid + slip if delta > 0 else mid - slip
    fee = abs(delta) * px * p.fee_bps / Decimal(10_000)
    realized = ZERO
    after = size + delta
    if size != 0 and _sign(delta) != _sign(size):  # closing part
        closed = min(abs(delta), abs(size))
        realized = closed * (px - entry) * _sign(size)
    if after == 0:
        new_entry = ZERO
    elif size == 0 or _sign(after) != _sign(size):  # opened or flipped: the new part's price
        new_entry = px
    elif abs(after) > abs(size):  # added
        new_entry = (abs(size) * entry + abs(delta) * px) / abs(after)
    else:  # reduced
        new_entry = entry
    return Fill(after, new_entry, px, fee, abs(delta) * slip, realized)


def maintenance(positions: dict[str, tuple[Decimal, Decimal]], mids: dict[str, Decimal],
                max_lev: dict[str, int]) -> Decimal:
    """Hyperliquid-style maintenance margin: half the initial margin at the coin's max leverage."""
    return sum((abs(sz) * mids.get(c, e) / (2 * max_lev.get(c, DEFAULT_COIN_MAX_LEVERAGE))
                for c, (sz, e) in positions.items()), ZERO)


def _params(row) -> Params:
    return Params(row.sizing, row.size_factor, row.cap_per_coin, row.max_leverage, tuple(row.coins_include or ()),
                  tuple(row.coins_exclude or ()), row.follow_adjust, row.fee_bps, row.slippage_bps, row.min_trade_usd,
                  row.rebalance_pct, getattr(row, "leverage_mode", None) or "account")


def _record(s: Session, sim_id: int, now: datetime, coin: str, kind: str, side: str | None, size: Decimal,
            mid: Decimal | None, f: Fill | None, after: Decimal, leader: Decimal | None, reason: str,
            funding: Decimal = ZERO, mae: Decimal | None = None) -> None:
    s.execute(text("""
        INSERT INTO sim_trade (sim_id, t, coin, kind, side, size, mid, px, fee, slippage_cost, funding, realized_pnl,
                               position_after, leader_szi, reason, mae)
        VALUES (:s, :t, :c, :k, :side, :size, :mid, :px, :fee, :slip, :fund, :real, :after, :lead, :reason, :mae)"""),
        {"s": sim_id, "t": now, "c": coin, "k": kind, "side": side, "size": abs(size), "mid": mid,
         "px": f.px if f else None, "fee": f.fee if f else ZERO, "slip": f.slippage_cost if f else ZERO,
         "fund": funding, "real": f.realized if f else ZERO, "after": after, "lead": leader, "reason": reason,
         "mae": mae})


def step_simulations(sf: sessionmaker[Session], mids: dict[str, Decimal], funding: dict[str, Decimal] | None = None,
                     max_lev: dict[str, int] | None = None, now: datetime | None = None,
                     venue: str = "hyperliquid") -> dict:
    """Advance every simulation whose leader trades on `venue`, at that venue's prices."""
    now = now or datetime.now(UTC)
    max_lev = max_lev or {}
    stats = {"sims": 0, "trades": 0, "stopped": 0}
    with sf() as s:
        sims = s.execute(text("""
            SELECT c.* FROM copy_sim c JOIN wallet w ON w.id = c.leader_wallet_id
            WHERE c.status IN ('running', 'paused') AND w.venue = :v ORDER BY c.id"""), {"v": venue}).all()
    for sim in sims:
        with sf() as s:
            stats["trades"] += _step_one(s, sim, mids, funding, max_lev, now, stats)
            s.commit()
        stats["sims"] += 1
    return stats


def _step_one(s: Session, sim, mids, funding, max_lev, now: datetime, stats: dict) -> int:
    p = _params(sim)
    state = dict(sim.state or {})
    leader_seen = s.execute(text("SELECT account_value FROM live_account WHERE wallet_id = :w AND error IS NULL"),
                            {"w": sim.leader_wallet_id}).first()
    if leader_seen is None:
        return 0  # leader not polled yet
    leader_equity = leader_seen.account_value
    leader, leader_lev, leader_liq = {}, {}, {}
    for c, z, lev, liq in s.execute(text("""
            SELECT coin, szi, leverage, liquidation_px FROM live_position WHERE wallet_id = :w"""),
            {"w": sim.leader_wallet_id}).all():
        leader[c], leader_lev[c], leader_liq[c] = z, lev, liq
    rows = s.execute(text("""SELECT coin, size, entry_px, leverage, liq_px, margin, worst_upnl, best_upnl
                             FROM sim_position WHERE sim_id = :s"""), {"s": sim.id}).all()
    positions = {r.coin: (r.size, r.entry_px) for r in rows}
    meta = {r.coin: {"lev": r.leverage, "liq": r.liq_px, "margin": r.margin, "worst": r.worst_upnl or ZERO,
                     "best": r.best_upnl or ZERO} for r in rows}
    isolated = p.leverage_mode == "leader"
    cash = sim.cash
    trades = 0

    if not state.get("initialized"):
        state["baseline"] = {c: _sign(z) for c, z in leader.items()}
        state["followed"] = sorted(leader) if sim.copy_existing else []
        state["initialized"] = True
        state["started_at"] = now.isoformat()
    baseline: dict[str, int] = dict(state.get("baseline") or {})
    followed = set(state.get("followed") or [])
    for coin in list(baseline):  # a pre-existing position becomes followable once closed or flipped
        if _sign(leader.get(coin, ZERO)) != baseline[coin]:
            baseline.pop(coin)
            if leader.get(coin, ZERO) != 0:
                followed.add(coin)  # flipped: a fresh position
    for coin, z in leader.items():
        if z != 0 and coin not in baseline:
            followed.add(coin)

    def apply(coin: str, delta: Decimal, reason: str, kind: str = "trade", at: Decimal | None = None) -> None:
        """Trade `delta` at the mid (or at `at`, e.g. an isolated liquidation price) +/- slippage."""
        nonlocal cash, trades
        size, entry = positions.get(coin, (ZERO, ZERO))
        f = fill(size, entry, delta, at if at is not None else mids[coin], p)
        cash += f.realized - f.fee
        m = meta.get(coin) or {"lev": None, "liq": None, "margin": None, "worst": ZERO, "best": ZERO}
        closing = size != 0 and (f.size_after == 0 or _sign(f.size_after) != _sign(size))
        # the worst unrealized PnL of the position being closed, including the price we close at
        mae = min(m["worst"], size * (f.px - entry)) if closing else None
        if f.size_after == 0:
            positions.pop(coin, None)
            meta.pop(coin, None)
            s.execute(text("DELETE FROM sim_position WHERE sim_id = :s AND coin = :c"), {"s": sim.id, "c": coin})
        else:
            positions[coin] = (f.size_after, f.entry_after)
            fresh = size == 0 or _sign(f.size_after) != _sign(size)
            if fresh:
                m = {"lev": None, "liq": None, "margin": None, "worst": ZERO, "best": ZERO}
            if isolated:
                lev = coin_leverage(p, coin, leader_lev, max_lev) if fresh or m["lev"] is None else Decimal(m["lev"])
                m["lev"] = lev
                m["margin"] = abs(f.size_after) * f.entry_after / lev
                m["liq"] = isolated_liq_px(f.entry_after, f.size_after, lev, max_lev.get(coin))
            meta[coin] = m
            s.execute(text("""
                INSERT INTO sim_position (sim_id, coin, size, entry_px, opened_at, leverage, liq_px, margin, worst_upnl,
                                          best_upnl)
                VALUES (:s, :c, :z, :e, :t, :lev, :liq, :mg, :w, :b)
                ON CONFLICT (sim_id, coin) DO UPDATE SET size = EXCLUDED.size, entry_px = EXCLUDED.entry_px,
                  leverage = EXCLUDED.leverage, liq_px = EXCLUDED.liq_px, margin = EXCLUDED.margin,
                  worst_upnl = EXCLUDED.worst_upnl, best_upnl = EXCLUDED.best_upnl,
                  opened_at = CASE WHEN sign(sim_position.size) = sign(EXCLUDED.size) THEN sim_position.opened_at
                                   ELSE EXCLUDED.opened_at END"""),
                {"s": sim.id, "c": coin, "z": f.size_after, "e": f.entry_after, "t": now, "lev": m["lev"],
                 "liq": m["liq"], "mg": m["margin"], "w": m["worst"], "b": m["best"]})
        _record(s, sim.id, now, coin, kind, "buy" if delta > 0 else "sell", delta, mids[coin], f, f.size_after,
                leader.get(coin), reason, mae=mae)
        trades += 1

    def close_all(kind: str) -> None:
        for coin, (size, _) in list(positions.items()):
            if coin in mids:
                apply(coin, -size, kind, kind)

    # hourly funding (longs pay positive rates)
    hour = now.replace(minute=0, second=0, microsecond=0)
    if funding is not None and (sim.last_funding_hour is None or sim.last_funding_hour < hour):
        for coin, (size, _) in positions.items():
            rate, mid = funding.get(coin), mids.get(coin)
            if rate is None or mid is None:
                continue
            pay = size * mid * rate
            cash -= pay
            _record(s, sim.id, now, coin, "funding", None, ZERO, mid, None, size, leader.get(coin), "funding", pay)
        s.execute(text("UPDATE copy_sim SET last_funding_hour = :h WHERE id = :id"), {"h": hour, "id": sim.id})

    stop_requested = state.pop("stop_requested", False)
    # worst / best unrealized while held (MAE / MFE), at this cycle's mids
    for coin, (z, e) in positions.items():
        if coin in mids and coin in meta:
            u = z * (mids[coin] - e)
            meta[coin]["worst"], meta[coin]["best"] = min(meta[coin]["worst"], u), max(meta[coin]["best"], u)
    # the leader's position vanished at / beyond the liquidation price they reported last cycle: inferred liquidation
    prev_liq = state.get("leader_liq") or {}
    leader_liquidated = set()
    for coin, info in prev_liq.items():
        mid = mids.get(coin)
        if leader.get(coin, ZERO) != 0 or mid is None or coin not in positions:
            continue
        liq, side = Decimal(info["px"]), info["side"]
        if (side > 0 and mid <= liq * (1 + LEADER_LIQ_TOLERANCE)) or (side < 0 and mid >= liq * (1 - LEADER_LIQ_TOLERANCE)):
            leader_liquidated.add(coin)
    state["leader_liq"] = {c: {"px": str(leader_liq[c]), "side": _sign(z)} for c, z in leader.items()
                           if z != 0 and leader_liq.get(c)}
    if sim.status == "running" and not stop_requested:
        for coin in sorted(leader_liquidated):
            size = positions[coin][0]
            apply(coin, -size, "leader_liquidated", "liquidation" if isolated else "trade")
        if isolated:  # isolated positions whose own liquidation price was crossed go alone
            for coin, (z, _) in list(positions.items()):
                liq, mid = meta.get(coin, {}).get("liq"), mids.get(coin)
                if liq is not None and mid is not None and ((z > 0 and mid <= liq) or (z < 0 and mid >= liq)):
                    apply(coin, -z, "isolated_liquidation", "liquidation", at=Decimal(liq))
                    if leader.get(coin, ZERO) != 0:  # do not re-enter the same leader position: wait for its next one
                        baseline[coin] = _sign(leader[coin])
                        followed.discard(coin)
    # account liquidation happens at the price move itself, before we could rebalance at these mids
    unreal = sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
    liquidated = not isolated and bool(positions) and cash + unreal <= maintenance(positions, mids, max_lev)
    if sim.status == "running" and not stop_requested and not liquidated:
        current = {c: z for c, (z, _) in positions.items()}
        unreal = sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
        equity = cash + unreal
        targets = target_sizes(p, leader, leader_equity, equity, mids, followed, current, leader_lev, max_lev)
        orders = plan_orders(p, targets, current, mids)
        pending = dict(state.get("pending") or {})
        if sim.delay_seconds:
            due = (now + timedelta(seconds=sim.delay_seconds)).isoformat()
            for coin, delta in orders.items():
                prev = pending.get(coin)
                pending[coin] = {"target": str(targets[coin]), "due": prev["due"] if prev else due}
            for coin in [c for c in pending if c not in orders]:
                pending.pop(coin)  # target reached again before the delayed order was due
            for coin, o in list(pending.items()):
                if datetime.fromisoformat(o["due"]) <= now and coin in mids:
                    delta = Decimal(o["target"]) - positions.get(coin, (ZERO, ZERO))[0]
                    if delta != 0:
                        apply(coin, delta, "follow_delayed")
                    pending.pop(coin)
            state["pending"] = pending
        else:
            for coin, delta in orders.items():
                apply(coin, delta, "follow")

    # mark to market, liquidation, drawdown stop
    unreal = sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
    equity = cash + unreal
    gross = sum(abs(z) * mids.get(c, e) for c, (z, e) in positions.items())
    status, reason = sim.status, None
    if liquidated or (not isolated and positions and equity <= maintenance(positions, mids, max_lev)) \
            or (isolated and equity <= 0):
        close_all("liquidation")
        status, reason = "stopped", "liquidated"
    elif sim.stop_drawdown_pct and equity <= sim.peak_equity * (1 - sim.stop_drawdown_pct / 100):
        close_all("stop")
        status, reason = "stopped", "drawdown"
    elif stop_requested:
        close_all("stop")
        status, reason = "stopped", "manual"
    if status == "stopped":
        stats["stopped"] += 1
        unreal = sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
        equity = cash + unreal
        gross = sum(abs(z) * mids.get(c, e) for c, (z, e) in positions.items())
    for coin in positions:
        if coin in mids:
            m = meta.get(coin) or {"worst": ZERO, "best": ZERO}
            s.execute(text("""UPDATE sim_position SET mark_px = :m, worst_upnl = :w, best_upnl = :b
                              WHERE sim_id = :s AND coin = :c"""),
                      {"m": mids[coin], "w": m["worst"], "b": m["best"], "s": sim.id, "c": coin})
    peak = max(sim.peak_equity, equity)
    snapshot = trades > 0 or status != sim.status or sim.last_equity_at is None or \
        now - sim.last_equity_at >= EQUITY_SNAPSHOT_EVERY
    if snapshot:
        s.execute(text("""
            INSERT INTO sim_equity (sim_id, t, equity, cash, unrealized, gross_exposure, leader_equity)
            VALUES (:s, :t, :eq, :cash, :u, :g, :le) ON CONFLICT DO NOTHING"""),
            {"s": sim.id, "t": now, "eq": equity, "cash": cash, "u": unreal, "g": gross, "le": leader_equity})
    state["baseline"], state["followed"] = baseline, sorted(followed)
    s.execute(text("""
        UPDATE copy_sim SET cash = :cash, peak_equity = :peak, state = CAST(:state AS jsonb), status = :st,
          stop_reason = coalesce(:reason, stop_reason), stopped_at = coalesce(:stopped_at, stopped_at),
          last_equity_at = CASE WHEN CAST(:snap AS boolean) THEN :t ELSE last_equity_at END
        WHERE id = :id"""),
        {"cash": cash, "peak": peak, "state": json.dumps(state), "st": status, "reason": reason, "t": now,
         "snap": snapshot, "id": sim.id, "stopped_at": now if status == "stopped" else None})
    return trades
