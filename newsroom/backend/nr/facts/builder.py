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
BUILDER_VERSION = "facts-3"
SCORE_VERSION = "s1"
HOLDING_CLASSES = ("whale_open", "flip", "whale_add", "underwater_add", "near_liquidation", "notable_trader_move")
MAJOR_COINS = ("BTC", "ETH", "SOL", "HYPE")
# values that move with the mark price between polls: they never make a new packet version on their own
# (sizes and notionals are not here: a 10% move in either is material, whatever caused it)
VOLATILE = {"position.mark_px", "position.unrealized_pnl_usd", "position.liq_distance", "position.observed_at", "episode.path",
            "trader.account_value_usd"}
VOLATILE_PREFIXES = ("crowd.", "book.", "change.recency", "trader.pnl_series")  # snapshots of other traders and other coins: context, not the story
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
    "CROWD_SAMPLE": "Crowd figures count traders in AlphaScope's sample whose positions were fresh at the time shown; "
                    "they are a snapshot of the sample, not of the exchange.",
    "HEDGE_POSSIBLE": "The trader also holds sizeable opposite-direction positions (book.opposite); do not present this "
                      "position as a one-way bet without saying so.",
    "ARCHIVE_PARTIAL": "The trade archive starts later than the trader's history; 'largest', 'last' and counts refer only "
                       "to the period it covers.",
    "RADAR_SINCE": "Earlier positions are known only since this trader was first observed; say 'since <date>'.",
    "LEADERBOARD_PNL": "Leaderboard P&L is reported by the venue and includes unrealized P&L; never call it realized.",
}


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def T(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def short_ref(trader_key: str) -> str:
    """0x9ee…be185: enough to recognise and search, short enough for a post."""
    return f"OKX·{trader_key[-6:]}" if trader_key.startswith("okx:") else f"{trader_key[:5]}…{trader_key[-5:]}"


def explorer_url(trader_key: str, venue: str) -> str | None:
    return f"https://hypurrscan.io/address/{trader_key}" if venue == "hyperliquid" and trader_key.startswith("0x") \
        else None


def recency(seen: datetime, now: datetime) -> tuple[str, dict] | None:
    """How long ago, in a post's words. Refreshed when a draft is written; the guard holds "刚刚" to 3 hours."""
    age = now - seen
    if age <= timedelta(hours=1):
        return "just_now", {"zh": ["刚刚"], "en": ["just now"]}
    if age <= timedelta(hours=6):
        return "hours", {"zh": ["过去几小时"], "en": ["in the past few hours"]}
    if age <= timedelta(hours=24):
        return "day", {"zh": ["过去24小时"], "en": ["in the past 24 hours"]}
    return None


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
          now: datetime, cfg: dict, path: list[dict] | None = None, crowd: dict | None = None,
          prior: dict | None = None) -> Built:
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
        seen = max(T(e["observed_at"]) for e in source_events)
        if r := recency(seen, now):
            f.add("change.recency", "text", r[0], r[1], "source_event.observed_at", as_of=seen.isoformat(),
                  label="how long ago the change was seen; use these words instead of a clock time")

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
    if path and len(path) >= 2:  # the observed size path, for the lifecycle card (sampled: changes between polls)
        pts = [{"t": T(p["observed_at"]).isoformat(), "size": format(abs(D(p["szi_after"])), "f"),
                "kind": p["kind"]} for p in path[-40:]]
        f.add("episode.path", "series", pts, {"zh": [f"{len(pts)} 次观测"], "en": [f"{len(pts)} observations"]},
              "source_event (this position)", label="size after each observed change; changes happen between polls")

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
        contrast_own(f, event, position, ctx, prior, now)
        if name:
            f.add("trader.venue_nickname", "text", name, {"zh": [name], "en": [name]}, "context.trader.display_name",
                  label="self-chosen public nickname on the venue")

    if crowd:
        contrast_crowd(f, event, position, crowd)

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
                    "explorer_url": explorer_url(event["trader_key"], venue),
                    "identity": "public_label" if event.get("public_label") else "unknown"},
        "event": {"class": cls, "coin": coin, "direction": event["direction"], "escalation_level": event["escalation_level"],
                  "liquidation_status": liq_status},
        "facts": f.items,
        "constraints": [{"code": c, "text": CONSTRAINT_TEXT[c]} for c in sorted(f.constraints)],
        "gate": {"status": gate_status, "blocking": codes},
    }
    return Built(content, gate_status, blocking, score, breakdown, record_strong, liq_status)


# ---- contrast: how this move compares with the trader's own past, the rest of their book and the crowd -------------
# Each fact carries its sample and coverage. Nothing is computed where the data cannot support it.

SIDE = {"long": {"zh": "多单", "en": "long"}, "short": {"zh": "空单", "en": "short"}}


