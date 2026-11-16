from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import baseline_import_at, data_as_of, num
from app.db import get_session
from app.models import LEADERBOARD_WINDOWS
from app.sources.leaderboard import ADDRESS_RE

router = APIRouter(prefix="/api/v1/wallets", tags=["wallets"])


@router.get("/new")
def new_wallets(session: Session = Depends(get_session), days: int = Query(7, ge=1, le=90),
                limit: int = Query(50, ge=1, le=200)):
    """Wallets first seen after the initial bulk import (true discoveries, not the seed set)."""
    baseline = baseline_import_at(session)
    rows = session.execute(text("""
        SELECT address, display_name, first_seen_at, account_value, month_pnl, month_vlm, tier, quality_flags
        FROM wallet
        WHERE first_seen_at > :baseline AND first_seen_at > now() - make_interval(days => :days)
        ORDER BY first_seen_at DESC, month_vlm DESC NULLS LAST
        LIMIT :limit"""), {"baseline": baseline, "days": days, "limit": limit}).all()
    return {
        "baseline_import_at": baseline,
        "freshness": data_as_of(session),
        "rows": [{"address": r.address, "display_name": r.display_name, "first_seen_at": r.first_seen_at,
                  "account_value": num(r.account_value), "month_pnl": num(r.month_pnl),
                  "month_vlm": num(r.month_vlm), "tier": r.tier, "flags": list(r.quality_flags or [])}
                 for r in rows],
    }


@router.get("/{address}")
def wallet_detail(address: str, session: Session = Depends(get_session)):
    address = address.lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(422, "invalid address")
    w = session.execute(text("SELECT * FROM wallet WHERE address = :a"), {"a": address}).mappings().first()
    if w is None:
        raise HTTPException(404, "wallet not in database")
    wid = w["id"]

    metrics = session.execute(text("""
        SELECT * FROM wallet_metric WHERE wallet_id = :w
        ORDER BY array_position(ARRAY['30d','90d','180d','365d','all'], metric_window)"""), {"w": wid}).mappings().all()
    equity = {}
    for series in ("month", "allTime"):
        pts = session.execute(text("""
            SELECT ts, account_value, pnl FROM equity_point WHERE wallet_id = :w AND series = :s ORDER BY ts"""),
            {"w": wid, "s": series}).all()
        equity[series] = [[p.ts, float(p.account_value), float(p.pnl)] for p in pts]
    snapshots = session.execute(text("""
        SELECT s.fetched_at, r.account_value, r.day_pnl, r.week_pnl, r.month_pnl, r.alltime_pnl,
               r.month_roi, r.month_vlm
        FROM leaderboard_row r JOIN leaderboard_snapshot s ON s.id = r.snapshot_id
        WHERE r.wallet_id = :w ORDER BY s.fetched_at DESC LIMIT 120"""), {"w": wid}).mappings().all()
    tiers = session.execute(text("""
        SELECT tier, previous_tier, rule_version, assigned_at FROM wallet_tier_history
        WHERE wallet_id = :w ORDER BY assigned_at DESC LIMIT 50"""), {"w": wid}).mappings().all()
    account = session.execute(text("""
        SELECT id, fetched_at, account_value, total_ntl_pos, total_margin_used, withdrawable
        FROM account_snapshot WHERE wallet_id = :w ORDER BY fetched_at DESC LIMIT 1"""), {"w": wid}).mappings().first()
    positions = []
    if account:
        positions = session.execute(text("""
            SELECT coin, szi, entry_px, position_value, unrealized_pnl, return_on_equity, leverage_type,
                   leverage_value, liquidation_px, margin_used
            FROM position_snapshot WHERE account_snapshot_id = :id ORDER BY abs(position_value) DESC NULLS LAST"""),
            {"id": account["id"]}).mappings().all()

    def clean(m) -> dict:
        return {k: (num(v) if hasattr(v, "is_finite") else v) for k, v in dict(m).items() if k not in ("id", "wallet_id")}

    return {
        "freshness": data_as_of(session),
        "wallet": {
            "address": w["address"],
            "display_name": w["display_name"],
            "first_seen_at": w["first_seen_at"],
            "first_seen_source": w["first_seen_source"],
            "last_seen_at": w["last_seen_at"],
            "present_in_latest": w["present_in_latest"],
            "removed_at": w["removed_at"],
            "account_value": num(w["account_value"]),
            "tier": w["tier"],
            "tier_rule_version": w["tier_rule_version"],
            "flags": list(w["quality_flags"] or []),
            "enrichment_flags": list(w["enrichment_flags"] or []),
            "portfolio_fetched_at": w["portfolio_fetched_at"],
            "leaderboard_fetched_at": w["leaderboard_fetched_at"],
        },
        "leaderboard": {
            win: {"pnl": num(w[f"{win}_pnl"]), "roi": num(w[f"{win}_roi"]), "vlm": num(w[f"{win}_vlm"])}
            for win in LEADERBOARD_WINDOWS
        },
        "metrics": [clean(m) for m in metrics],
        "equity": equity,
        "snapshots": [clean(s) for s in snapshots],
        "tier_history": [dict(t) for t in tiers],
        "account": clean(account) if account else None,
        "positions": [clean(p) for p in positions],
    }
