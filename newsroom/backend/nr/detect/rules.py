"""Detection rules d1 (docs/05-detection-rules.md): transparent thresholds over one position change at a time.

Nothing here judges whether a story is worth telling; it only proposes candidates. Scoring and the credibility gate
run once the trader's context is fetched (nr/facts/build.py).
"""

from dataclasses import dataclass
from decimal import Decimal

from nr.normalize.episodes import Change, notional

RULE_VERSION = "d1"
# one event per episode for these; increases merge into the open event until it is published
MERGING = ("whale_add", "underwater_add")
CLASSES = ("whale_open", "flip", "whale_add", "underwater_add", "whale_close", "liquidation", "near_liquidation",
           "notable_trader_move")


@dataclass
class Candidate:
    event_class: str
    level: int
    trader_id: int
    trader_key: str
    episode_id: int
    venue: str
    coin: str
    direction: str
    source_event_id: int | None
    liquidation_status: str = "none"


def tier_level(ntl: Decimal, tiers: list) -> int:
    return sum(1 for t in tiers if ntl >= Decimal(str(t)))


def suspected_liquidation(side: str, mark: Decimal | None, liq: Decimal | None, tol: Decimal) -> bool:
    """AlphaScope's rule (api/market.py LIQ_TOLERANCE): the position disappeared at, or beyond, the liquidation price
    it last reported. Inferred, never the exchange's liquidation record."""
    if not mark or not liq or mark <= 0 or liq <= 0:
        return False
    return mark <= liq * (1 + tol) if side == "long" else mark >= liq * (1 - tol)


def evaluate(ch: Change, cfg: dict, liq_tol: Decimal, star_floor: Decimal | None = None) -> list[Candidate]:
    """`star_floor`: the trader is on the star list, every move from this size on is news (docs/16)."""
    e, ep = ch.event, ch.episode
    venue, kind, mark = e["venue"], e["kind"], e["mark_px"]

    def usd(k: str) -> Decimal:
        v = cfg[k]
        v = Decimal(str(v.get(venue, v.get("okx")) if isinstance(v, dict) else v))
        return min(v, star_floor) if star_floor is not None else v
    tiers = cfg["tiers_usd"]

    def cand(cls: str, level: int = 0, ep_id: int | None = None, liq: str = "none", side: str | None = None):
        return Candidate(cls, level, e["trader_id"], e["trader_key"], ep_id or ep["id"], venue, e["coin"],
                         side or ep["direction"], e["id"], liq)

    after = notional(e["szi_after"], mark)
    out: list[Candidate] = []
    if kind == "open" and after >= usd("open_usd"):
        out.append(cand("whale_open", tier_level(after, tiers)))
    elif kind == "flip" and after >= usd("open_usd"):
        out.append(cand("flip", tier_level(after, tiers)))
    elif kind == "increase":
        before = notional(e["szi_before"], mark)
        lv_before, lv_after = tier_level(before, tiers), tier_level(after, tiers)
        if ep["added_notional"] >= usd("add_usd") or (lv_after > lv_before and lv_after >= 1):
            out.append(cand("whale_add", lv_after))
        entry = e["entry_px"]
        underwater = mark is not None and entry is not None and (
            (ep["direction"] == "long" and mark < entry) or (ep["direction"] == "short" and mark > entry))
        if underwater and after >= usd("underwater_add_usd"):
            out.append(cand("underwater_add", tier_level(after, tiers)))
    elif kind == "close":
        if e["notional_change"] >= usd("liq_usd") and suspected_liquidation(e["side"], mark, e["liq_px"], liq_tol):
            out.append(cand("liquidation", liq="suspected", side=e["side"]))
        elif e["notional_change"] >= usd("close_usd"):
            out.append(cand("whale_close", side=e["side"]))
    # first_seen never yields an open-type event: the position was not opened at that moment
    if not out and kind in ("open", "flip", "increase"):
        size = after if kind != "increase" else e["notional_change"]
        if size >= usd("notable_usd"):
            out.append(cand("notable_trader_move"))  # kept only if the trader has a verified record (build step)
    return out
