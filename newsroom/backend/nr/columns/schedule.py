"""When columns run: weekly columns on Mondays (UTC), monthly on the 1st, daily and hourly ones every tick. A period
whose issue is missing or blocked is retried on the next tick of the same day."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.columns.base import day_key, month_key, week_key
from nr.columns.defs import COLUMNS
from nr.jobs.queue import enqueue


def due(cadence: str, now: datetime) -> str | None:
    """The period prefix to build now, or None when this cadence does not run today."""
    if cadence == "weekly":
        return week_key(now) if now.isoweekday() == 1 else None
    if cadence == "monthly":
        return month_key(now) if now.day == 1 else None
    if cadence in ("daily", "hourly"):
        return day_key(now) if cadence == "daily" else f"{day_key(now)}T{now:%H}"
    return None


def tick(sf: sessionmaker, now: datetime) -> list[str]:
    queued = []
    with sf() as s:
        disabled = set(settings_store.get(s, "columns")["disabled"])
        for key, col in COLUMNS.items():
            period = due(col.cadence, now)
            if period is None or key in disabled:
                continue
            if col.cadence in ("weekly", "monthly"):
                states = s.execute(text("""SELECT state FROM news_event WHERE column_key = :k AND period_key LIKE :p"""),
                                   {"k": key, "p": f"{period}%"}).scalars().all()
                if states and all(st != "blocked" for st in states):
                    continue
            if enqueue(s, "column_build", {"column": key}, dedupe_key=f"column:{key}:{period}", priority=70, now=now):
                queued.append(key)
        s.commit()
    return queued