def contrast_own(f: Facts, event: dict, position: dict | None, ctx: dict, prior: dict | None, now: datetime) -> None:
    coin, direction, venue = event["coin"], event["direction"], event["venue"]
    size = D(position["notional_usd"]) if position else None
    # the rest of the book: share of gross exposure, sizeable opposite positions (a pair trade or a hedge)
    if position and len(ctx["positions"]) >= 2 and size:
        book = [(p, D(p["notional_usd"])) for p in ctx["positions"] if D(p["notional_usd"]) > 0]
        gross = sum((n for _, n in book), Decimal(0))
        f.add("book.positions", "count", len(book), fmt.count(len(book)), "context.positions",
              label="open positions the trader holds now, all coins")
        f.usd("book.gross_notional_usd", "notional", gross, "context.positions.notional_usd",
              derivation="sum of notional over all open positions")
        f.add("book.share", "percent", size / gross, fmt.pct(size / gross), "position / book.gross_notional_usd")
        rank = 1 + sum(1 for _, n in book if n > size)
        f.add("book.rank", "count", rank, fmt.count(rank), "context.positions",
              label="rank of this position by notional in the trader's book (1 = largest)")
        opp = [(p, n) for p, n in book if p["coin"] != coin and (D(p["szi"]) > 0) != (direction == "long")
               and n >= size * Decimal("0.2")]
        if opp:
            other = "short" if direction == "long" else "long"
            f.add("book.opposite", "text", [{"coin": p["coin"], "direction": other, "notional_usd": format(n, "f")}
                                            for p, n in opp],
                  {"zh": [f"{p['coin']} {SIDE[other]['zh']} {fmt.usd(n)['zh'][0]}" for p, n in opp],
                   "en": [f"{p['coin']} {SIDE[other]['en']} {fmt.usd(n)['en'][0]}" for p, n in opp]},
                  "context.positions", label="opposite-direction positions of at least 20% of this one's size")
            for p, n in opp:  # the figures themselves, so a writer can cite them
                f.usd(f"book.opposite_{p['coin']}_usd", "notional", n, "context.positions.notional_usd")
            f.constraints.add("HEDGE_POSSIBLE")
    if len(ctx["positions"]) >= 2:
        book_lines(f, ctx, prior, now)
    # the trade archive on this coin: complete cycles only, wherever the archive starts
    cov = ctx["fill_coverage"]
    if venue == "hyperliquid" and cov and cov["status"] == "synced" and cov["earliest_at"]:
        done = [c for c in ctx["cycles"] if c["coin"] == coin and c["complete"] and c["status"] != "open"]
        if done:
            hc = {"complete_only": True, "coin": coin, "n_cycles": len(done), "fills_from": cov["earliest_at"],
                  "truncated": bool(cov["truncated"]), "note": "complete closed cycles on this coin in the archive"}
            if cov["truncated"]:
                f.constraints.add("ARCHIVE_PARTIAL")
            f.add("contrast.archive_since", "time", T(cov["earliest_at"]), fmt.when(T(cov["earliest_at"])),
                  "context.fill_coverage.earliest_at", coverage=hc, label="the trade archive starts here")
            f.add("contrast.coin_cycles", "count", len(done), fmt.count(len(done)), "context.cycles", coverage=hc)
            wins = sum(1 for c in done if D(c["realized_pnl"]) - D(c["fees"]) > 0)
            f.add("contrast.coin_winning_cycles", "count", wins, fmt.count(wins), "context.cycles", coverage=hc,
                  label="cycles closed with a profit net of fees")
            largest = max(D(c["max_notional"]) for c in done)
            f.usd("contrast.largest_prior_cycle_usd", "notional", largest, "context.cycles.max_notional", coverage=hc)
            if size and largest > 0:
                f.add("contrast.vs_largest_prior_cycle", "ratio", size / largest, fmt.multiple(size / largest),
                      "position.notional_usd / contrast.largest_prior_cycle_usd", unit="x", coverage=hc)
            same = [c for c in done if c["direction"] == direction]  # cycles come most recent first
            if same:
                c = same[0]
                net = D(c["realized_pnl"]) - D(c["fees"])
                f.add("contrast.last_same_side_closed_at", "time", T(c["closed_at"]), fmt.when(T(c["closed_at"])),
                      "context.cycles.closed_at", coverage=hc, label=f"last complete {direction} cycle on {coin}")
                f.usd("contrast.last_same_side_net_usd", "realized_pnl", net, "context.cycles", coverage=hc,
                      derivation="realized P&L minus fees of that cycle")
                f.usd("contrast.last_same_side_peak_usd", "notional", D(c["max_notional"]), "context.cycles.max_notional",
                      coverage=hc)
                v, unit, disp = fmt.duration(D(c["holding_hours"]))
                f.add("contrast.last_same_side_held", "duration", v, disp, "context.cycles.holding_hours", unit=unit,
                      coverage=hc)
    # the radar's own memory: earlier positions on this coin since the trader was first observed
    if prior and prior.get("since") and now - T(prior["since"]) >= timedelta(days=3):
        since = T(prior["since"])
        rc = {"since": since.isoformat(), "note": "positions seen by the radar; changes between polls are not seen"}
        f.constraints.add("RADAR_SINCE")
        eps = prior["episodes"]
        f.add("radar.since", "time", since, fmt.when(since), "trader first observed", coverage=rc)
        f.add("radar.prior_positions", "count", len(eps), fmt.count(len(eps)), "position_episode", coverage=rc,
              label=f"earlier {coin} positions of this wallet since we started tracking it (radar.since)")
        same = [e for e in eps if e["direction"] == direction]
        f.add("radar.prior_same_side", "count", len(same), fmt.count(len(same)), "position_episode", coverage=rc,
              label=f"of which {direction}")
        if eps:
            peak = max(D(e["peak_notional"]) for e in eps)
            f.usd("radar.largest_prior_peak_usd", "notional", peak, "position_episode.peak_notional", coverage=rc)
            if size and peak > 0:
                f.add("radar.vs_largest_prior_peak", "ratio", size / peak, fmt.multiple(size / peak),
                      "position.notional_usd / radar.largest_prior_peak_usd", unit="x", coverage=rc)
    # the venue's all-time P&L curve, for cards only (never sent to a model)
    curve = ctx.get("pnl_series") or []
    if venue == "hyperliquid" and len(curve) >= 5:
        f.add("trader.pnl_series", "series", [{"t": p["t"], "pnl": p["pnl"]} for p in curve],
              {"zh": [f"{len(curve)} 个点"], "en": [f"{len(curve)} points"]}, "context.pnl_series",
              label="all-time P&L curve from the venue's portfolio history (includes unrealized)")
    # the venue's leaderboard figures (they include unrealized P&L)
    tr = ctx["trader"]
    if venue == "hyperliquid" and tr.get("leaderboard_fetched_at"):
        lb = {"as_of": tr["leaderboard_fetched_at"], "note": "venue leaderboard, includes unrealized P&L"}
        for fid, col in (("trader.alltime_pnl_usd", "alltime_pnl"), ("trader.month_pnl_usd", "month_pnl")):
            if tr.get(col) is not None:
                f.usd(fid, "pnl", D(tr[col]), f"context.trader.{col}", coverage=lb)
                f.constraints.add("LEADERBOARD_PNL")


