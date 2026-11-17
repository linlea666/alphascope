"""OKX boards: today's verified 7-day board (or the top of the longer boards), today's newcomers, and traders who
have dropped off but whose history is archived. Each row carries our own evidence next to the official numbers:
realized PnL from closed positions, liquidations, history coverage and the best timing score."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import settings_store
from app.api.common import num
from app.db import get_session
from app.research.timing import TIMING_VERSION

router = APIRouter(prefix="/api/v1/okx", tags=["okx"])

EVIDENCE_SQL = """
    w.address, w.display_name, w.avatar_url, t.history_status, t.pool_status, t.n_positions, t.oldest_at, t.asset,
    p.seen_days, p.streak, p.first_seen_at, p.best_rank, p.currently_seen,
    (SELECT sum(realized_pnl) FROM okx_position x WHERE x.wallet_id = w.id AND x.close_type <> 'partClose'
       AND x.close_time > now() - interval '30 days') AS realized_30d,
    (SELECT sum(realized_pnl) FROM okx_position x WHERE x.wallet_id = w.id AND x.close_type <> 'partClose'
       AND x.close_time > now() - interval '90 days') AS realized_90d,
    (SELECT count(*) FROM okx_position x WHERE x.wallet_id = w.id AND x.liquidated
       AND x.close_time > now() - interval '90 days') AS liquidations_90d,
    (SELECT max(edge_shrunk) FROM timing_score s WHERE s.wallet_id = w.id AND s.status = 'scored'
       AND s.coin_group = 'all' AND s.version = :tv AND s.q_value < 0.1) AS best_edge"""
NUMERIC = ("asset", "pnl", "pnl_ratio", "win_rate", "max_drawdown", "realized_30d", "realized_90d", "best_edge")


def _row(r) -> dict:
    return {k: (num(v) if k in NUMERIC else v) for k, v in dict(r).items()}


@router.get("/board")
def okx_board(
    session: Session = Depends(get_session),
    window: Literal["7d", "30d", "90d", "365d"] = "7d",
    view: Literal["board", "new", "dropped"] = "board",
    limit: int = Query(300, ge=1, le=1000),
):
    tz = settings_store.get(session, "live").get("timezone") or "Asia/Shanghai"
    run = session.execute(text("""
        SELECT id, business_date, completed_at, expected_total, unique_count, new_traders, source, data_version
        FROM okx_leaderboard_run WHERE time_window = :w AND status = 'complete'
        ORDER BY business_date DESC, completed_at DESC LIMIT 1"""), {"w": window}).mappings().first()
    runs = session.execute(text("""
        SELECT business_date, count(*) FILTER (WHERE status = 'complete') AS complete,
               count(*) FILTER (WHERE status = 'failed') AS failed
        FROM okx_leaderboard_run WHERE time_window = :w GROUP BY 1 ORDER BY 1 DESC LIMIT 14"""),
        {"w": window}).mappings().all()
    params = {"tv": TIMING_VERSION, "w": window, "n": limit, "tz": tz}
    if view == "dropped":
        rows = session.execute(text(f"""
            SELECT NULL AS rank, NULL AS pnl, NULL AS pnl_ratio, NULL AS win_rate, NULL AS max_drawdown,
                   p.last_seen_at, false AS is_new, {EVIDENCE_SQL}
            FROM okx_trader t JOIN wallet w ON w.id = t.wallet_id
            LEFT JOIN okx_presence p ON p.wallet_id = w.id AND p.time_window = :w
            WHERE t.pool_status IN ('dropped', 'inactive', 'watching') AND coalesce(p.currently_seen, false) = false
              AND t.history_status IN ('target_complete', 'source_complete') AND t.n_positions >= 20
            ORDER BY realized_90d DESC NULLS LAST LIMIT :n"""), params).mappings().all()
    elif run is None:
        rows = []
    else:
        new_only = "AND (p.first_seen_at AT TIME ZONE :tz)::date = :d" if view == "new" else ""
        rows = session.execute(text(f"""
            SELECT r.rank, r.pnl, r.pnl_ratio, r.win_rate, r.max_drawdown, p.last_seen_at,
                   (p.first_seen_at AT TIME ZONE :tz)::date = :d AS is_new, {EVIDENCE_SQL}
            FROM okx_leaderboard_row r JOIN wallet w ON w.id = r.wallet_id
            LEFT JOIN okx_trader t ON t.wallet_id = w.id
            LEFT JOIN okx_presence p ON p.wallet_id = w.id AND p.time_window = :w
            WHERE r.run_id = :run {new_only}
            ORDER BY r.rank LIMIT :n"""), {**params, "run": run["id"], "d": run["business_date"]}).mappings().all()
    return {
        "window": window, "view": view, "timezone": tz,
        "run": dict(run) if run else None,
        "runs": [dict(r) for r in runs],
        "rows": [_row(r) for r in rows],
    }
