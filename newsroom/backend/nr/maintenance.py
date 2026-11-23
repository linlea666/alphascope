"""Housekeeping: expired leases, expired account authorizations, pruning of transient rows."""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import audit, settings_store
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
        # "过期作废": events nobody covered whose last observation is older than the refresh horizon
        horizon = now - timedelta(hours=settings_store.get(s, "detect")["refresh_hours"])
        expired_events = s.execute(text("""
            SELECT e.id FROM news_event e LEFT JOIN source_event se ON se.id = e.last_source_event_id
            WHERE e.state IN ('detected', 'blocked', 'dismissed') AND coalesce(se.observed_at, e.detected_at) < :h"""),
            {"h": horizon}).scalars().all()
        for eid in expired_events:
            transition(s, eid, "archived", reason="expired: no longer timely")
        out["expired_events"] = len(expired_events)
        # story engine: quiet for a week -> dormant while a position is still open, closed once none is
        changed = s.execute(text("""
            UPDATE news_story st SET status = CASE WHEN EXISTS (SELECT 1 FROM position_episode ep
                                                               WHERE ep.trader_id = st.trader_id AND ep.status = 'open')
                                                  THEN 'dormant' ELSE 'closed' END,
                   closed_at = CASE WHEN NOT EXISTS (SELECT 1 FROM position_episode ep WHERE ep.trader_id = st.trader_id
                                                     AND ep.status = 'open') THEN :n END
            WHERE coalesce(st.last_event_at, st.opened_at) < :quiet
              AND (st.status = 'active' OR (st.status = 'dormant' AND NOT EXISTS (
                   SELECT 1 FROM position_episode ep WHERE ep.trader_id = st.trader_id AND ep.status = 'open')))
            RETURNING st.status"""), {"n": now, "quiet": now - timedelta(days=7)}).scalars().all()
        out["stories"] = {"dormant": changed.count("dormant"), "closed": changed.count("closed")}
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
