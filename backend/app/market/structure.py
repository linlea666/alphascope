"""Positioning structure of one coin in the observed sample (docs/12 §5): where longs and shorts entered, where
their venue-reported liquidation prices cluster, the nearest liquidations, and how much would be reached by a given
price move. Pure functions over fresh positions; the API supplies the rows and the reference price.

Liquidation prices are only those the venues report; positions without one are counted, never estimated. A long
whose reported liquidation price sits above the reference price (or a short's below) is stale data and is counted
apart instead of being drawn on the wrong side of the price.
"""

from dataclasses import dataclass

LADDER_STEPS = (1, 2, 3, 5, 7, 10, 15, 20)  # % price move
ZONE_PEAK_FRACTION = 0.25  # neighbours with at least this share of the peak bin join its zone
ZONE_MIN_SHARE = 0.05  # zones below this share of the side's notional are not reported
MAX_ZONES = 3
SIGNIFICANT_SHARE = 0.02  # the nearest "significant" liquidation cluster holds >= 2% of the side's known notional


@dataclass
class Row:
    wallet_id: int
    side: int  # +1 long, -1 short
    notional: float
    entry: float | None
    liq: float | None
    lev: int | None
    mode: str | None  # cross | isolated


def _pct(px: float, ref: float) -> float:
    return (px - ref) / ref


def histogram(items: list[tuple[float, Row]], ref: float, span: float, n: int) -> tuple[list[dict], dict]:
    """items: (price, row). Bins over ref x (1 ± span); returns bins and what fell outside."""
    lo, hi = ref * (1 - span), ref * (1 + span)
    width = (hi - lo) / n
    bins = [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "ntl": 0.0, "n": 0, "_px": 0.0, "_lev": 0.0,
             "_lev_ntl": 0.0} for i in range(n)]
    outside = {"positions": 0, "notional": 0.0}
    for px, r in items:
        if not lo <= px < hi:
            outside["positions"] += 1
            outside["notional"] += r.notional
            continue
        b = bins[min(int((px - lo) / width), n - 1)]
        b["ntl"] += r.notional
        b["n"] += 1
        b["_px"] += px * r.notional
        if r.lev:
            b["_lev"] += r.lev * r.notional
            b["_lev_ntl"] += r.notional
    return bins, outside


def find_zones(bins: list[dict], ref: float, total: float) -> list[dict]:
    """Up to MAX_ZONES clusters: the heaviest bin plus contiguous neighbours >= ZONE_PEAK_FRACTION of it."""
    free = [b["ntl"] > 0 for b in bins]
    zones = []
    while len(zones) < MAX_ZONES and total > 0:
        candidates = [i for i, ok in enumerate(free) if ok]
        if not candidates:
            break
        peak = max(candidates, key=lambda i: bins[i]["ntl"])
        floor = bins[peak]["ntl"] * ZONE_PEAK_FRACTION
        a = b = peak
        while a - 1 >= 0 and free[a - 1] and bins[a - 1]["ntl"] >= floor:
            a -= 1
        while b + 1 < len(bins) and free[b + 1] and bins[b + 1]["ntl"] >= floor:
            b += 1
        for i in range(a, b + 1):
            free[i] = False
        part = bins[a:b + 1]
        ntl = sum(x["ntl"] for x in part)
        if ntl / total < ZONE_MIN_SHARE:
            break  # later peaks are smaller still
        lev_ntl = sum(x["_lev_ntl"] for x in part)
        avg_px = sum(x["_px"] for x in part) / ntl
        zones.append({"lo": part[0]["lo"], "hi": part[-1]["hi"], "notional": ntl, "share": ntl / total,
                      "positions": sum(x["n"] for x in part), "avg_px": avg_px,
                      "avg_lev": sum(x["_lev"] for x in part) / lev_ntl if lev_ntl else None,
                      "distance": _pct(avg_px, ref)})
    return zones


def _public(bins: list[dict]) -> list[dict]:
    return [{"lo": b["lo"], "hi": b["hi"], "notional": b["ntl"], "positions": b["n"]} for b in bins]


def _top_share(rows: list[Row], top: int = 5) -> float | None:
    total = sum(r.notional for r in rows)
    if not total:
        return None
    by_wallet: dict[int, float] = {}
    for r in rows:
        by_wallet[r.wallet_id] = by_wallet.get(r.wallet_id, 0.0) + r.notional
    return sum(sorted(by_wallet.values(), reverse=True)[:top]) / total