BOOK_LINES = 5


def book_lines(f: Facts, ctx: dict, prior: dict | None, now: datetime) -> None:
    """The whole book, position by position (biggest open P&L first, as trackers list it), with its total open P&L:
    '$NVDA: +$1.24M'."""
    book = sorted((p for p in ctx["positions"] if D(p["notional_usd"]) > 0),
                  key=lambda p: (-abs(D(p["upl"] or 0)), -D(p["notional_usd"])))
    total = Decimal(0)
    for i, p in enumerate(book[:BOOK_LINES], 1):
        side = "long" if D(p["szi"]) > 0 else "short"
        tag = fmt.cashtag(p["coin"])
        f.add(f"book.pos{i}.coin", "text", tag, {"zh": [tag], "en": [tag]}, "context.positions.coin",
              label=f"{p['coin']} {side}")
        f.add(f"book.pos{i}.side", "text", side, {"zh": [SIDE[side]["zh"]], "en": [SIDE[side]["en"]]},
              "context.positions.szi")
        f.usd(f"book.pos{i}.notional_usd", "notional", D(p["notional_usd"]), "context.positions.notional_usd")
        if p["leverage"] is not None:
            f.add(f"book.pos{i}.leverage", "leverage", D(p["leverage"]), fmt.leverage(D(p["leverage"])),
                  "context.positions.leverage")
        for col in ("entry_px", "mark_px"):
            if p[col] is not None:
                f.add(f"book.pos{i}.{col}", "price", D(p[col]), fmt.price(D(p[col])), f"context.positions.{col}")
        if p["upl"] is not None:
            f.usd(f"book.pos{i}.upl_usd", "unrealized_pnl", D(p["upl"]), "context.positions.upl")
    for p in book:
        total += D(p["upl"] or 0)
    if any(p["upl"] is not None for p in book):
        f.usd("book.total_upl_usd", "unrealized_pnl", total, "context.positions.upl",
              derivation="sum of unrealized P&L over all open positions")
        f.constraints.add("PNL_UNREALIZED")
    # "opened today" only when we were already watching: a wallet found today has every position "new"
    since = T(prior["since"]) if prior and prior.get("since") else None
    if since and now - since > timedelta(days=2):
        fresh = [p for p in book if p["first_seen_at"] and now - T(p["first_seen_at"]) <= timedelta(hours=24)]
        f.add("book.opened_24h", "count", len(fresh), fmt.count(len(fresh)), "context.positions.first_seen_at",
              label="positions that appeared in the last 24 hours: say 'opened today' / '今天新开' only for these")
        if fresh:
            tags = [fmt.cashtag(p["coin"]) for p in fresh]
            f.add("book.opened_24h_coins", "text", tags, {"zh": ["、".join(tags)], "en": [", ".join(tags)]},
                  "context.positions.first_seen_at")


