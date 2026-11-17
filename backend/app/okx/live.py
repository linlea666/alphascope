"""Live polling of OKX lead traders for alerts and copy simulation (called from app.live).

One tickers call gives every USDT swap's price; one position-current call per monitored trader gives their open
positions. Contracts become coin units with the instrument specs; long and short positions on the same coin
(hedge mode) or several isolated positions are netted per coin, which is what a follower would hold.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.alerts.diff import Pos
from app.okx.client import OkxClient, OkxError
from app.okx.positions import parse_inst

log = logging.getLogger(__name__)


def _d(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except ArithmeticError:
        return None


def mids_from_tickers(tickers: list[dict]) -> dict[str, Decimal]:
    """coin -> mid of its USDT-margined swap (bid/ask midpoint, last price if the book side is empty)."""
    out: dict[str, Decimal] = {}
    for t in tickers:
        inst = str(t.get("instId") or "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        bid, ask, last = _d(t.get("bidPx")), _d(t.get("askPx")), _d(t.get("last"))
        px = (bid + ask) / 2 if bid and ask else last
        if px and px > 0:
            out[inst.split("-")[0]] = px
    return out


def parse_current(positions: list[dict], specs: dict[str, tuple[str, Decimal]]) -> tuple[dict[str, Pos], Decimal]:
    """Net open positions per coin in coin units. specs: inst_id -> (ct_type, ct_val)."""
    acc: dict[str, dict] = {}
    for p in positions:
        inst = str(p.get("instId") or "")
        try:
            coin, ct_type = parse_inst(inst)
        except ValueError:
            continue
        ct_type, ct_val = specs.get(inst, (ct_type, _d(p.get("ctVal"))))
        contracts, mark = _d(p.get("pos")), _d(p.get("markPx")) or _d(p.get("avgPx"))
        if not contracts or not ct_val or not mark:
            continue
        side = p.get("posSide")
        signed = abs(contracts) if side == "long" else -abs(contracts) if side == "short" else contracts
        size = signed * ct_val if ct_type == "linear" else signed * ct_val / mark
        a = acc.setdefault(coin, {"szi": Decimal(0), "legs": [], "upl": Decimal(0), "mark": mark})
        a["szi"] += size
        upl = _d(p.get("upl")) or Decimal(0)
        a["upl"] += upl if ct_type == "linear" else upl * mark
        a["legs"].append((abs(size), _d(p.get("avgPx")), _d(p.get("lever")), _d(p.get("liqPx")), p.get("mgnMode")))
    out: dict[str, Pos] = {}
    total = Decimal(0)
    for coin, a in acc.items():
        if a["szi"] == 0:
            continue  # fully hedged: no net exposure to follow
        size, entry, lever, liq, mode = max(a["legs"], key=lambda leg: leg[0])  # the dominant leg describes it
        value = abs(a["szi"]) * a["mark"]
        out[coin] = Pos(a["szi"], entry, int(lever) if lever else None, liq, value, a["upl"], mode)
        total += value
    return out, total


def load_specs(s: Session) -> tuple[dict[str, tuple[str, Decimal]], dict[str, int]]:
    specs, max_lev = {}, {}
    for inst, coin, ct_type, ct_val, lever in s.execute(text(
            "SELECT inst_id, coin, ct_type, ct_val, max_lever FROM okx_instrument")).all():
        specs[inst] = (ct_type, ct_val)
        if inst.endswith("-USDT-SWAP") and lever:
            max_lev[coin] = int(lever)
    return specs, max_lev


async def fetch_funding(client: OkxClient, coins: set[str]) -> dict[str, Decimal]:
    """Hourly-equivalent funding rate per coin (OKX settles every 8h, 4h or 1h; the interval comes from the API)."""
    out: dict[str, Decimal] = {}
    for coin in sorted(coins):
        try:
            data = await client.get("/api/v5/public/funding-rate", {"instId": f"{coin}-USDT-SWAP"})
            row = data[0]
            rate = Decimal(row["fundingRate"])
            hours = (int(row["nextFundingTime"]) - int(row["fundingTime"])) / 3_600_000 if row.get(
                "nextFundingTime") else 8
            out[coin] = rate / Decimal(str(hours if hours > 0 else 8))
        except (OkxError, KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
            log.info("okx funding %s: %s", coin, exc)
    return out


async def poll_okx(sf: sessionmaker[Session], client: OkxClient, liq_risk_pct: float, apply_snapshot,
                   record_error, monitored) -> dict:
    """One OKX cycle. The snapshot / error writers and the monitored-wallet query come from app.live."""
    now = datetime.now(UTC)
    with sf() as s:
        wallets = monitored(s, "okx")
        handles = dict(s.execute(text("""
            SELECT w.id, w.handle FROM wallet w WHERE w.id = ANY(:ids)"""), {"ids": [w for w, _ in wallets]}).all())
        assets = dict(s.execute(text("SELECT wallet_id, asset FROM okx_trader WHERE wallet_id = ANY(:ids)"),
                                {"ids": [w for w, _ in wallets]}).all())
        specs, max_lev = load_specs(s)
    if not wallets:
        return {"wallets": 0, "events": 0, "errors": 0, "mids": {}, "max_lev": max_lev}
    mids = mids_from_tickers(await client.tickers())
    n_events = errors = 0
    for wid, _ in wallets:
        handle = handles.get(wid)
        if not handle:
            continue  # profile not resolved yet (the worker fills uniqueName in)
        try:
            raw = await client.current(handle)
        except Exception as exc:  # noqa: BLE001 — one trader must not stop the cycle
            errors += 1
            record_error(sf, wid, now, exc)
            continue
        after, ntl = parse_current(raw, specs)
        n_events += apply_snapshot(sf, wid, after, assets.get(wid), ntl, mids, liq_risk_pct, now)
    return {"wallets": len(wallets), "events": n_events, "errors": errors, "mids": mids, "max_lev": max_lev}


def sim_coins(sf: sessionmaker[Session]) -> set[str]:
    """Coins held by running OKX-leader simulations or their leaders (funding is fetched for these only)."""
    with sf() as s:
        return set(s.execute(text("""
            SELECT p.coin FROM sim_position p JOIN copy_sim c ON c.id = p.sim_id
            JOIN wallet w ON w.id = c.leader_wallet_id WHERE c.status = 'running' AND w.venue = 'okx'
            UNION SELECT lp.coin FROM live_position lp JOIN copy_sim c ON c.leader_wallet_id = lp.wallet_id
            JOIN wallet w ON w.id = c.leader_wallet_id WHERE c.status = 'running' AND w.venue = 'okx'""")).scalars())


FUNDING_REFRESH = timedelta(hours=1)
