"""feed.v1 keys the newsroom relies on. Identical to AlphaScope's backend/tests/fixtures/feed_v1_contract.json
(a test compares them), so a breaking change on either side fails a test instead of production."""

SCHEMA = "feed.v1"
REQUIRED: dict[str, list[str]] = {
    "meta": [
        "schema",
        "server_time",
        "settle_seconds",
        "limits",
        "rules"
    ],
    "meta.rules": [
        "liq_tolerance",
        "stale_factor",
        "poll_minutes",
        "health"
    ],
    "events": [
        "schema",
        "as_of",
        "settled_before",
        "after_id",
        "next_after_id",
        "head_id",
        "has_more",
        "rows"
    ],
    "events.row": [
        "source_id",
        "trader_key",
        "venue",
        "display_name",
        "handle",
        "coin",
        "kind",
        "side",
        "szi_before",
        "szi_after",
        "notional_change",
        "mark_px",
        "entry_px",
        "leverage",
        "liq_px",
        "event_time",
        "time_precision",
        "window_start",
        "observed_at"
    ],
    "context": [
        "schema",
        "as_of",
        "trader",
        "watch",
        "positions",
        "recent_events",
        "metrics",
        "quality",
        "fill_coverage",
        "cycles",
        "confirmed_liquidations",
        "okx"
    ],
    "context.trader": [
        "trader_key",
        "venue",
        "display_name",
        "handle",
        "first_seen_at",
        "account_value",
        "tier",
        "quality_flags",
        "enrichment_flags",
        "month_pnl",
        "alltime_pnl",
        "leaderboard_fetched_at"
    ],
    "context.watch": [
        "tier",
        "last_observed_at",
        "stale_after",
        "is_fresh",
        "failures",
        "account_value",
        "n_positions",
        "gross_notional"
    ],
    "context.position": [
        "coin",
        "szi",
        "notional_usd",
        "mark_px",
        "entry_px",
        "leverage",
        "margin_mode",
        "liq_px",
        "upl",
        "opened_at",
        "opened_at_source",
        "opened_after",
        "first_seen_at",
        "observed_at",
        "implied_margin"
    ],
    "context.recent_event": [
        "source_id",
        "coin",
        "kind",
        "side",
        "szi_before",
        "szi_after",
        "notional_change",
        "mark_px",
        "event_time",
        "time_precision",
        "window_start",
        "observed_at"
    ],
    "context.metric": [
        "window",
        "computed_at",
        "covered",
        "n_points",
        "twr",
        "mdd",
        "pnl",
        "reliability",
        "notes"
    ],
    "context.quality": [
        "styles",
        "timing",
        "copy",
        "health"
    ],
    "context.fill_coverage": [
        "source",
        "status",
        "earliest_at",
        "latest_at",
        "n_records",
        "truncated",
        "last_synced_at"
    ],
    "context.cycle": [
        "coin",
        "direction",
        "opened_at",
        "version",
        "closed_at",
        "status",
        "complete",
        "max_notional",
        "realized_pnl",
        "fees"
    ],
    "context.confirmed_liquidation": [
        "time",
        "coin",
        "px",
        "sz",
        "dir",
        "closed_pnl",
        "liquidation"
    ],
    "context.okx": [
        "closes",
        "totals",
        "trader"
    ],
    "context.okx.close": [
        "pos_id",
        "coin",
        "direction",
        "open_time",
        "close_time",
        "max_notional",
        "pnl",
        "realized_pnl",
        "close_type",
        "liquidated",
        "lever"
    ],
    "at_risk": [
        "schema",
        "as_of",
        "max_dist",
        "rows"
    ],
    "at_risk.row": [
        "trader_key",
        "venue",
        "coin",
        "szi",
        "notional_usd",
        "mark_px",
        "entry_px",
        "leverage",
        "liq_px",
        "upl",
        "opened_at_source",
        "first_seen_at",
        "observed_at",
        "account_value",
        "liq_dist"
    ],
    "health": [
        "schema",
        "as_of",
        "rules",
        "sources"
    ],
    "health.sources": [
        "radar_hyperliquid",
        "radar_okx",
        "market_event",
        "jobs",
        "fill_archive",
        "okx_history",
        "copy_score"
    ],
    "health.radar": [
        "watched",
        "fresh",
        "positioned",
        "latest",
        "oldest",
        "fresh_ratio",
        "status"
    ],
    "copy_board": [
        "schema",
        "as_of",
        "window",
        "run",
        "assumptions",
        "rows"
    ],
    "copy_board.row": [
        "rank",
        "trader_key",
        "display_name",
        "pnl",
        "pnl_ratio",
        "win_rate",
        "max_drawdown",
        "copy_ret",
        "copy_mdd",
        "leader_ret",
        "trades",
        "liquidations",
        "approx_equity",
        "backtest_at"
    ],
    "dropouts": [
        "schema",
        "as_of",
        "days",
        "rows"
    ],
    "dropouts.row": [
        "trader_key",
        "display_name",
        "first_seen_at",
        "last_seen_at",
        "seen_days",
        "best_rank",
        "history_status",
        "closes_since",
        "realized_since",
        "liquidations_since"
    ],
    "structure": [
        "schema",
        "as_of",
        "coverage",
        "coin",
        "positions",
        "ref"
    ],
    "forecasts": [
        "schema",
        "as_of",
        "coin",
        "days",
        "status",
        "scores"
    ],
    "forecasts.score": [
        "model",
        "horizon",
        "issued",
        "settled",
        "missing",
        "brier",
        "base_brier",
        "beat_baseline",
        "skill_p",
        "blocks"
    ],
    "funding": [
        "schema",
        "as_of",
        "coin",
        "funding",
        "frames"
    ],
    "funding.frame": [
        "bucket_ts",
        "venue",
        "smart_n",
        "smart_long",
        "control_n",
        "control_long",
        "divergence"
    ],
    "holding": [
        "schema",
        "as_of",
        "days",
        "rows"
    ],
    "holding.row": [
        "scale",
        "winner",
        "n",
        "p25",
        "median",
        "p75",
        "traders"
    ],
    "closed": [
        "schema",
        "as_of",
        "hours",
        "rows"
    ],
    "closed.row": [
        "trader_key",
        "venue",
        "coin",
        "direction",
        "opened_at",
        "version",
        "closed_at",
        "max_notional",
        "realized_pnl",
        "fees",
        "prior_streak",
        "prior_known",
        "coverage_status",
        "truncated"
    ],
    "groups": [
        "schema",
        "as_of",
        "coin",
        "latest",
        "rows"
    ],
    "groups.row": [
        "venue",
        "cohort",
        "bucket_ts",
        "long_traders",
        "short_traders",
        "long_ntl",
        "short_ntl"
    ],
    "coin_positions": [
        "schema",
        "as_of",
        "coin",
        "venue",
        "coverage",
        "sides",
        "largest"
    ],
    "coin_positions.side": [
        "side",
        "traders",
        "notional",
        "timing_traders",
        "timing_notional"
    ],
    "coin_positions.largest": [
        "side",
        "rank",
        "trader_key",
        "venue",
        "notional_usd",
        "leverage",
        "entry_px",
        "liq_px",
        "timing_significant"
    ],
}


class ContractError(Exception):
    pass


def check(name: str, obj) -> None:
    if not isinstance(obj, dict):
        raise ContractError(f"{name}: expected an object")
    missing = [k for k in REQUIRED[name] if k not in obj]
    if missing:
        raise ContractError(f"{name}: missing {missing}")


def check_all(name: str, items, item_name: str) -> None:
    for it in items or []:
        check(item_name, it)


def check_context(c: dict) -> None:
    check("context", c)
    if c.get("schema") != SCHEMA:
        raise ContractError(f"unexpected schema {c.get('schema')}")
    check("context.trader", c["trader"])
    if c["watch"] is not None:
        check("context.watch", c["watch"])
    check_all("positions", c["positions"], "context.position")
    check_all("recent", c["recent_events"], "context.recent_event")
    check_all("metrics", c["metrics"], "context.metric")
    check("context.quality", c["quality"])
    if c["fill_coverage"] is not None:
        check("context.fill_coverage", c["fill_coverage"])
    check_all("cycles", c["cycles"], "context.cycle")
    check_all("liqs", c["confirmed_liquidations"], "context.confirmed_liquidation")
    if c["okx"] is not None:
        check("context.okx", c["okx"])
        check_all("okx", c["okx"]["closes"], "context.okx.close")
