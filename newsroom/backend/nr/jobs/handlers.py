"""Job handlers and a runner loop shared by the worker, the CLI and tests."""

import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.ai.runner import AIUnavailable, BudgetExceeded
from nr.config import get_settings
from nr.facts.build import RateLimited, build_event_facts
from nr.feed.client import FeedClient
from nr.jobs import queue

log = logging.getLogger(__name__)
DETERMINISTIC = ("build_facts", "card_render", "column_build", "experiment_create")


@dataclass
class Deps:
    feed: Callable[[], FeedClient]
    provider: Callable | None = None  # AI transport override (tests); None = real HTTP providers
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def default_deps() -> Deps:
    st = get_settings()
    return Deps(feed=lambda: FeedClient(st.feed_base_url, st.feed_token, st.feed_timeout_seconds))


def handle(sf: sessionmaker, deps: Deps, job: dict, now: datetime) -> dict:
    kind, p = job["kind"], job["payload"]
    if kind == "build_facts":
        with sf() as s:
            col = s.execute(text("SELECT column_key, column_params, coin FROM news_event WHERE id = :e"),
                            {"e": p["event_id"]}).mappings().first()
        if col and col["column_key"]:  # a column issue is rebuilt by its column, not from a position
            from nr.columns.run import build_column
            params = {**(col["column_params"] or {}), **({"coins": [col["coin"]]} if col["coin"] != "*" else {})}
            with deps.feed() as c:
                return build_column(sf, c, col["column_key"], now, params)
        with deps.feed() as c:
            return build_event_facts(sf, c, p["event_id"], now, get_settings().context_calls_per_hour)
    if kind == "eic_evaluate":
        from nr.desk.eic import evaluate_event
        return evaluate_event(sf, p["event_id"], now, job_id=job["id"], transport=deps.provider)
    if kind == "draft_generate" and p.get("experiment_pair"):
        from nr.experiments.contrast_ab import run_arm
        return run_arm(sf, p, now, job_id=job["id"], transport=deps.provider)
    if kind == "draft_generate":
        from nr.desk.drafting import generate_draft
        return generate_draft(sf, p["assignment_id"], now, job_id=job["id"], transport=deps.provider,
                              user_id=p.get("user_id"), mode=p.get("mode", "draft"),
                              model_config_id=p.get("model_config_id"), compare=p.get("compare", False),
                              style_key=p.get("style_key"), prompt_version=p.get("prompt_version"),
                              review_note=p.get("review_note"), review_issues=p.get("review_issues"),
                              sequel=p.get("sequel", False))
    if kind == "draft_review":
        from nr.desk.review import review_version
        return review_version(sf, p["version_id"], now, job_id=job["id"], transport=deps.provider)
    if kind == "column_build":
        from nr.columns.run import build_column
        with deps.feed() as c:
            return build_column(sf, c, p["column"], now, p.get("params") or {})
    if kind == "experiment_create":
        from nr.experiments.contrast_ab import create
        with deps.feed() as c:
            return create(sf, c, p["name"], p["account_id"], p["model_config_id"], p["n"], now, p["user_id"],
                          arms=p.get("arms"))
    if kind == "card_render":
        from nr.cards.render import render_for_packet
        return render_for_packet(sf, p["fact_packet_id"], p["template"], p["language"], p.get("brand", "default"))
    raise ValueError(f"unknown job kind {kind}")


def run_pending(sf: sessionmaker, deps: Deps, kinds: tuple[str, ...], max_jobs: int = 50,
                worker: str | None = None) -> list[dict]:
    worker = worker or f"{socket.gethostname()}:{kinds[0]}"
    done = []
    for _ in range(max_jobs):
        now = deps.now()
        with sf() as s:
            if set(kinds) & set(queue.AI_KINDS) and settings_store.emergency_stopped(s):
                return done  # AI work waits while the emergency stop is on
            job = queue.claim(s, kinds, worker, now)
            s.commit()
        if job is None:
            break
        try:
            result = handle(sf, deps, job, now)
            with sf() as s:
                queue.complete(s, job["id"], deps.now(), result)
                s.commit()
            done.append({"job": job["id"], "kind": job["kind"], "result": result})
        except RateLimited as exc:
            with sf() as s:
                queue.defer(s, job, now + timedelta(minutes=10), str(exc))
                s.commit()
            break
        except (AIUnavailable, BudgetExceeded) as exc:
            # no model configured yet, AI switched off or budget spent: wait without using up the job's retries
            with sf() as s:
                queue.defer(s, job, now + timedelta(minutes=30), f"{type(exc).__name__}: {exc}")
                s.commit()
            break
        except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
            log.exception("job %s %s failed", job["id"], job["kind"])
            with sf() as s:
                queue.fail(s, job, f"{type(exc).__name__}: {exc}", deps.now())
                s.commit()
            done.append({"job": job["id"], "kind": job["kind"], "error": str(exc)})
    return done
