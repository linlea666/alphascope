"""Housekeeping: expired leases, expired account authorizations, pruning of transient rows."""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import audit
from nr.events.states import transition
from nr.jobs.queue import recover_expired


def run(sf: sessionmaker, now: datetime) -> dict:
    with sf() as s:
        out = {"leases": recover_expired(s, now)}
        expired = s.execute(text("""UPDATE editorial_account SET authorization_status = 'expired', updated_at = now()
                                    WHERE authorization_status = 'authorized' AND authorization_expires_at < :n
                                    RETURNING id"""), {"n": now}).scalars().all()
        for a in expired:
            audit.record(s, "account.authorization_expired", "account", a, actor_kind="system", account_id=a)
        out["expired_accounts"] = len(expired)
        # an evaluation that crashed mid-way would otherwise leave its event "evaluating" forever
        stuck = s.execute(text("""
            SELECT e.id FROM news_event e WHERE e.state = 'evaluating' AND e.state_changed_at < :t
              AND NOT EXISTS (SELECT 1 FROM job j WHERE j.kind = 'eic_evaluate' AND j.status IN ('queued', 'running')
                              AND (j.payload->>'event_id')::bigint = e.id)"""), {"t": now - timedelta(minutes=30)}
        ).scalars().all()
        for eid in stuck:
            transition(s, eid, "candidate", reason="evaluation did not finish; back to the pool")
        out["unstuck_events"] = len(stuck)
        out["pruned"] = sum(s.execute(text(q), {"t": now - timedelta(days=d)}).rowcount for q, d in (
            ("DELETE FROM ingestion_checkpoint_history WHERE recorded_at < :t", 7),
            ("DELETE FROM source_health_snapshot h WHERE at < :t AND NOT EXISTS (SELECT 1 FROM event_evidence ev "
             "WHERE ev.kind = 'health_snapshot' AND ev.ref_id = h.id)", 14),
            ("DELETE FROM sync_run WHERE started_at < :t", 30),
            ("DELETE FROM user_session WHERE expires_at < :t", 7),
            ("DELETE FROM job j WHERE status IN ('done', 'cancelled') AND finished_at < :t "
             "AND NOT EXISTS (SELECT 1 FROM ai_call_log c WHERE c.job_id = j.id)", 30)))
        s.commit()
    return out
