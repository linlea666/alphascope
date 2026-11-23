"""Fact packets fp.v1 (docs/06-facts-and-guard.md): every number a story may use, computed here from feed payloads.

Each fact names its source path and carries display strings; wording constraints travel with the packet so the
claims guard can enforce them. Facts that coverage cannot support are left out, never estimated: no cycle statistics
without a complete fill archive, no account ratio for aggregate accounts, no exact open time from a poll window.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from nr.facts import fmt

SCHEMA = "fp.v1"
BUILDER_VERSION = "facts-1"
SCORE_VERSION = "s1"
HOLDING_CLASSES = ("whale_open", "flip", "whale_add", "underwater_add", "near_liquidation", "notable_trader_move")
MAJOR_COINS = ("BTC", "ETH", "SOL", "HYPE")
# values that move with the mark price between polls: they never make a new packet version on their own
# (sizes and notionals are not here: a 10% move in either is material, whatever caused it)
VOLATILE = {"position.mark_px", "position.unrealized_pnl_usd", "position.liq_distance", "position.observed_at",
            "trader.account_value_usd"}
CONSTRAINT_TEXT = {
    "OBSERVED_SAMPLE": "Data covers AlphaScope's observed sample of traders, not the whole exchange.",
    "NO_IDENTITY": "Who controls the account is unknown; do not guess or imply an identity.",
    "NOTIONAL_NOT_MARGIN": "Notional value is position size x price, not the trader's own capital or margin.",
    "PNL_UNREALIZED": "Profit or loss on an open position is unrealized (paper); never call it realized or locked in.",
    "TIME_WINDOW_ONLY": "The change happened between two polls; only the window is known, not an exact time.",
    "FIRST_SEEN_NOT_OPEN": "The position was already open when first observed; its opening time is unknown.",
    "LIQ_SUSPECTED_ONLY": "The liquidation is inferred from prices, not confirmed by an exchange record.",
    "PNL_ESTIMATED_FROM_MARK": "Closing P&L is estimated from the mark price at observation, not from fills.",
    "ACCOUNT_RATIO_UNAVAILABLE": "Account value is unreliable for this account; do not relate size to account value.",
    "HISTORY_UNAVAILABLE": "The trading history archive is incomplete; make no claims about past record or streaks.",
    "OKX_LEAD_ASSETS": "OKX assets are the lead-trading account only, not the trader's whole capital.",
}


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def T(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def short_ref(trader_key: str) -> str:
    return f"OKX·{trader_key[-6:]}" if trader_key.startswith("okx:") else f"{trader_key[:6]}…{trader_key[-4:]}"


def clean_name(name: str | None) -> str | None:
    """External text (a venue nickname) only ever travels as data: short, no markup, no control characters."""
    if not name:
        return None
    s = "".join(ch for ch in str(name) if ch.isprintable() and ch not in "<>{}`")
    return s.strip()[:64] or None


@dataclass
class Built:
    content: dict
    gate_status: str
    blocking: list[dict]
    score: Decimal
    breakdown: dict
    record_strong: bool
    liquidation_status: str
    facts: dict = field(default_factory=dict)


class Facts:
    def __init__(self):
        self.items: list[dict] = []
        self.constraints: set[str] = set()

    def add(self, fid: str, kind: str, value, display: dict, source: str, **extra) -> None:
        v = format(value, "f") if isinstance(value, Decimal) else value
        if isinstance(v, datetime):
            v = v.isoformat()
        self.items.append({"id": fid, "kind": kind, "value": v, "display": display, "source": source, **extra})

    def usd(self, fid, kind, v, source, **x):
        if v is not None:
            self.add(fid, kind, v, fmt.usd(v), source, unit="USD", **x)

    def get(self, fid: str) -> dict | None:
        return next((f for f in self.items if f["id"] == fid), None)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def _strong_record(ctx: dict) -> tuple[bool, list[str]]:
    q = ctx["quality"]
    reasons = [f"style {t['style']} verified" for t in q["styles"] if t["status"] == "verified"]
    for t in q["timing"]:
        if t["status"] == "scored" and t["q_value"] is not None and D(t["q_value"]) <= Decimal("0.1") \
                and (t["n_scored"] or 0) >= 30:
            reasons.append(f"timing {t['scale']}/{t['window']} q={D(t['q_value']):.3f} n={t['n_scored']}")
    if q["copy"] and q["copy"]["grade"] == "A":
        reasons.append("copy grade A")
    return bool(reasons), reasons


def build(event: dict, episode: dict, source_events: list[dict], ctx: dict | None, source_blockers: list[dict],
          now: datetime, cfg: dict) -> Built:
    f = Facts()
    blocking = list(source_blockers)
    cls, coin, venue = event["event_class"], event["coin"], event["venue"]
    trigger = source_events[-1] if source_events else None
    f.constraints |= {"OBSERVED_SAMPLE", "NOTIONAL_NOT_MARGIN"}
    if cls != "near_liquidation" and (trigger is None or trigger["mark_px"] is None):
        blocking.append({"code": "MISSING_PRICE", "detail": "no mark price on the triggering observation"})

    # --- what changed (from the radar events this news event is made of)
    if trigger:
        exact = all(e["time_precision"] == "exact" for e in source_events)
        if exact:
            f.add("change.time", "time", T(trigger["event_time"]), fmt.when(T(trigger["event_time"])), "source_event.event_time")
        else:
            starts = [T(e["window_start"]) for e in source_events if e["window_start"] is not None]
            a, b = (min(starts) if starts else None), max(T(e["observed_at"]) for e in source_events)
            f.add("change.window", "time_window", {"from": a.isoformat() if a else None, "to": b.isoformat()},
                  fmt.window(a, b), "source_event.window_start..observed_at", label="observed_between")
            f.constraints.add("TIME_WINDOW_ONLY")
        moved = sum((D(e["notional_change"]) for e in source_events), Decimal(0))
        f.usd("change.notional_usd", "notional", moved, "source_event.notional_change",
              derivation="sum of |size change| x mark price at observation")
        first = source_events[0]
        f.add("change.size_before", "size", abs(D(first["szi_before"])), fmt.size(D(first["szi_before"]), coin),
              "source_event.szi_before", unit=coin)
        f.add("change.size_after", "size", abs(D(trigger["szi_after"])), fmt.size(D(trigger["szi_after"]), coin),
              "source_event.szi_after", unit=coin)
        if trigger["mark_px"] is not None:
            f.add("change.mark_px", "price", D(trigger["mark_px"]), fmt.price(D(trigger["mark_px"])),
                  "source_event.mark_px", unit="USD")
        if len(source_events) > 1:
            f.add("change.observations", "count", len(source_events), fmt.count(len(source_events)), "source_event")

    # --- the episode (how much we know about its start)
    if episode["start_kind"] in ("first_seen", "reconstructed"):
        f.add("episode.first_seen_at", "time", T(episode["start_to"]), fmt.when(T(episode["start_to"])),
              "position_episode.start_to", label="first_observed")
        f.constraints.add("FIRST_SEEN_NOT_OPEN")
        if cls in ("whale_open", "flip"):
            blocking.append({"code": "FIRST_SEEN_ONLY", "detail": "an open needs an observed opening"})
    elif episode["start_precision"] == "exact":
        f.add("episode.opened_at", "time", T(episode["start_to"]), fmt.when(T(episode["start_to"])),
              "position_episode.start_to (venue-reported)")
    else:
        a, b = T(episode["start_from"]), T(episode["start_to"])
        f.add("episode.opened_window", "time_window", {"from": a.isoformat() if a else None, "to": b.isoformat()},
              fmt.window(a, b), "position_episode.start_from..start_to", label="opened_between")
        f.constraints.add("TIME_WINDOW_ONLY")
    if episode["n_increases"]:
        f.usd("episode.added_notional_usd", "notional", D(episode["added_notional"]), "position_episode.added_notional",
              derivation="sum of observed increases x mark at each observation")
        f.add("episode.increases", "count", episode["n_increases"], fmt.count(episode["n_increases"]),
              "position_episode.n_increases")
    f.usd("episode.peak_notional_usd", "notional", D(episode["peak_notional"]), "position_episode.peak_notional")

    record_strong, record_reasons = False, []
    position, ratio = None, None
    liq_status = event["liquidation_status"]
    if ctx is None:
        blocking.append({"code": "CONTEXT_UNAVAILABLE", "detail": "trader context could not be fetched"})
    else:
        tr, watch = ctx["trader"], ctx["watch"]
        record_strong, record_reasons = _strong_record(ctx)
        position = next((p for p in ctx["positions"] if p["coin"] == coin), None)
        fresh = bool(watch and watch["is_fresh"])
        if cls in HOLDING_CLASSES:
            if not fresh:
                blocking.append({"code": "TRADER_STALE", "detail": f"last poll {watch and watch['last_observed_at']}"})
            if position is None or (D(position["szi"]) > 0) != (event["direction"] == "long"):
                blocking.append({"code": "CONTRADICTED_BY_CONTEXT",
                                 "detail": "current positions do not show this position any more"})
                position = None
        if position and cls in HOLDING_CLASSES:
            p = position
            szi, ntl = D(p["szi"]), D(p["notional_usd"])
            f.add("position.size", "size", abs(szi), fmt.size(szi, coin), "context.positions.szi", unit=coin)
            f.usd("position.notional_usd", "notional", ntl, "context.positions.notional_usd", as_of=p["observed_at"])
            if p["mark_px"] is not None:
                f.add("position.mark_px", "price", D(p["mark_px"]), fmt.price(D(p["mark_px"])), "context.positions.mark_px",
                      unit="USD", as_of=p["observed_at"])
            if p["entry_px"] is not None:
                f.add("position.entry_px", "price", D(p["entry_px"]), fmt.price(D(p["entry_px"])),
                      "context.positions.entry_px", unit="USD", label="average entry reported by the venue")
            if p["leverage"] is not None:
                f.add("position.leverage", "leverage", D(p["leverage"]), fmt.leverage(D(p["leverage"])),
                      "context.positions.leverage")
                f.usd("position.implied_margin_usd", "estimate", D(p["implied_margin"]) if p["implied_margin"] else None,
                      "context.positions.implied_margin", derivation="notional / leverage (estimate for cross margin)")
            if p["liq_px"] is not None and p["mark_px"]:
                liq, mark = D(p["liq_px"]), D(p["mark_px"])
                f.add("position.liq_px", "price", liq, fmt.price(liq), "context.positions.liq_px", unit="USD",
                      label="liquidation price reported by the venue")
                dist = abs(mark - liq) / mark
                f.add("position.liq_distance", "percent", dist, fmt.pct(dist), "context.positions (|mark-liq|/mark)")
            if p["upl"] is not None:
                f.usd("position.unrealized_pnl_usd", "unrealized_pnl", D(p["upl"]), "context.positions.upl",
                      as_of=p["observed_at"])
                f.constraints.add("PNL_UNREALIZED")
            f.add("position.observed_at", "time", T(p["observed_at"]), fmt.when(T(p["observed_at"])),
                  "context.positions.observed_at")
            if p["opened_at_source"] == "source" and p["opened_at"]:
                f.add("position.opened_at", "time", T(p["opened_at"]), fmt.when(T(p["opened_at"])),
                      "context.positions.opened_at (venue-reported)")
        # --- the trader
        name = clean_name(tr.get("display_name") or tr.get("handle"))
        flags = set(tr.get("quality_flags") or []) | set(tr.get("enrichment_flags") or [])
        av = D(watch["account_value"]) if watch and watch["account_value"] is not None else D(tr["account_value"])
        if venue == "okx":
            f.constraints.add("OKX_LEAD_ASSETS")
            okx_assets = D(ctx["okx"]["trader"]["asset"]) if ctx.get("okx") and ctx["okx"]["trader"] and \
                ctx["okx"]["trader"]["asset"] is not None else None
            f.usd("trader.okx_lead_assets_usd", "notional", okx_assets, "context.okx.trader.asset")
        elif av and av > 0 and not (flags & {"aggregate_account", "zero_account_value", "zero", "dust"}):
            f.usd("trader.account_value_usd", "notional", av, "context.watch.account_value")
            if position:
                ratio = D(position["notional_usd"]) / av
                f.add("trader.position_to_account", "ratio", ratio, fmt.multiple(ratio),
                      "position.notional_usd / trader.account_value_usd", unit="x")
        else:
            f.constraints.add("ACCOUNT_RATIO_UNAVAILABLE")
        for m in ctx["metrics"]:
            if m["window"] == "90d" and m["covered"] and m["reliability"] in ("high", "medium") and m["twr"] is not None:
                f.add("trader.twr_90d", "return", D(m["twr"]), fmt.pct(D(m["twr"])), "context.metrics[90d].twr",
                      coverage={"reliability": m["reliability"], "n_points": m["n_points"], "notes": m["notes"]},
                      label="time-weighted return, coarse portfolio points")
                if m["mdd"] is not None:
                    f.add("trader.max_drawdown_90d", "return", abs(D(m["mdd"])), fmt.pct(abs(D(m["mdd"]))),
                          "context.metrics[90d].mdd", coverage={"reliability": m["reliability"]})
        if record_strong:
            f.add("trader.record", "text", record_reasons, {"zh": record_reasons, "en": record_reasons},
                  "context.quality")
        # --- history, only when the archive is complete
        cov = ctx["fill_coverage"]
        hist_ok = bool(cov and cov["status"] == "synced" and not cov["truncated"] and cov["earliest_at"])
        if hist_ok and venue == "hyperliquid":
            done = [c for c in ctx["cycles"] if c["complete"] and c["status"] != "open"]
            if done:
                nets = [D(c["realized_pnl"]) - D(c["fees"]) for c in done]
                since = min(T(c["opened_at"]) for c in done)
                hc = {"complete_only": True, "n_cycles": len(done), "fills_from": cov["earliest_at"], "truncated": False,
                      "since": since.isoformat(), "note": "most recent complete cycles returned by the feed"}
                f.add("history.complete_cycles", "count", len(done), fmt.count(len(done)), "context.cycles", coverage=hc)
                wins = sum(1 for n in nets if n > 0)
                f.add("history.winning_cycles", "count", wins, fmt.count(wins), "context.cycles", coverage=hc)
                f.usd("history.net_realized_usd", "realized_pnl", sum(nets, Decimal(0)), "context.cycles",
                      coverage=hc, derivation="sum of realized P&L minus fees over complete closed cycles")
                f.usd("history.largest_prior_notional_usd", "notional", max(D(c["max_notional"]) for c in done),
                      "context.cycles.max_notional", coverage=hc)
                f.add("history.since", "time", since, fmt.when(since), "context.cycles.opened_at", coverage=hc)
                streak = 0
                for n in nets:  # cycles come most recent first
                    if n <= 0:
                        break
                    streak += 1
                if streak >= 3 and streak < len(done):  # a streak needs a known start: a loss before it
                    f.add("history.win_streak", "count", streak, fmt.count(streak), "context.cycles", coverage=hc)
        elif hist_ok and venue == "okx" and ctx.get("okx"):
            t = ctx["okx"]["totals"]
            hc = {"okx_whole_positions": True, "since": t["since"], "note": "OKX position history, SWAP only"}
            f.add("history.okx_closed_positions", "count", int(t["positions"]), fmt.count(int(t["positions"])),
                  "context.okx.totals", coverage=hc)
            f.add("history.okx_winning_positions", "count", int(t["wins"]), fmt.count(int(t["wins"])),
                  "context.okx.totals", coverage=hc)
            f.usd("history.okx_realized_usd", "realized_pnl", D(t["realized"]), "context.okx.totals", coverage=hc)
            f.add("history.okx_liquidations", "count", int(t["liquidations"]), fmt.count(int(t["liquidations"])),
                  "context.okx.totals", coverage=hc)
        else:
            f.constraints.add("HISTORY_UNAVAILABLE")
        # --- liquidation: confirmation from an exchange record within the observation window
        if cls == "liquidation" and trigger:
            a = T(trigger["window_start"]) or T(trigger["observed_at"])
            b = T(trigger["observed_at"])
            if venue == "hyperliquid":
                hits = [x for x in ctx["confirmed_liquidations"] if x["coin"] == coin and a <= T(x["time"]) <= b]
                if hits:
                    liq_status = "confirmed"
                    f.add("liquidation.fill_time", "time", T(hits[0]["time"]), fmt.when(T(hits[0]["time"])),
                          "context.confirmed_liquidations.time")
                    f.usd("liquidation.closed_pnl_usd", "realized_pnl", sum(D(x["closed_pnl"]) for x in hits),
                          "context.confirmed_liquidations.closed_pnl")
            elif ctx.get("okx"):
                hits = [x for x in ctx["okx"]["closes"] if x["coin"] == coin and x["liquidated"]
                        and a <= T(x["close_time"]) <= b]
                if hits:
                    liq_status = "confirmed"
                    f.add("liquidation.close_time", "time", T(hits[0]["close_time"]), fmt.when(T(hits[0]["close_time"])),
                          "context.okx.closes.close_time")
                    f.usd("liquidation.realized_pnl_usd", "realized_pnl", D(hits[0]["realized_pnl"]),
                          "context.okx.closes.realized_pnl")
        if name:
            f.add("trader.venue_nickname", "text", name, {"zh": [name], "en": [name]}, "context.trader.display_name",
                  label="self-chosen public nickname on the venue")

    # --- closes: what was there before
    if cls in ("whale_close", "liquidation") and trigger:
        for fid, col, label in (("close.entry_px", "entry_px", "last reported average entry"),
                                ("close.liq_px", "liq_px", "last reported liquidation price")):
            if trigger[col] is not None:
                f.add(fid, "price", D(trigger[col]), fmt.price(D(trigger[col])), f"source_event.{col}", unit="USD",
                      label=label)
        if trigger["leverage"] is not None:
            f.add("close.leverage", "leverage", D(trigger["leverage"]), fmt.leverage(D(trigger["leverage"])),
                  "source_event.leverage")
        if trigger["entry_px"] is not None and trigger["mark_px"] is not None and liq_status != "confirmed":
            sz, entry, mark = abs(D(trigger["szi_before"])), D(trigger["entry_px"]), D(trigger["mark_px"])
            est = (mark - entry) * sz * (1 if trigger["side"] == "long" else -1)
            f.usd("close.pnl_estimate_usd", "estimate", est, "source_event (mark - entry) x size",
                  derivation="mark at observation minus last reported entry, times size; fees and funding excluded")
            f.constraints.add("PNL_ESTIMATED_FROM_MARK")
    if cls == "liquidation":
        f.add("liquidation.status", "text", liq_status, {"zh": ["已确认" if liq_status == "confirmed" else "疑似"],
                                                         "en": [liq_status]}, "derived")
        if liq_status != "confirmed":
            f.constraints.add("LIQ_SUSPECTED_ONLY")
    subject_ref = event.get("public_label") or short_ref(event["trader_key"])
    if not event.get("public_label"):
        f.constraints.add("NO_IDENTITY")

    score, breakdown = _score(event, episode, f, ctx, record_strong, ratio, liq_status, now)
    codes = sorted({b["code"] for b in blocking})
    gate_status = "blocked" if blocking else "pass"
    content = {
        "schema": SCHEMA, "builder_version": BUILDER_VERSION,
        "source": {"system": "alphascope", "feed_schema": "feed.v1",
                   "source_event_ids": [e["source_event_id"] for e in source_events],
                   "context_as_of": ctx["as_of"] if ctx else None},
        "subject": {"trader_ref": subject_ref, "trader_key": event["trader_key"], "venue": venue,
                    "identity": "public_label" if event.get("public_label") else "unknown"},
        "event": {"class": cls, "coin": coin, "direction": event["direction"], "escalation_level": event["escalation_level"],
                  "liquidation_status": liq_status},
        "facts": f.items,
        "constraints": [{"code": c, "text": CONSTRAINT_TEXT[c]} for c in sorted(f.constraints)],
        "gate": {"status": gate_status, "blocking": codes},
    }
    return Built(content, gate_status, blocking, score, breakdown, record_strong, liq_status)


def _score(event, episode, f: Facts, ctx, record_strong, ratio, liq_status, now) -> tuple[Decimal, dict]:
    """Score s1: transparent points per component; credibility is the gate, not a component."""
    cls, venue = event["event_class"], event["venue"]
    b: dict[str, dict] = {}

    def comp(name, pts, mx, note, **inputs):
        b[name] = {"points": round(float(pts), 2), "max": mx, "note": note, "inputs": inputs}

    size = None
    for fid in ("position.notional_usd", "change.notional_usd", "episode.peak_notional_usd"):
        x = f.get(fid)
        if x and x["kind"] == "notional":
            size = D(x["value"])
            break
    base = Decimal(1_000_000 if venue == "hyperliquid" else 300_000)
    if size and size > 0:
        comp("size_abs", 25 * max(0.0, min(1.0, math.log10(float(size / base)) / 2 + 0.25)), 25,
             "25 x (log10(notional / venue threshold) / 2 + 0.25): 1x=6, 10x=19, 32x and above=25",
             notional=str(size))
    else:
        comp("size_abs", 0, 25, "no size")
    if ratio is not None:
        comp("size_vs_account", 15 * min(1.0, float(ratio) / 2), 15, "position / account value, full at 2x",
             ratio=str(round(ratio, 3)))
    else:
        comp("size_vs_account", 0, 15, "account value unavailable or not applicable")
    largest = f.get("history.largest_prior_notional_usd")
    if largest and size:
        r = size / D(largest["value"])
        comp("size_vs_own_history", 10 if r >= 2 else 5 if r >= 1 else 0, 10,
             "vs largest complete cycle in the archive", ratio=str(round(r, 2)))
    else:
        comp("size_vs_own_history", 0, 10, "no complete archive to compare with")
    rec = 0
    if ctx:
        q = ctx["quality"]
        rec += 8 if any(t["status"] == "verified" for t in q["styles"]) else 0
        rec += 6 if any(t["status"] == "scored" and t["q_value"] is not None and D(t["q_value"]) <= Decimal("0.1")
                        and (t["n_scored"] or 0) >= 30 for t in q["timing"]) else 0
        rec += {"A": 6, "B": 3}.get((q["copy"] or {}).get("grade"), 0)
        rec += 4 if f.get("trader.twr_90d") and D(f.get("trader.twr_90d")["value"]) > 0 \
            and f.get("trader.twr_90d")["coverage"]["reliability"] == "high" else 0
    comp("trader_record", min(rec, 20), 20, "verified style 8, significant timing 6, copy grade A 6 / B 3, TWR 4",
         strong=record_strong)
    risk = 0
    lev = f.get("position.leverage") or f.get("close.leverage")
    if lev and D(lev["value"]) >= 20:
        risk += 5
    dist = f.get("position.liq_distance")
    if dist and D(dist["value"]) <= Decimal("0.05"):
        risk += 5
    if cls == "underwater_add":
        risk += 5
    if cls == "liquidation":
        risk = 15 if liq_status == "confirmed" else 8
    comp("risk", min(risk, 15), 15, "leverage>=20 5, within 5% of liquidation 5, adding underwater 5; "
                                    "liquidation 15 confirmed / 8 suspected")
    comp("market_relevance", 4 if event["coin"] in MAJOR_COINS else 0, 10, "major coin 4 (cluster bonus in Phase 2)")
    comp("continuity", 2 if event.get("story_published_before") else 0, 5, "story with an earlier publication")
    pen = 0
    w = f.get("change.window")
    if w and w["value"]["from"] and T(w["value"]["to"]) - T(w["value"]["from"]) > timedelta(hours=4):
        pen -= 5
    if ctx and not (ctx["watch"] and ctx["watch"]["is_fresh"]):
        pen -= 10
    comp("penalties", pen, 0, "observation window over 4h -5, trader data stale -10")
    total = max(0.0, sum(c["points"] for c in b.values()))
    return Decimal(str(round(total, 2))), {"version": SCORE_VERSION, "components": b, "total": round(total, 2)}


def material_changes(old: dict | None, new: dict) -> list[str]:
    """Fact ids (or event/gate markers) whose change is material: new or removed facts, any change in text, time,
    count or classification, a size change of 10% or more. Mark-driven values alone never count."""
    if old is None:
        return ["<new>"]
    out = []
    if old["event"] != new["event"]:
        out.append("<event>")
    if old["gate"] != new["gate"]:
        out.append("<gate>")
    a = {x["id"]: x for x in old["facts"]}
    b = {x["id"]: x for x in new["facts"]}
    for fid in sorted(set(a) | set(b)):
        if fid in VOLATILE:
            continue
        if fid not in a or fid not in b:
            out.append(fid)
            continue
        va, vb = a[fid]["value"], b[fid]["value"]
        if a[fid]["kind"] in ("notional", "size", "price", "leverage", "realized_pnl", "estimate", "ratio", "return",
                              "percent", "unrealized_pnl"):
            x, y = D(va), D(vb)
            if x != y and (x == 0 or abs(y - x) / abs(x) >= Decimal("0.1")):
                out.append(fid)
        elif va != vb:
            out.append(fid)
    return out
