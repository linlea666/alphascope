"""Shared pieces for column issues: a column packet has the same shape as an event packet (fp.v1) so the editor-in-chief,
reporters, claims guard, cards and publishing all work on it unchanged. Every number is a fact with display strings;
a `chart` fact lists which facts a card draws, so a card can only show numbers that are in the packet."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from nr.facts import fmt
from nr.facts.builder import CONSTRAINT_TEXT, D, Facts

COLUMN_CONSTRAINTS = {
    "COPY_BACKTEST_ASSUMPTIONS": "Copy returns are backtests with stated assumptions (delay, fees, slippage, sizing), "
                                 "not what any follower actually earned.",
    "SURVIVORSHIP": "Boards only show traders who are doing well now; do not generalize from them.",
    "DROPOUT_REASON_UNKNOWN": "Why a trader left the board is unknown; do not imply losses or liquidation unless the "
                              "facts show them.",
    "FORECAST_SHADOW": "These are pre-registered research forecasts in shadow mode, not trading signals; report misses "
                       "as plainly as hits.",
    "SCREEN_NOT_SIGNAL": "A research screen, not evidence of a tradable edge.",
    "MATCHED_CONTROL": "The comparison is specialists against a matched control group from the observed sample.",
    "COMPLETE_CYCLES_ONLY": "Only complete, closed cycles reconstructed from fills are counted.",
    "STYLE_RULE_VERSION": "Style labels come from versioned rules; a change can reflect a rule change as well as the "
                          "trader.",
    "PUBLISHED_OUTCOMES": "Outcomes are read from later observations of the same positions; prices since publication "
                          "are mark prices at observation.",
    "HINDSIGHT": "This is a look back with information that was not available at the time.",
    "GROUPS_OVERLAP": "Groups overlap (a cohort is part of its venue's sample); compare them side by side, never add them.",
}
CONSTRAINT_TEXT.update(COLUMN_CONSTRAINTS)


@dataclass
class ColumnIssue:
    column: str
    period_key: str
    coin: str = "*"
    trader_key: str | None = None
    title_zh: str = ""
    title_en: str = ""
    facts: Facts = field(default_factory=Facts)
    blocking: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def month_key(now: datetime) -> str:
    return f"{now:%Y-%m}"


def day_key(now: datetime) -> str:
    return f"{now:%Y-%m-%d}"


def pct_fact(f: Facts, fid: str, v, source: str, **x) -> None:
    if v is not None:
        f.add(fid, "percent", D(v), fmt.pct(D(v)), source, **x)


def count_fact(f: Facts, fid: str, v, source: str, **x) -> None:
    if v is not None:
        f.add(fid, "count", int(v), fmt.count(int(v)), source, **x)


def hours_fact(f: Facts, fid: str, v, source: str) -> None:
    if v is not None:
        h = D(v).quantize(Decimal("0.1"))
        f.add(fid, "duration", h, {"zh": [f"{h}小时"], "en": [f"{h}h"]}, source, unit="hours")


def text_fact(f: Facts, fid: str, v: str, source: str, **x) -> None:
    f.add(fid, "text", v, {"zh": [v], "en": [v]}, source, **x)


def time_fact(f: Facts, fid: str, t: datetime | str | None, source: str, **x) -> None:
    if t:
        t = t if isinstance(t, datetime) else datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        f.add(fid, "time", t, fmt.when(t), source, **x)


def chart(f: Facts, title_zh: str, title_en: str, rows: list[dict]) -> None:
    """rows: [{"label": "...", "fact_ids": [...]}]; values are drawn from those facts' display strings."""
    f.add("chart", "chart", {"title": {"zh": title_zh, "en": title_en}, "rows": rows},
          {"zh": [title_zh], "en": [title_en]}, "column")


def stale(at, now: datetime, hours: int, what: str) -> dict | None:
    if at is None:
        return {"code": "STALE_RESEARCH", "detail": f"{what}: no data"}
    t = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    if now - t > timedelta(hours=hours):
        return {"code": "STALE_RESEARCH", "detail": f"{what} is from {t:%Y-%m-%d %H:%M} UTC"}
    return None


def short_name(trader_key: str, display_name: str | None) -> str:
    from nr.facts.builder import clean_name, short_ref
    return clean_name(display_name) or short_ref(trader_key)
