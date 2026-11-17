from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session


def num(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)


def data_as_of(session: Session) -> dict:
    """Freshness block attached to every response so the UI can always show 'data as of'."""
    row = session.execute(text("""
        SELECT max(finished_at) FILTER (WHERE kind = 'leaderboard' AND status IN ('success', 'not_modified')),
               max(finished_at) FILTER (WHERE kind = 'enrich' AND status IN ('success', 'partial')),
               (SELECT max(fetched_at) FROM leaderboard_snapshot)
        FROM ingest_run""")).one()
    return {"leaderboard_checked_at": row[0], "enrichment_at": row[1], "leaderboard_snapshot_at": row[2]}


VenueFilter = Literal["all", "hyperliquid", "okx"]
# OKX history status counts as "synced" for open-cycle handling: a still-open OKX cycle is a real open position
SYNC_STATUS_SQL = """coalesce(f.status, CASE WHEN o.wallet_id IS NOT NULL THEN 'synced' END)"""


def venue_clause(venue: str, alias: str = "w") -> str:
    return "" if venue == "all" else f" AND {alias}.venue = '{'okx' if venue == 'okx' else 'hyperliquid'}'"


def baseline_import_at(session: Session) -> datetime | None:
    """first_seen_at of the initial bulk import; wallets seen at this instant are not 'newly discovered'."""
    return session.execute(text("SELECT min(first_seen_at) FROM wallet WHERE venue = 'hyperliquid'")).scalar()
