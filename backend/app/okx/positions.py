"""OKX position-history records -> okx_position rows -> generic position_cycle / trade_decision rows.

OKX reports whole positions (open time and average price, close time and average price, peak size), not
fills. Each position becomes one cycle with an open and a close decision, so the timing, holder and replay
research built for Hyperliquid fills runs on OKX unchanged, at position granularity: adds and trims inside
a position are invisible and the open average price includes later adds.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.research.positions import scale_for

DERIVE_VERSION = "okx1"
LIQUIDATION_CLOSE_TYPES = {"liquidateClose"}
ZERO = Decimal(0)


def parse_inst(inst_id: str) -> tuple[str, str]:
    """'BTC-USDT-SWAP' -> ('BTC', 'linear'); 'BTC-USD-SWAP' -> ('BTC', 'inverse')."""
    parts = inst_id.split("-")
    if len(parts) < 3 or parts[-1] != "SWAP":
        raise ValueError(f"not a perpetual swap: {inst_id}")
    return parts[0], "inverse" if parts[1] == "USD" else "linear"


def _d(v, default: Decimal | None = ZERO) -> Decimal | None:
    if v is None or v == "":
        return default
    return Decimal(str(v))


def _ms(v) -> datetime:
    return datetime.fromtimestamp(int(v) / 1000, UTC)


def normalize(raw: dict) -> dict:
    """One position-history record in USD and coin units, fees and funding positive when paid.

    Linear contracts: size = contracts x ctVal coins, PnL in the quote currency (USDT/USDC ~ USD).
    Inverse contracts: a contract is ctVal USD, PnL / fee / funding are in the coin and converted at the close.
    closeAmount is everything closed over the position's life (it exceeds openMaxAmount when the trader re-added
    after trimming); OKX computes pnl on it, so it is the traded size of the open and close decisions.
    """
    inst_id = raw["instId"]
    coin, ct_type = parse_inst(inst_id)
    ct_val = _d(raw["ctVal"])
    open_px = _d(raw["openAvgPx"])
    close_px = _d(raw.get("closeAvgPx"), None)
    open_max = abs(_d(raw.get("openMaxAmount")))
    closed = abs(_d(raw.get("closeAmount")))
    if ct_type == "linear":
        max_size, closed_size, usd = open_max * ct_val, closed * ct_val, Decimal(1)
        max_notional = max_size * open_px
    else:
        max_notional = open_max * ct_val
        max_size = max_notional / open_px if open_px else ZERO
        closed_size = closed * ct_val / open_px if open_px else ZERO
        usd = close_px or open_px  # coin-denominated PnL to USD at the close
    pnl = _d(raw.get("pnl")) * usd
    fee = -_d(raw.get("fee")) * usd
    funding = -_d(raw.get("fundingFee")) * usd
    side = raw.get("posSide")
    if side not in ("long", "short"):  # net mode: infer from the sign of PnL against the price move
        move = (close_px or open_px) - open_px
        side = "long" if (move >= 0) == (pnl >= 0) else "short"
    close_type = raw.get("closeType") or ""
    return {
        "pos_id": str(raw["posId"]), "inst_id": inst_id, "coin": coin, "ct_type": ct_type, "direction": side,
        "open_time": _ms(raw["cTime"]), "close_time": _ms(raw["uTime"]),
        "open_avg_px": open_px, "close_avg_px": close_px, "max_size": max_size, "closed_size": closed_size,
        "max_notional": max_notional, "pnl": pnl, "fee": fee, "funding": funding,
        # OKX's own figure includes liquidation penalties that are not in the fee field
        "realized_pnl": _d(raw.get("realizedPnl"), None) * usd if raw.get("realizedPnl") not in (None, "")
        else pnl - fee - funding, "pnl_ratio": _d(raw.get("pnlRatio"), None),
        "lever": _d(raw.get("lever"), None), "mgn_mode": raw.get("mgnMode"), "close_type": close_type,
        "liquidated": close_type in LIQUIDATION_CLOSE_TYPES or str(raw.get("liquidationStatus")) == "1",
    }


UPSERT_SQL = """
    INSERT INTO okx_position (wallet_id, pos_id, inst_id, coin, ct_type, direction, open_time, close_time, open_avg_px,
      close_avg_px, max_size, closed_size, max_notional, pnl, realized_pnl, fee, funding, pnl_ratio, lever, mgn_mode,
      close_type, liquidated, raw, fetched_at)
    VALUES (:wallet_id, :pos_id, :inst_id, :coin, :ct_type, :direction, :open_time, :close_time, :open_avg_px,
      :close_avg_px, :max_size, :closed_size, :max_notional, :pnl, :realized_pnl, :fee, :funding, :pnl_ratio, :lever,
      :mgn_mode, :close_type, :liquidated, CAST(:raw AS jsonb), :fetched_at)
    ON CONFLICT (wallet_id, pos_id) DO UPDATE SET close_time = EXCLUDED.close_time,
      close_avg_px = EXCLUDED.close_avg_px, max_size = EXCLUDED.max_size, closed_size = EXCLUDED.closed_size,
      max_notional = EXCLUDED.max_notional, pnl = EXCLUDED.pnl, realized_pnl = EXCLUDED.realized_pnl,
      fee = EXCLUDED.fee, funding = EXCLUDED.funding, pnl_ratio = EXCLUDED.pnl_ratio, lever = EXCLUDED.lever,
      close_type = EXCLUDED.close_type, liquidated = EXCLUDED.liquidated, raw = EXCLUDED.raw,
      fetched_at = EXCLUDED.fetched_at"""


def upsert_positions(session: Session, wallet_id: int, raws: list[dict], fetched_at: datetime) -> int:
    """Store position-history records; a partClose record is replaced when the same posId closes fully."""
    rows = []
    for raw in raws:
        if raw.get("instType", "SWAP") != "SWAP":
            continue
        rows.append({**normalize(raw), "wallet_id": wallet_id, "raw": json.dumps(raw), "fetched_at": fetched_at})
    if rows:
        session.execute(text(UPSERT_SQL), rows)
    return len(rows)


def derive_wallet(session: Session, wallet_id: int) -> dict:
    """Rebuild this trader's okx1 cycles and decisions from okx_position.

    A partClose record is a position still open after a trim: it becomes an open cycle with only its open
    decision. The cycle's PnL is OKX's realized PnL before fees (funding and liquidation penalties included), so
    cycle realized_pnl - fees equals what OKX reports. Fees are split evenly between the open and close decisions.
    """
    rows = session.execute(text("""
        SELECT * FROM okx_position WHERE wallet_id = :w ORDER BY open_time, pos_id"""), {"w": wallet_id}).mappings().all()
    session.execute(text("DELETE FROM trade_decision WHERE wallet_id = :w AND version = :v"),
                    {"w": wallet_id, "v": DERIVE_VERSION})
    session.execute(text("DELETE FROM position_cycle WHERE wallet_id = :w AND version = :v"),
                    {"w": wallet_id, "v": DERIVE_VERSION})
    ids = session.execute(text("SELECT nextval('position_cycle_id_seq') FROM generate_series(1, :n)"),
                          {"n": len(rows)}).scalars().all() if rows else []
    cycles, decisions = [], []
    for cid, r in zip(ids, rows, strict=True):
        still_open = r["close_type"] == "partClose"
        long = r["direction"] == "long"
        hours = (r["close_time"] - r["open_time"]).total_seconds() / 3600
        size = r["closed_size"] or r["max_size"]
        exits = None if still_open or r["close_avg_px"] is None else \
            [[r["close_time"].isoformat(), float(r["close_avg_px"]), float(size)]]
        cycles.append({
            "id": cid, "w": wallet_id, "coin": r["coin"], "v": DERIVE_VERSION, "dir": r["direction"],
            "o": r["open_time"], "cl": None if still_open else r["close_time"],
            "st": "open" if still_open else "closed", "scale": scale_for(hours), "h": hours, "ep": r["open_avg_px"],
            "xp": None if still_open else r["close_avg_px"], "ms": r["max_size"], "mn": r["max_notional"],
            "nd": 1 if still_open else 2,
            "entries": json.dumps([[r["open_time"].isoformat(), float(r["open_avg_px"]), float(size)]]),
            "exits": json.dumps(exits or []), "pnl": r["realized_pnl"] + r["fee"], "fees": r["fee"]})
        signed = r["max_size"] if long else -r["max_size"]
        half_fee = r["fee"] / 2
        decisions.append({"w": wallet_id, "cid": cid, "coin": r["coin"], "v": DERIVE_VERSION, "kind": "open",
                          "side": "buy" if long else "sell", "sa": r["open_time"], "ea": r["open_time"], "nf": 1,
                          "sz": size, "nt": size * r["open_avg_px"], "vwap": r["open_avg_px"], "pb": ZERO,
                          "pa": signed, "pnl": ZERO, "fee": half_fee})
        if not still_open and r["close_avg_px"] is not None:
            decisions.append({"w": wallet_id, "cid": cid, "coin": r["coin"], "v": DERIVE_VERSION, "kind": "close",
                              "side": "sell" if long else "buy", "sa": r["close_time"], "ea": r["close_time"],
                              "nf": 1, "sz": size, "nt": size * r["close_avg_px"], "vwap": r["close_avg_px"],
                              "pb": signed, "pa": ZERO, "pnl": r["pnl"], "fee": r["fee"] - half_fee})
    if cycles:
        session.execute(text("""
            INSERT INTO position_cycle (id, wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
              scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions, entries, exits,
              realized_pnl, fees)
            VALUES (:id, :w, :coin, :v, :dir, :o, :cl, :st, true, :scale, :h, :ep, :xp, :ms, :mn, 0, :nd,
                    CAST(:entries AS jsonb), CAST(:exits AS jsonb), :pnl, :fees)"""), cycles)
    if decisions:
        session.execute(text("""
            INSERT INTO trade_decision (wallet_id, cycle_id, coin, version, kind, side, started_at, ended_at, n_fills,
              size, notional, vwap, pos_before, pos_after, realized_pnl, fee)
            VALUES (:w, :cid, :coin, :v, :kind, :side, :sa, :ea, :nf, :sz, :nt, :vwap, :pb, :pa, :pnl, :fee)"""),
            decisions)
    session.execute(text("UPDATE okx_trader SET derived_at = :t WHERE wallet_id = :w"),
                    {"t": datetime.now(UTC), "w": wallet_id})
    return {"cycles": len(rows), "decisions": len(decisions)}