def contrast_crowd(f: Facts, event: dict, position: dict | None, crowd: dict) -> None:
    """Who else holds this coin right now in the sample, and where this position ranks among them."""
    direction = event["direction"]
    if direction not in SIDE:
        return
    sides = {x["side"]: x for x in crowd["sides"]}
    same, opp = sides.get(direction), sides.get("short" if direction == "long" else "long")
    if not same and not opp:
        return
    cc = {"as_of": crowd["as_of"], "venue": crowd["venue"], "fresh_traders": crowd["coverage"].get("fresh"),
          "note": "traders in the sample whose last poll is fresh"}
    f.constraints.add("CROWD_SAMPLE")
    if fresh := crowd["coverage"].get("fresh"):
        f.add("crowd.sample_traders", "count", int(fresh), fmt.headcount(int(fresh)), "coin_positions.coverage.fresh",
              coverage=cc, label=f"wallets we track on {crowd['venue']} right now: say 'among the ~N wallets we track'")
    f.add("crowd.as_of", "time", T(crowd["as_of"]), fmt.when(T(crowd["as_of"])), "coin_positions.as_of", coverage=cc)
    for fid, row in (("crowd.same_side", same), ("crowd.opposite_side", opp)):
        if row:
            f.add(f"{fid}_traders", "count", int(row["traders"]), fmt.count(int(row["traders"])),
                  "coin_positions.sides.traders", coverage=cc)
            f.usd(f"{fid}_notional_usd", "notional", D(row["notional"]), "coin_positions.sides.notional", coverage=cc)
    timing = sum(int(x["timing_traders"]) for x in (same, opp) if x)
    if timing >= 5:  # fewer is an anecdote, not a split
        for fid, row in (("crowd.timing_same_side", same), ("crowd.timing_opposite_side", opp)):
            n = int(row["timing_traders"]) if row else 0
            f.add(fid, "count", n, fmt.count(n), "coin_positions.sides.timing_traders", coverage=cc,
                  label="wallets with a statistically proven timing record (q < 0.1), e.g. '择时高手' / 'proven timers'")
            f.usd(f"{fid}_notional_usd", "notional", D(row["timing_notional"]) if row else Decimal(0),
                  "coin_positions.sides.timing_notional", coverage=cc)
    if position and same:
        size = D(position["notional_usd"])
        if D(same["notional"]) > 0:
            share = size / D(same["notional"])
            f.add("crowd.share_of_same_side", "percent", share, fmt.pct(share),
                  "position.notional_usd / crowd.same_side_notional_usd", coverage=cc)
        me = next((x for x in crowd["largest"] if x["side"] == direction and x["trader_key"] == event["trader_key"]),
                  None)
        if me:
            f.add("crowd.same_side_rank", "count", int(me["rank"]), fmt.count(int(me["rank"])),
                  "coin_positions.largest.rank", coverage=cc,
                  label=f"rank by size among all {direction} positions on this coin in the wallets we track (1 = largest)")


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
    out = ["<builder>"] if old.get("builder_version") != new.get("builder_version") else []  # new facts to carry
    if old["event"] != new["event"]:
        out.append("<event>")
    if old["gate"] != new["gate"]:
        out.append("<gate>")
    a = {x["id"]: x for x in old["facts"]}
    b = {x["id"]: x for x in new["facts"]}
    for fid in sorted(set(a) | set(b)):
        if fid in VOLATILE or fid.startswith(VOLATILE_PREFIXES):
            continue
        if fid not in a or fid not in b:
            out.append(fid)
            continue
        va, vb = a[fid]["value"], b[fid]["value"]
        if a[fid]["kind"] in ("notional", "size", "price", "leverage", "realized_pnl", "pnl", "estimate", "ratio", "return",
                              "percent", "unrealized_pnl"):
            x, y = D(va), D(vb)
            if x != y and (x == 0 or abs(y - x) / abs(x) >= Decimal("0.1")):
                out.append(fid)
        elif va != vb:
            out.append(fid)
    return out
