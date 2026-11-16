from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import baseline_import_at, data_as_of, num
from app.config import get_settings
from app.db import get_session
from app.quant.quality import AGGREGATE_ACCOUNT, ALL_FLAGS, DEFAULT_RANKING_EXCLUSIONS
from app.quant.returns import METRIC_VERSION
from app.tiering import TIER_RULE_VERSION

router = APIRouter(prefix="/api/v1", tags=["system"])

# Screening candidates: computed 30d metrics only, explicitly unverified (see docs/04-metrics-scoring.md).
SCREEN_MIN_TWR = 0.0
SCREEN_MAX_DD = -0.15
SCREEN_MIN_POSITIVE = 0.55


@router.get("/meta")
def meta():
    return {
        "flags": ALL_FLAGS,
        "default_exclusions": DEFAULT_RANKING_EXCLUSIONS,
        "tier_rule_version": TIER_RULE_VERSION,
        "metric_version": METRIC_VERSION,
        "windows": ["day", "week", "month", "alltime"],
    }


@router.get("/overview")
def overview(session: Session = Depends(get_session)):
    baseline = baseline_import_at(session)
    counts = session.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE present_in_latest) AS present,
               count(*) FILTER (WHERE first_seen_at > :b AND first_seen_at > now() - interval '24 hours') AS new_24h,
               count(*) FILTER (WHERE first_seen_at > :b AND first_seen_at > now() - interval '7 days') AS new_7d,
               count(*) FILTER (WHERE portfolio_fetched_at IS NOT NULL) AS enriched,
               count(*) FILTER (WHERE tier = 'B' AND present_in_latest) AS tier_b
        FROM wallet"""), {"b": baseline}).mappings().one()
    tiers = dict(session.execute(text(
        "SELECT tier, count(*) FROM wallet WHERE present_in_latest GROUP BY tier")).all())
    flags = dict(session.execute(text("""
        SELECT f, count(*) FROM wallet, unnest(quality_flags || enrichment_flags) f
        WHERE present_in_latest GROUP BY f""")).all())
    candidates = session.execute(text("""
        SELECT w.address, w.display_name, w.account_value, m.twr, m.mdd, m.positive_period_ratio,
               m.n_valid_periods, m.reliability, w.month_pnl
        FROM wallet_metric m JOIN wallet w ON w.id = m.wallet_id
        WHERE m.metric_window = '30d' AND m.reliability IN ('high', 'medium') AND m.covered
          AND m.twr > :min_twr AND m.mdd >= :max_dd AND m.positive_period_ratio >= :min_pos
          AND NOT (w.quality_flags && CAST(:ex AS text[]) OR w.enrichment_flags && CAST(:ex AS text[]))
        ORDER BY m.twr / greatest(abs(m.mdd), 0.01) DESC
        LIMIT 10"""), {"min_twr": SCREEN_MIN_TWR, "max_dd": SCREEN_MAX_DD, "min_pos": SCREEN_MIN_POSITIVE,
                       "ex": [*DEFAULT_RANKING_EXCLUSIONS, AGGREGATE_ACCOUNT]}).mappings().all()
    newest = session.execute(text("""
        SELECT address, display_name, first_seen_at, account_value, month_pnl, tier FROM wallet
        WHERE first_seen_at > :b ORDER BY first_seen_at DESC LIMIT 10"""), {"b": baseline}).mappings().all()
    return {
        "freshness": data_as_of(session),
        "baseline_import_at": baseline,
        "counts": dict(counts),
        "tiers": tiers,
        "flags": flags,
        "screening": {
            "criteria": {"min_twr_30d": SCREEN_MIN_TWR, "max_drawdown_30d": SCREEN_MAX_DD,
                         "min_positive_period_ratio": SCREEN_MIN_POSITIVE},
            "rows": [{**dict(c), "account_value": num(c["account_value"]), "twr": num(c["twr"]),
                      "mdd": num(c["mdd"]), "positive_period_ratio": num(c["positive_period_ratio"]),
                      "month_pnl": num(c["month_pnl"])} for c in candidates],
        },
        "new_wallets": [{**dict(n), "account_value": num(n["account_value"]), "month_pnl": num(n["month_pnl"])}
                        for n in newest],
    }


@router.get("/data-health")
def data_health(session: Session = Depends(get_session)):
    settings = get_settings()
    runs = session.execute(text("""
        SELECT id, kind, status, started_at, finished_at, stats, error FROM ingest_run
        ORDER BY started_at DESC LIMIT 40""")).mappings().all()
    latest = session.execute(text("""
        SELECT DISTINCT ON (kind) kind, status, started_at, finished_at FROM ingest_run
        WHERE status IN ('success', 'not_modified', 'partial') ORDER BY kind, started_at DESC""")).mappings().all()
    tables = {}
    for t in ("wallet", "leaderboard_snapshot", "wallet_metric", "account_snapshot", "raw_object",
              "wallet_tier_history", "position_cycle", "market_file"):
        tables[t] = session.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
    # Large append-only tables: planner estimate (exact counts would scan millions of rows).
    for t, n in session.execute(text("""
        SELECT relname, greatest(reltuples, 0)::bigint FROM pg_class
        WHERE relname IN ('leaderboard_row', 'equity_point', 'position_snapshot', 'fill', 'trade_decision',
                          'market_candle')""")).all():
        tables[f"{t} (≈)"] = n
    raw = session.execute(text("""
        SELECT source, count(*) AS n, sum(size_bytes) AS bytes FROM raw_object GROUP BY source""")).mappings().all()
    weight_24h = session.execute(text("""
        SELECT coalesce(sum((stats->'api'->>'weight')::numeric), 0) FROM ingest_run
        WHERE kind = 'enrich' AND started_at > now() - interval '24 hours'""")).scalar_one()
    # Binance coverage comes from the import ledger (scanning millions of candles would be slow);
    # Hyperliquid candles are a small, index-bounded range.
    market = session.execute(text("""
        SELECT 'binance_um' AS source, coin, sum(rows) AS m1, min(period) AS first_t, max(period) AS last_t
        FROM market_file WHERE source = 'binance_um' GROUP BY coin
        UNION ALL
        SELECT 'hyperliquid', coin, count(*) FILTER (WHERE interval = '1m'), min(t)::date::text, max(t)::text
        FROM market_candle WHERE source = 'hyperliquid' GROUP BY coin
        ORDER BY 1, 3 DESC""")).mappings().all()
    symbols = session.execute(text("""
        SELECT coin, symbol, status, median_ratio, basis_p95_bps, n_samples, checked_at, notes FROM market_symbol
        ORDER BY status, coin""")).mappings().all()
    return {
        "freshness": data_as_of(session),
        "market_data": [dict(m) for m in market],
        "symbols": [{**dict(r), "median_ratio": num(r["median_ratio"]), "basis_p95_bps": num(r["basis_p95_bps"])}
                    for r in symbols],
        "latest_success": [dict(r) for r in latest],
        "runs": [dict(r) for r in runs],
        "tables": tables,
        "raw_archive": [{**dict(r), "bytes": int(r["bytes"] or 0)} for r in raw],
        "api_budget": {
            "ip_limit_per_minute": settings.hl_weight_per_minute,
            "safety_share": settings.hl_weight_safety,
            "weight_used_24h": float(weight_24h),
            "weight_capacity_24h": settings.hl_weight_per_minute * settings.hl_weight_safety * 60 * 24,
        },
        "schedule": {
            "leaderboard_interval_minutes": settings.leaderboard_interval_minutes,
            "leaderboard_snapshot_hours": settings.leaderboard_snapshot_hours,
            "enrich_interval_minutes": settings.enrich_interval_minutes,
            "enrich_batch_size": settings.enrich_batch_size,
            "portfolio_refresh_days": settings.portfolio_refresh_days,
        },
    }