def entry_side(rows: list[Row], ref: float, span: float, n: int) -> dict:
    """Cost basis of one side: weighted / median entry, zones, share in profit vs underwater, whale share."""
    with_entry = [(r.entry, r) for r in rows if r.entry and r.entry > 0]
    total = sum(r.notional for _, r in with_entry)
    bins, outside = histogram(with_entry, ref, span, n)
    side = rows[0].side if rows else 1
    profit = sum(r.notional for px, r in with_entry if (px < ref if side > 0 else px > ref))
    entries = sorted(px for px, _ in with_entry)
    avg = sum(px * r.notional for px, r in with_entry) / total if total else None
    zones = find_zones(bins, ref, total)
    for z in zones:
        z["in_profit"] = z["avg_px"] < ref if side > 0 else z["avg_px"] > ref
    return {
        "positions": len(rows), "notional": sum(r.notional for r in rows), "with_entry": len(with_entry),
        "avg_entry": avg, "avg_distance": _pct(avg, ref) if avg else None,
        "median_entry": entries[len(entries) // 2] if entries else None,
        "profit_share": profit / total if total else None, "underwater_share": 1 - profit / total if total else None,
        "top5_share": _top_share(rows), "bins": _public(bins), "zones": zones, "outside": outside,
    }


def liq_side(rows: list[Row], ref: float, span: float, n: int, min_lev: int) -> dict:
    """Venue-reported liquidation prices of one side beyond the reference price."""
    side = rows[0].side if rows else 1
    eligible = [r for r in rows if (r.lev or 0) >= min_lev] if min_lev else rows
    known = [r for r in eligible if r.liq and r.liq > 0]
    beyond = [r for r in known if (r.liq < ref if side > 0 else r.liq > ref)]  # the price still has to get there
    crossed = [r for r in known if (r.liq >= ref if side > 0 else r.liq <= ref)]
    total = sum(r.notional for r in beyond)
    bins, outside = histogram([(r.liq, r) for r in beyond], ref, span, n)
    nearest = max(beyond, key=lambda r: r.liq) if side > 0 and beyond else \
        min(beyond, key=lambda r: r.liq) if beyond else None
    # nearest significant cluster: walk bins outward from the price
    ordered = sorted((b for b in bins if (b["hi"] <= ref * 1.000001 if side > 0 else b["lo"] >= ref * 0.999999)),
                     key=lambda b: -b["lo"] if side > 0 else b["lo"])
    cluster = next((b for b in ordered if total and b["ntl"] >= total * SIGNIFICANT_SHARE), None)
    ladder = []
    for step in LADDER_STEPS:
        bound = ref * (1 - step / 100) if side > 0 else ref * (1 + step / 100)
        hit = [r for r in beyond if (r.liq >= bound if side > 0 else r.liq <= bound)]
        ladder.append({"move": -step if side > 0 else step, "notional": sum(r.notional for r in hit),
                       "positions": len(hit)})
    eligible_ntl = sum(r.notional for r in eligible)
    return {
        "positions": len(eligible), "notional": eligible_ntl,
        "known_share": sum(r.notional for r in known) / eligible_ntl if eligible_ntl else None,
        "cross_share": sum(r.notional for r in beyond if r.mode == "cross") / total if total else None,
        "without_liq": {"positions": len(eligible) - len(known),
                        "notional": eligible_ntl - sum(r.notional for r in known)},
        "crossed": {"positions": len(crossed), "notional": sum(r.notional for r in crossed)},
        "beyond_notional": total, "bins": _public(bins), "zones": find_zones(bins, ref, total), "outside": outside,
        "nearest": None if nearest is None else {"wallet_id": nearest.wallet_id, "px": nearest.liq,
                                                 "distance": _pct(nearest.liq, ref), "notional": nearest.notional,
                                                 "leverage": nearest.lev, "mode": nearest.mode},
        "nearest_cluster": None if cluster is None else {
            "lo": cluster["lo"], "hi": cluster["hi"], "notional": cluster["ntl"], "positions": cluster["n"],
            "distance": _pct((cluster["lo"] + cluster["hi"]) / 2, ref)},
        "ladder": ladder,
    }


def build(rows: list[Row], ref: float, entry_span: float = 0.4, liq_span: float = 0.3, bins: int = 60,
          min_lev: int = 0) -> dict:
    longs = [r for r in rows if r.side > 0]
    shorts = [r for r in rows if r.side < 0]
    return {
        "entries": {"long": entry_side(longs, ref, entry_span, bins), "short": entry_side(shorts, ref, entry_span, bins)},
        "liquidations": {"long": liq_side(longs, ref, liq_span, bins, min_lev),
                         "short": liq_side(shorts, ref, liq_span, bins, min_lev), "min_leverage": min_lev},
    }
