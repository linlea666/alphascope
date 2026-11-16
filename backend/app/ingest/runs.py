from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

# No single run legitimately lasts this long (a 200-wallet enrich batch takes ~8 minutes).
STALE_AFTER = timedelta(hours=2)


def mark_abandoned_runs(sf: sessionmaker[Session], now: datetime | None = None) -> int:
    """Runs left 'running' by a killed process are closed as 'abandoned' so health stats stay truthful."""
    now = now or datetime.now(UTC)
    with sf() as s:
        n = s.execute(text("""
            UPDATE ingest_run SET status = 'abandoned', finished_at = :now,
              error = coalesce(error, 'process ended before the run finished')
            WHERE status = 'running' AND started_at < :cutoff"""), {"now": now, "cutoff": now - STALE_AFTER}).rowcount
        s.commit()
    return n
