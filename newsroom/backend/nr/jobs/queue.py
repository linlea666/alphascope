"""A small Postgres job queue: SELECT ... FOR UPDATE SKIP LOCKED with leases. AI work waits here when a model is
down; nothing else depends on it finishing."""

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

AI_KINDS = ("eic_evaluate", "draft_generate")


def enqueue(s: Session, kind: str, payload: dict, dedupe_key: str | None = None, priority: int = 100,
            run_after: datetime | None = None, max_attempts: int = 4, now: datetime | None = None) -> int | None:
    return s.execute(text("""
        INSERT INTO job (kind, payload, dedupe_key, priority, run_after, max_attempts)
        VALUES (:k, CAST(:p AS jsonb), :d, :pr, coalesce(:ra, :now, now()), :m)
        ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING RETURNING id"""),
        {"k": kind, "p": json.dumps(payload), "d": dedupe_key, "pr": priority, "ra": run_after, "m": max_attempts,
         "now": now}
    ).scalar()


def claim(s: Session, kinds: tuple[str, ...], worker: str, now: datetime, lease_minutes: int = 10) -> dict | None:
    row = s.execute(text("""
        UPDATE job SET status = 'running', attempts = attempts + 1, locked_by = :w, lease_until = :l
        WHERE id = (SELECT id FROM job WHERE status = 'queued' AND run_after <= :n AND kind = ANY(:k)
                    ORDER BY priority, id FOR UPDATE SKIP LOCKED LIMIT 1)
        RETURNING id, kind, payload, attempts, max_attempts"""),
        {"w": worker, "l": now + timedelta(minutes=lease_minutes), "n": now, "k": list(kinds)}).mappings().first()
    return dict(row) if row else None


def complete(s: Session, job_id: int, now: datetime, result: dict | None = None) -> None:
    s.execute(text("""UPDATE job SET status = 'done', finished_at = :n, lease_until = NULL, result = CAST(:r AS jsonb)
                      WHERE id = :i"""), {"n": now, "i": job_id, "r": json.dumps(result or {}, default=str)})


def fail(s: Session, job: dict, error: str, now: datetime, retry: bool = True, delay: timedelta | None = None) -> None:
    dead = not retry or job["attempts"] >= job["max_attempts"]
    s.execute(text("""UPDATE job SET status = :st, last_error = :e, lease_until = NULL, run_after = :ra,
                             finished_at = CASE WHEN :dead THEN :n END WHERE id = :i"""),
              {"st": "dead" if dead else "queued", "e": error[:2000], "i": job["id"], "dead": dead, "n": now,
               "ra": now + (delay or timedelta(minutes=2 ** job["attempts"]))})


def defer(s: Session, job: dict, until: datetime, reason: str) -> None:
    """Put a job back without counting the attempt (budget exhausted, emergency stop)."""
    s.execute(text("""UPDATE job SET status = 'queued', attempts = attempts - 1, run_after = :u, lease_until = NULL,
                             last_error = :r WHERE id = :i"""), {"u": until, "r": reason, "i": job["id"]})


def recover_expired(s: Session, now: datetime) -> int:
    return s.execute(text("""UPDATE job SET status = 'queued', lease_until = NULL, last_error = 'lease expired'
                             WHERE status = 'running' AND lease_until < :n"""), {"n": now}).rowcount
