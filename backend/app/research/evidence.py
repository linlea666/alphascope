"""Feature comparison at a shared retrospective data vintage. Only pre-event flows enter the distance."""

from datetime import timedelta
from math import sqrt
from statistics import mean, pstdev

from sqlalchemy import select

from app.models import ResearchEvent
from app.research.replay import event_info, summarize, wallet_flows


def unit_vector(flow):
    if not flow["fills"] or flow["wallets"] < 3 or flow["net"] is None or flow["top1_share"] is None:
        return None
    keys = ("long_add", "long_cut", "short_add", "short_cut")
    gross = sum(flow[k] for k in keys)
    if not gross:
        return None
    return [float(flow[k] / gross) for k in keys] + [float(flow["top1_share"])]


def similar(s, event, vintage, hours, cross_coin=False, mode="retrospective"):
    start = event.start_at - timedelta(hours=hours)
    cutoff = min(vintage, event.start_at) if mode == "as_observed" else vintage
    query = select(ResearchEvent).where(ResearchEvent.end_at < start, ResearchEvent.available_at <= cutoff)
    if not cross_coin:
        query = query.where(ResearchEvent.coin == event.coin)
    candidates = list(
        s.scalars(query.order_by(ResearchEvent.end_at.desc(), ResearchEvent.revision.desc()).limit(40))
    )
    anchor = unit_vector(summarize(wallet_flows(s, event.coin, event.start_at, hours, cutoff)))
    xs = []
    for e in candidates if anchor is not None else []:
        if any(
            e.start_at - timedelta(hours=hours) <= other.end_at
            and other.start_at - timedelta(hours=hours) <= e.end_at
            or e.cluster_key == other.cluster_key
            for other, _ in xs
        ):
            continue
        vec = unit_vector(
            summarize(
                wallet_flows(
                    s, e.coin, e.start_at, hours, min(cutoff, e.start_at) if mode == "as_observed" else cutoff
                )
            )
        )
        if vec:
            xs.append((e, vec))
        if len(xs) >= 16:
            break
    if len(xs) < 3:
        return {"matches": [], "n": len(xs), "basis": "insufficient_archived_flows"}
    scales = [pstdev(col) for col in zip(*(v for _, v in xs))]
    matches = []
    for e, v in xs:
        d = [((a - b) / sd) ** 2 for a, b, sd in zip(anchor, v, scales) if sd > 0]
        if d:
            matches.append(
                {
                    "event": event_info(e),
                    "distance": sqrt(mean(d)),
                    "features": len(d),
                    "event_return": float(e.end_px / e.start_px - 1),
                    "opposite": e.direction != event.direction,
                }
            )
    matches.sort(key=lambda x: x["distance"])
    return {
        "matches": matches[:5],
        "counterexample": next((m for m in matches if m["opposite"]), None),
        "n": len(xs),
        "basis": mode + "_standardized_pre_event_flows",
    }
