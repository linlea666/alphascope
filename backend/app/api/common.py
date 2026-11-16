from datetime import datetime
from decimal import Decimal

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


def baseline_import_at(session: Session) -> datetime | None:
    """first_seen_at of the initial bulk import; wallets seen at this instant are not 'newly discovered'."""
    return session.execute(text("SELECT min(first_seen_at) FROM wallet")).scalar()
