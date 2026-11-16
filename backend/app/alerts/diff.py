"""Turn two consecutive position snapshots of a wallet into position events (pure functions)."""

from dataclasses import dataclass, field
from decimal import Decimal

EVENT_KINDS = ("open", "increase", "decrease", "close", "flip", "leverage", "liq_risk")
ZERO = Decimal("1e-12")


@dataclass(frozen=True)
class Pos:
    szi: Decimal
    entry_px: Decimal | None = None
    leverage: int | None = None
    liquidation_px: Decimal | None = None
    position_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    leverage_type: str | None = None


@dataclass
class Event:
    coin: str
    kind: str
    szi_before: Decimal
    szi_after: Decimal
    px: Decimal | None
    details: dict = field(default_factory=dict)

    @property
    def notional_change(self) -> Decimal | None:
        if self.px is None:
            return None
        return abs(self.szi_after - self.szi_before) * self.px


def _sign(x: Decimal) -> int:
    return 0 if abs(x) <= ZERO else (1 if x > 0 else -1)


def size_event(before: Decimal, after: Decimal) -> str | None:
    b, a = _sign(before), _sign(after)
    if b == a == 0 or before == after:
        return None
    if b == 0:
        return "open"
    if a == 0:
        return "close"
    if a != b:
        return "flip"
    return "increase" if abs(after) > abs(before) else "decrease"


def near_liquidation(pos: Pos, mid: Decimal | None, pct: float) -> bool:
    if _sign(pos.szi) == 0 or pos.liquidation_px is None or mid is None or mid <= 0:
        return False
    return abs(mid - pos.liquidation_px) / mid * 100 <= Decimal(str(pct))


def diff(before: dict[str, Pos], after: dict[str, Pos], mids: dict[str, Decimal], liq_risk_pct: float,
         was_at_risk: dict[str, bool]) -> tuple[list[Event], dict[str, bool]]:
    """Events between snapshots, plus the new at-risk flags (a liq_risk event fires only on entering risk)."""
    events: list[Event] = []
    risk: dict[str, bool] = {}
    for coin in sorted(set(before) | set(after)):
        b = before.get(coin, Pos(Decimal(0)))
        a = after.get(coin, Pos(Decimal(0)))
        mid = mids.get(coin)
        kind = size_event(b.szi, a.szi)
        if kind:
            events.append(Event(coin, kind, b.szi, a.szi, mid, {
                "entry_px": str(a.entry_px) if a.entry_px is not None else None,
                "leverage": a.leverage, "liquidation_px": str(a.liquidation_px) if a.liquidation_px else None}))
        elif _sign(a.szi) and b.leverage and a.leverage and a.leverage != b.leverage:
            events.append(Event(coin, "leverage", b.szi, a.szi, mid,
                                {"leverage_before": b.leverage, "leverage_after": a.leverage}))
        at_risk = near_liquidation(a, mid, liq_risk_pct)
        risk[coin] = at_risk
        if at_risk and not was_at_risk.get(coin, False):
            events.append(Event(coin, "liq_risk", a.szi, a.szi, mid,
                                {"liquidation_px": str(a.liquidation_px), "distance_pct": float(
                                    abs(mid - a.liquidation_px) / mid * 100) if mid else None}))
    return events, risk


def parse_clearinghouse(data: dict) -> tuple[dict[str, Pos], Decimal | None, Decimal | None]:
    out: dict[str, Pos] = {}
    for ap in data.get("assetPositions", []):
        p = ap["position"]
        lev = p.get("leverage") or {}
        out[p["coin"]] = Pos(
            szi=Decimal(p["szi"]),
            entry_px=Decimal(p["entryPx"]) if p.get("entryPx") else None,
            leverage=lev.get("value"),
            leverage_type=lev.get("type"),
            liquidation_px=Decimal(p["liquidationPx"]) if p.get("liquidationPx") else None,
            position_value=Decimal(p["positionValue"]) if p.get("positionValue") else None,
            unrealized_pnl=Decimal(p["unrealizedPnl"]) if p.get("unrealizedPnl") else None,
        )
    ms = data.get("marginSummary") or {}
    av = Decimal(ms["accountValue"]) if ms.get("accountValue") else None
    ntl = Decimal(ms["totalNtlPos"]) if ms.get("totalNtlPos") else None
    return out, av, ntl
