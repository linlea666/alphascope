import logging
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app import worker


def schedule(sf, monkeypatch):
    jobs = {}

    def start(self):
        # pending jobs only carry next_run_time when it was given explicitly
        now = datetime.now(UTC)
        jobs.update({j.id: (j.trigger, getattr(j, "next_run_time", None) or j.trigger.get_next_fire_time(None, now))
                     for j in self.get_jobs()})
        raise SystemExit

    monkeypatch.setattr(worker, "session_factory", lambda: sf)
    monkeypatch.setattr(BlockingScheduler, "start", start)
    worker.main()
    return jobs


def run(sf, kind, finished):
    with sf.begin() as s:
        s.execute(text("""INSERT INTO ingest_run(kind,status,started_at,finished_at,stats)
            VALUES(:k,'success',:t,:t,'{}')"""), {"k": kind, "t": finished})


def test_restart_keeps_clock_times_and_catches_up_only_missed_batches(sf, monkeypatch):
    now = datetime.now(UTC)
    run(sf, "styles", now - timedelta(hours=1))
    run(sf, "copy_analysis", now - timedelta(hours=30))
    jobs = schedule(sf, monkeypatch)
    assert not [j for j in jobs if "boot" in j]
    # research frames only at :55, so forecasts always find a frame at their due time
    assert isinstance(jobs["research_frames"][0], CronTrigger) and jobs["research_frames"][1].minute == 55
    for job in ("timing", "okx_timing", "swings", "styles", "copy_analysis"):
        assert isinstance(jobs[job][0], CronTrigger), job
    assert jobs["styles"][1].minute == 50  # ran an hour ago: no catch-up
    assert jobs["copy_analysis"][1] < now + timedelta(minutes=21)  # missed last night
    assert jobs["timing"][1] < now + timedelta(minutes=16)  # never ran


def test_failed_runs_do_not_count_as_finished(sf):
    with sf.begin() as s:
        s.execute(text("""INSERT INTO ingest_run(kind,status,started_at,finished_at,stats)
            VALUES('timing','failed',now(),now(),'{}')"""))
    assert not worker.finished_within(sf, "timing", 3)
    run(sf, "timing", datetime.now(UTC) - timedelta(hours=2))
    assert worker.finished_within(sf, "timing", 3) and not worker.finished_within(sf, "timing", 1)


def test_batch_child_failure_is_reported(caplog):
    with caplog.at_level(logging.ERROR, logger="alphascope.worker"):
        worker.isolated("unknown")()
    assert "unknown exited with 1" in caplog.text


@pytest.mark.parametrize("name", sorted(worker.BATCH))
def test_batch_jobs_are_named_for_the_scheduler_log(name):
    assert worker.isolated(name).__name__ == f"{name}_job"
