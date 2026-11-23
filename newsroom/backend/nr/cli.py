"""newsroom CLI: python -m nr.cli <command>. Every command prints JSON."""

import argparse
import getpass
import json
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import text

from nr import audit, settings_store
from nr.auth.passwords import hash_password, password_problem
from nr.config import get_settings
from nr.db import session_factory
from nr.detect.run import process_pending
from nr.feed.client import FeedClient
from nr.feed.health import overview, poll_health
from nr.feed.sync import ensure_source, sync_events
from nr.jobs.handlers import DETERMINISTIC, default_deps, run_pending
from nr.jobs.queue import AI_KINDS
from nr.seed import seed


def _password(args) -> str:
    pw = os.environ.get("NEWSROOM_ADMIN_PASSWORD") or (args.password if hasattr(args, "password") else None) \
        or getpass.getpass("password: ")
    if problem := password_problem(pw):
        sys.exit(problem)
    return pw


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="newsroom")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--username", default="admin")
    a.add_argument("--password")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    sub.add_parser("seed")
    sub.add_parser("check-feed")
    sub.add_parser("sync")
    sub.add_parser("jobs")
    sub.add_parser("status")
    e = sub.add_parser("emergency-stop")
    e.add_argument("state", choices=["on", "off"])
    e.add_argument("--reason", default="cli")
    args = p.parse_args(argv)
    sf, st, now = session_factory(), get_settings(), datetime.now(UTC)
    out: dict
    with sf() as s:
        if args.cmd == "create-admin":
            if s.execute(text("SELECT 1 FROM app_user WHERE lower(username) = lower(:u)"), {"u": args.username}).scalar():
                sys.exit(f"user {args.username} exists; use reset-password")
            uid = s.execute(text("""INSERT INTO app_user (username, password_hash, must_change_password)
                                    VALUES (:u, :p, true) RETURNING id"""),
                            {"u": args.username, "p": hash_password(_password(args))}).scalar_one()
            s.execute(text("INSERT INTO user_role VALUES (:u, 'admin'), (:u, 'editor_in_chief')"), {"u": uid})
            audit.record(s, "user.create_admin", "user", uid, actor_kind="system")
            out = {"user_id": uid, "username": args.username, "must_change_password": True}
        elif args.cmd == "reset-password":
            n = s.execute(text("""UPDATE app_user SET password_hash = :p, must_change_password = true, failed_logins = 0,
                                         locked_until = NULL WHERE lower(username) = lower(:u)"""),
                          {"p": hash_password(_password(args)), "u": args.username}).rowcount
            audit.record(s, "user.reset_password", "user", args.username, actor_kind="system")
            out = {"updated": n}
        elif args.cmd == "seed":
            out = seed(s)
        elif args.cmd == "check-feed":
            with FeedClient(st.feed_base_url, st.feed_token, st.feed_timeout_seconds) as c:
                out = {"meta": c.meta(), "health": c.health()["sources"]["market_event"]}
        elif args.cmd == "sync":
            sid = ensure_source(s, st.feed_base_url)
            s.commit()
            with FeedClient(st.feed_base_url, st.feed_token, st.feed_timeout_seconds) as c:
                poll_health(sf, c, sid, now)
                out = {"sync": sync_events(sf, c, sid, now), "detect": process_pending(sf, now)}
        elif args.cmd == "jobs":
            deps = default_deps()
            out = {"deterministic": run_pending(sf, deps, DETERMINISTIC), "ai": run_pending(sf, deps, AI_KINDS)}
        elif args.cmd == "status":
            out = {"source": overview(s, now),
                   "events": dict(s.execute(text("SELECT state, count(*) FROM news_event GROUP BY 1")).all()),
                   "jobs": dict(s.execute(text("SELECT status, count(*) FROM job GROUP BY 1")).all()),
                   "emergency_stop": settings_store.get(s, "emergency_stop")}
        else:
            on = args.state == "on"
            out = settings_store.put(s, "emergency_stop", {"active": on, "reason": args.reason,
                                                           "since": now.isoformat() if on else None})
            audit.record(s, f"emergency_stop.{args.state}", "setting", "emergency_stop", actor_kind="system",
                         reason=args.reason)
        s.commit()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
