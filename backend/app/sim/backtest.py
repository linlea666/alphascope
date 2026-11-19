"""Historical copy backtest (M4): replay a trader's archived decisions as if we had copied them.

Uses the live engine's sizing, order planning and fill accounting (engine.target_sizes / plan_orders / fill), so a
backtest and a live simulation with the same parameters follow the same rules. Differences, stated in every result:

- prices: at the open of the first bar starting at or after decision time + delay, from the finest candles we hold
  for the coin (1m -> 5m -> 15m -> 1h; Binance USDT-M, then OKX, then Hyperliquid); coarse bars overstate the delay.
  A Hyperliquid decision with no delay fills at the leader's own price (vwap). OKX decision prices are whole-position
  averages (later adds / partial closes included), so OKX always uses candles and all delays compare like with like;
- liquidation is checked hourly at the worst price of the hour (low for longs, high for shorts);
- leader equity: Hyperliquid from the portfolio history (equity_point, last point before the decision); OKX publishes
  no equity history, so its current lead-trading assets are used for the whole window (approx_equity = true); without
  either there is no backtest;
- when the leader's exposure / equity x the copy ratio exceeds max_leverage the copy is capped; capped_share reports
  how often that happened (a high share means the result mostly reflects the cap, not the leader's sizing);
- funding is not modelled; positions the leader already held when the window starts are not chased (as live sims).
"""

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from tempfile import TemporaryFile

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.quant.returns import Point, index_path
from app.sim.engine import ZERO, Params, _sign, coin_leverage, fill, isolated_liq_px, maintenance, plan_orders, target_sizes

BACKTEST_VERSION = "replay-v2"
DELAYS = (0, 30, 300, 1800, 7200)  # seconds: instant, 30 s, 5 min, 30 min, 2 h
FINE = (("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60))
SOURCES = ("binance_um", "okx", "hyperliquid")
MAX_DECISIONS = 20_000
MAX_DAYS = 365
CURVE_POINTS = 120


def _minute(t: datetime) -> int:
    return int(t.timestamp() // 60)


RESIDENT_PRICE_BYTES=128*1024**2


class PriceBook:
    """Candles per coin for one time range, loaded lazily (numpy arrays) and shared across backtests, e.g. by the
    nightly batch over hundreds of traders."""

    def __init__(self, session: Session, since: datetime, until: datetime):
        self.s, self.since, self.until = session, since - timedelta(days=1), until
        self.fine: dict[str, tuple[str, int, np.ndarray, np.ndarray, np.ndarray]] = {}
        self.extremes: dict[str, tuple[np.ndarray,np.ndarray]] = {}
        self.cache_sizes: OrderedDict[str,int] = OrderedDict()
        self.hourly: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}

    def _bars(self, coin: str, interval: str) -> np.ndarray:
        """(minute, open, high, low, close) rows, one per bar; the first source in SOURCES wins a bar."""
        query = text("""
            SELECT DISTINCT ON (t) t, o, h, l, c FROM market_candle
            WHERE coin = :coin AND interval = :iv AND source = ANY(:src) AND t BETWEEN :a AND :b
            ORDER BY t, array_position(CAST(:src AS text[]), source::text)""").execution_options(yield_per=2048)
        chunks=[]
        with self.s.execute(query,{"coin":coin,"iv":interval,"src":list(SOURCES),"a":self.since,"b":self.until}) as rows:
            for part in rows.partitions(2048):
                chunks.append(np.array([(t.timestamp()//60,o,h,low,c) for t,o,h,low,c in part],dtype=float))
        return np.concatenate(chunks) if chunks else np.empty((0,5))

    def _load(self, coin: str) -> None:
        if coin in self.fine:
            if coin in self.cache_sizes:
                self.cache_sizes.move_to_end(coin)
            return
        self.fine[coin] = ("", 0, np.empty(0), np.empty(0), np.empty(0))
        self.extremes[coin]=(np.empty(0),np.empty(0))
        for interval, step in FINE:
            b = self._bars(coin, interval)
            if len(b) >= 2:
                self.fine[coin] = (interval, step, b[:, 0], b[:, 1], b[:, 4])
                self.extremes[coin]=b[:,2],b[:,3]
                break
        h = self._bars(coin, "1h")
        if not len(h):
            interval, step, ts, opens, closes = self.fine[coin]
            if len(ts):  # hourly high / low / close from the finer closes
                hours = ts - ts % 60
                keys, idx = np.unique(hours, return_index=True)
                ends = np.append(idx[1:], len(ts))
                hi,lo=self.extremes[coin]
                h = np.array([(k, opens[a], hi[a:e].max(), lo[a:e].min(), closes[e - 1])
                              for k, a, e in zip(keys, idx, ends)])
        self.hourly[coin] = (h[:, 0], h[:, 2], h[:, 3], h[:, 4]) if len(h) else (np.empty(0),) * 4

        self.cache_sizes[coin] = 40*(len(self.fine[coin][2])+len(h))
        while self.cache_sizes and sum(self.cache_sizes.values())>RESIDENT_PRICE_BYTES:
            evicted,_=self.cache_sizes.popitem(last=False)
            self._spill(evicted)

    def _spill(self,coin:str) -> None:
        """File-backed prices remain available to every subsequent trader without repeating the DB scan.
        TemporaryFile unlinks on close and the mapping lives as long as its price arrays.
        """
        interval,step,ts,opens,closes=self.fine[coin]
        if not len(ts):
            return
        hi,lo=self.extremes[coin]
        with TemporaryFile() as tmp:
            bars=np.memmap(tmp,dtype=float,mode="w+",shape=(5,len(ts)))
            for col,values in zip(bars,(ts,opens,hi,lo,closes)):
                col[:]=values
        self.fine[coin]=(interval,step,bars[0],bars[1],bars[4])
        self.extremes[coin]=(bars[2],bars[3])

    def exposed_hour(self,coin,hour_minute,changed_at):
        """Only completed bars entirely after the position change can contribute an intrahour extreme."""
        full=self.hour(coin,hour_minute)
        start=changed_at.timestamp()/60
        if start<=hour_minute:
            return full
        _,step,ts,_,closes=self.fine[coin]
        hi,lo=self.extremes[coin]
        a,b=np.searchsorted(ts,start),np.searchsorted(ts,hour_minute+60-step,side="right")
        if a<b:
            return float(hi[a:b].max()),float(lo[a:b].min()),float(closes[b-1])
        if full:
            return (full[2],)*3
        return None

    def resolution(self, coin: str) -> str:
        self._load(coin)
        return self.fine[coin][0]

    def execution(self, coin: str, due: datetime) -> tuple[datetime, float] | None:
        """First tradable bar open at or after the delay. Carry its actual timestamp into the replay."""
        self._load(coin)
        _, step, ts, opens, _ = self.fine[coin]
        minute = due.timestamp() / 60
        i = int(np.searchsorted(ts, minute))
        if i == len(ts) or ts[i] - minute > 2 * step:
            return None
        return datetime.fromtimestamp(float(ts[i]) * 60, UTC), float(opens[i])

    def price(self, coin: str, at: datetime) -> float | None:
        """Mark using only the open or completed close visible at this instant."""
        self._load(coin)
        _, step, ts, opens, closes = self.fine[coin]
        minute = at.timestamp() / 60
        i = int(np.searchsorted(ts, minute, side="right")) - 1
        if i < 0 or minute - ts[i] > 360:
            return None
        return float(closes[i] if ts[i] + step <= minute else opens[i])

    def hour(self, coin: str, hour_minute: int) -> tuple[float, float, float] | None:
        self._load(coin)
        ts, hi, lo, cl = self.hourly[coin]
        i = int(np.searchsorted(ts, hour_minute))
        return (float(hi[i]), float(lo[i]), float(cl[i])) if i < len(ts) and ts[i] == hour_minute else None


@dataclass(frozen=True)
class BacktestParams:
    days: int = 30
    capital: float = 10_000
    ratio: float = 1.0  # copy ratio: 1 = 100%
    delay_seconds: int = 30
    leverage_mode: str = "account"
    max_leverage: float = 10
    fee_bps: float = 4.5
    slippage_bps: float = 3

    def engine(self) -> Params:
        return Params(size_factor=Decimal(str(self.ratio)), max_leverage=Decimal(str(self.max_leverage)),
                      fee_bps=Decimal(str(self.fee_bps)), slippage_bps=Decimal(str(self.slippage_bps)),
                      leverage_mode=self.leverage_mode)


def load_inputs(session: Session, wallet_id: int, since: datetime, until: datetime) -> dict:
    """Decisions, the leader-equity function and (OKX) leverage per coin for one trader and window."""
    venue, asset = session.execute(text("""
        SELECT w.venue, coalesce(t.asset, (SELECT r.asset FROM okx_leaderboard_row r JOIN okx_leaderboard_run u
                                            ON u.id = r.run_id WHERE r.wallet_id = w.id
                                            ORDER BY u.business_date DESC LIMIT 1))
        FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id WHERE w.id = :w"""), {"w": wallet_id}).one()
    decisions = session.execute(text("""
        SELECT coin, started_at, ended_at, n_fills, vwap, pos_before, pos_after FROM trade_decision
        WHERE wallet_id = :w AND ended_at >= :a AND ended_at < :b ORDER BY ended_at, id LIMIT :n"""),
        {"w": wallet_id, "a": since, "b": until, "n": MAX_DECISIONS + 1}).all()
    equity: list[tuple[datetime, float]] = []
    if venue == "hyperliquid":
        rows = session.execute(text("""
            SELECT ts, greatest(ts, fetched_at) AS known, account_value FROM equity_point
            WHERE wallet_id=:w AND series IN ('allTime','month') AND ts<=:b AND fetched_at<=:b
            UNION ALL
            SELECT fetched_at, fetched_at, account_value FROM account_snapshot WHERE wallet_id=:w AND fetched_at<=:b
            ORDER BY 2,1"""), {"w": wallet_id, "b": until})
        latest_source = None
        by_publication = {}
        for ts, known, av in rows:
            if latest_source is None or ts >= latest_source:
                by_publication[known] = float(av)
                latest_source = ts
        equity = list(by_publication.items())
    levs: dict[str, list[tuple[datetime, int]]] = {}
    if venue == "okx":
        for coin, t, lever in session.execute(text("""
            SELECT coin, open_time, lever FROM okx_position WHERE wallet_id = :w AND open_time < :b AND lever IS NOT NULL
            ORDER BY open_time"""), {"w": wallet_id, "b": until}).all():
            levs.setdefault(coin, []).append((t, int(float(lever))))
    # coin maximum leverage (maintenance margin) on the trader's own venue: Hyperliquid's from coin_limit (stored by
    # the live loop), OKX's from its instruments; the other venue's figure fills gaps, then the engine default
    okx_lev = {c: int(float(v)) for c, v in session.execute(text("""
        SELECT coin, max(max_lever) FROM okx_instrument WHERE inst_id LIKE '%-USDT-SWAP' AND max_lever IS NOT NULL
        GROUP BY coin""")).all()}
    hl_lev = {c: int(v) for c, v in session.execute(text(
        "SELECT coin, max_leverage FROM coin_limit WHERE venue = 'hyperliquid'")).all()}
    max_lev = {**okx_lev, **hl_lev} if venue == "hyperliquid" else {**hl_lev, **okx_lev}
    return {"venue": venue, "asset": float(asset) if asset else None, "decisions": decisions, "equity": equity,
            "levs": levs, "max_lev": max_lev,
            "equity_source": "history" if equity else "current_assets" if asset else None}


def run(inputs: dict, prices: PriceBook, p: BacktestParams, since: datetime, until: datetime) -> dict:
    decisions = inputs["decisions"]
    if len(decisions) > MAX_DECISIONS:
        return {"ok": False, "reason": "too_many_decisions"}
    if not decisions:
        return {"ok": False, "reason": "no_decisions"}
    eq_pts = inputs["equity"]
    approx = inputs["venue"] != "hyperliquid" or not eq_pts
    if not eq_pts and not inputs["asset"]:
        return {"ok": False, "reason": "no_leader_equity"}
    eq_times = [t for t, _ in eq_pts]

    def leader_equity(t: datetime) -> Decimal | None:
        if eq_pts:
            i = bisect_right(eq_times, t) - 1
            return Decimal(str(eq_pts[i][1])) if i >= 0 else None
        return Decimal(str(inputs["asset"]))

    lev_times={coin:[point[0] for point in xs] for coin,xs in inputs["levs"].items()}

    def leader_lev_at(coin: str, t: datetime) -> int | None:
        xs = inputs["levs"].get(coin)
        if not xs:
            return None
        i = bisect_right(lev_times[coin], t) - 1
        return xs[i][1] if i >= 0 else None

    ep = p.engine()
    isolated = p.leverage_mode == "leader"
    max_lev = inputs["max_lev"]
    cash = Decimal(str(p.capital))
    positions: dict[str, tuple[Decimal, Decimal]] = {}
    meta: dict[str, dict] = {}
    changed_at: dict[str, datetime] = {}
    leader: dict[str, Decimal] = {}
    baseline: dict[str, int] = {}  # leader positions open at the window start (or after our liquidation)
    first_seen: set[str] = set()
    trades = liquidations = capped = sized = 0
    costs = ZERO
    curve: list[tuple[datetime, float]] = [(since, p.capital)]
    peak, mdd = Decimal(str(p.capital)), 0.0
    resolution: dict[str, str] = {}
    stopped = None

    def marks(at: datetime, extra: dict[str, Decimal] | None = None) -> dict[str, Decimal]:
        out = dict(extra or {})
        for c in positions:
            if c not in out:
                px = prices.price(c, at)
                if px:
                    out[c] = Decimal(str(px))
        return out

    def trade(coin: str, delta: Decimal, mid: Decimal, liq_kind: bool = False, at_lev: int | None = None) -> None:
        nonlocal cash, trades, costs, liquidations
        size, entry = positions.get(coin, (ZERO, ZERO))
        f = fill(size, entry, delta, mid, ep)
        cash += f.realized - f.fee
        costs += f.fee + f.slippage_cost
        trades += 1
        liquidations += liq_kind
        if f.size_after == 0:
            positions.pop(coin, None)
            meta.pop(coin, None)
            return
        fresh = size == 0 or _sign(f.size_after) != _sign(size)
        positions[coin] = (f.size_after, f.entry_after)
        if isolated:
            lev = coin_leverage(ep, coin, {coin: at_lev}, max_lev) if fresh or coin not in meta else meta[coin]["lev"]
            meta[coin] = {"lev": lev, "liq": isolated_liq_px(f.entry_after, f.size_after, lev, max_lev.get(coin))}

    def record(at: datetime, mids: dict[str, Decimal]) -> None:
        nonlocal peak, mdd
        eq = cash + sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, float(eq / peak - 1))
        curve.append((at, float(eq)))

    # events: decisions (acted on after the delay) and hourly liquidation checks
    delay = timedelta(seconds=p.delay_seconds)
    events = []
    for decision in decisions:
        due = decision.ended_at + delay
        if due > until:
            continue
        at_own_fill = not p.delay_seconds and inputs["venue"] == "hyperliquid" and decision.n_fills == 1
        quote = (due, decision.vwap) if at_own_fill else prices.execution(decision.coin, due)
        if quote and quote[0] <= until:
            events.append((quote[0], 1, (decision, quote[1], at_own_fill)))
    h = since.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while h <= until:
        events.append((h, 0, None))
        h += timedelta(hours=1)
    events.sort(key=lambda e: (e[0], e[1]))
    for at, kind, d in events:
        if at > until:
            break
        if kind == 0:  # hourly: worst price of the past hour against liquidation
            if not positions:
                continue
            hm = _minute(at) - 60
            worst, close = {}, {}
            for c, (z, _) in positions.items():
                bar = prices.exposed_hour(c, hm,changed_at[c])
                if bar:
                    hi, lo, cl = bar
                    worst[c], close[c] = Decimal(str(lo if z > 0 else hi)), Decimal(str(cl))
            if isolated:
                for c, (z, _) in list(positions.items()):
                    liq = meta.get(c, {}).get("liq")
                    if liq is not None and c in worst and ((z > 0 and worst[c] <= liq) or (z < 0 and worst[c] >= liq)):
                        trade(c, -z, Decimal(liq), liq_kind=True)
                        if leader.get(c, ZERO) != 0:
                            baseline[c] = _sign(leader[c])
            else:
                mids = {**marks(at), **worst}
                eq_worst = cash + sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
                if eq_worst <= maintenance(positions, mids, max_lev):
                    for c, (z, _) in list(positions.items()):
                        trade(c, -z, mids.get(c, positions[c][1]), liq_kind=True)
                    stopped = "liquidated"
                    record(at, mids)
                    break
            record(at, {**marks(at), **close})
            continue
        d, quote_px, own_px = d
        coin = d.coin
        if coin not in first_seen:
            first_seen.add(coin)
            if d.pos_before:  # the leader already held this coin when the window started: not chased (as live sims)
                baseline[coin] = _sign(d.pos_before)
        leader[coin] = d.pos_after
        if coin in baseline and _sign(d.pos_after) != baseline[coin]:
            baseline.pop(coin)
        if coin in baseline:
            continue
        px = Decimal(str(quote_px))
        if px <= 0:
            continue
        resolution.setdefault(coin, "decision" if own_px else prices.resolution(coin))
        mids = marks(at, {coin: px})
        for c in leader:  # coins the leader holds need a price to size against the leader's notional
            if c not in mids and leader[c] != 0 and c not in baseline:
                v = prices.price(c, at)
                if v:
                    mids[c] = Decimal(str(v))
        equity = cash + sum(z * (mids.get(c, e) - e) for c, (z, e) in positions.items())
        followed = {c for c, z in leader.items() if z != 0 and c not in baseline}
        current = {c: z for c, (z, _) in positions.items()}
        lev_now = {c: leader_lev_at(c, d.ended_at) for c in followed}
        leq = leader_equity(d.ended_at)
        if leq and leq > 0 and followed:
            sized += 1
            gross = sum(abs(leader[c]) * mids[c] for c in followed if c in mids)
            capped += gross / leq * ep.size_factor > ep.max_leverage
        targets = target_sizes(ep, {c: z for c, z in leader.items() if c not in baseline}, leq,
                               equity, mids, followed, current, lev_now, max_lev)
        # act on the decision's coin only; other coins rebalance when the leader trades them
        orders = {c: q for c, q in plan_orders(ep, targets, current, mids).items() if c == coin}
        for c, q in orders.items():
            trade(c, q, mids[c], at_lev=lev_now.get(c))
            changed_at[c]=at
        record(at, mids)
    end_mids = marks(until)
    if stopped is None:
        record(until, end_mids)
    final = curve[-1][1]
    leader_ret = inputs.get("leader_ret")
    ret = final / p.capital - 1
    step = max(1, len(curve) // CURVE_POINTS)
    return {
        "ok": True, "version": BACKTEST_VERSION, "basis": "okx_whole_position_approximation" if approx else "archived_venue_replay",
        "approx_equity": approx, "equity_source": inputs.get("equity_source"), "stopped": stopped,
        "capped_share": capped / sized if sized else None,
        "capital": p.capital, "final": final, "ret": ret, "pnl": final - p.capital, "mdd": mdd,
        "trades": trades, "liquidations": liquidations, "costs": float(costs), "n_decisions": len(decisions),
        "leader_ret": leader_ret,
        "retention": ret / leader_ret if leader_ret and abs(leader_ret) >= 0.005 else None,
        "resolution": resolution,
        "curve": [[t.isoformat(), round(v, 2)] for t, v in curve[::step]] + [[curve[-1][0].isoformat(), round(final, 2)]],
    }


def backtest(session: Session, wallet_id: int, p: BacktestParams, now: datetime | None = None,
             prices: PriceBook | None = None, delays: tuple[int, ...] = ()) -> dict:
    """One backtest, optionally with the same run at other delays (delay sensitivity)."""
    if p.days > MAX_DAYS:
        return {"ok": False, "reason": "window_too_long"}
    until = now or datetime.now(UTC)
    since = until - timedelta(days=p.days)
    inputs = load_inputs(session, wallet_id, since, until)
    # the leader's own return over the window: flow-adjusted (Hyperliquid) or realized PnL / assets (OKX, approx)
    inputs["leader_ret"] = _hl_leader_return(session, wallet_id, since, until) if inputs["venue"] == "hyperliquid" \
        else _okx_realized_return(session, wallet_id, since, until, inputs["asset"])
    prices = prices or PriceBook(session, since, until)
    out = run(inputs, prices, p, since, until)
    if not out["ok"]:
        return out
    out["since"], out["until"], out["params"] = since, until, p.__dict__
    if delays:
        curve = []
        for d in delays:
            r = out if d == p.delay_seconds else run(inputs, prices, replace(p, delay_seconds=d), since, until)
            curve.append([d, r["ret"] if r.get("ok") else None])
        out["delay_curve"] = curve
    return out


def _okx_realized_return(session: Session, wallet_id: int, since: datetime, until: datetime,
                         asset: float | None) -> float | None:
    if not asset:
        return None
    pnl = session.execute(text("""
        SELECT coalesce(sum(realized_pnl), 0) FROM okx_position
        WHERE wallet_id = :w AND close_time >= :a AND close_time < :b AND close_type <> 'partClose'"""),
        {"w": wallet_id, "a": since, "b": until}).scalar_one()
    return float(pnl) / asset


def _hl_leader_return(session: Session, wallet_id: int, since: datetime, until: datetime) -> float | None:
    pts: dict[datetime, Point] = {}
    for series in ("allTime", "month"):
        for ts, av, pnl in session.execute(text("""
            SELECT ts, account_value, pnl FROM equity_point WHERE wallet_id = :w AND series = :s AND ts <= :b
            ORDER BY ts"""), {"w": wallet_id, "s": series, "b": until}).all():
            pts[ts] = Point(ts, float(av), float(pnl))
    path = index_path(sorted(pts.values(), key=lambda x: x.ts), since)
    if len(path) < 2 or min(v for _, v in path) <= 0.001:  # a wipeout period (or a flow artifact): no usable return
        return None
    return path[-1][1] - 1
