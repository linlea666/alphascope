"""Review and publication. Publishing is manual in Phase 1: an operator posts on X, then records the post here.
Every check that protects readers runs again at this point: the guard on the final text, the event's gate, the
account's authorization and budget, and the emergency stop."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.articles import versions
from nr.events.states import InvalidTransition, transition

X_URL = re.compile(r"^https://(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})(?:[/?#].*)?$")


def submit(s: Session, article_id: int, user_id: int) -> None:
    art = versions.load(s, article_id)
    if art["status"] not in ("draft_ready", "changes_requested") or not art["current_version_id"]:
        raise HTTPException(409, f"article is {art['status']}")
    versions.set_status(s, article_id, "in_review")
    audit.record(s, "article.submit", "article", article_id, user_id=user_id, account_id=art["account_id"])


def live_guard(s: Session, art: dict, v: dict) -> dict:
    return versions.guard(s, art, v["fact_packet_id"], versions.parts_of(v["headline"], v["body"], v["thread"]),
                          list(v["facts_used"]))


def approve(s: Session, article_id: int, user_id: int, ack_duplicate: bool = False) -> dict:
    art = versions.load(s, article_id)
    if art["status"] != "in_review":
        raise HTTPException(409, f"article is {art['status']}")
    v = versions.current_version(s, article_id)
    if settings_store.get(s, "review")["four_eyes"] and v["author_user_id"] == user_id and v["origin"] == "human":
        raise HTTPException(409, "the author of the current version cannot approve it")
    if art["gate_status"] != "pass":
        raise HTTPException(409, "the event's credibility gate is not passing")
    report = live_guard(s, art, v)
    if report["status"] == "fail":
        raise HTTPException(409, {"message": "claims guard failed", "guard": report})
    warns = [f for f in report["findings"] if f["code"] == "NEAR_DUPLICATE_WARN"]
    if warns and not ack_duplicate:
        raise HTTPException(409, {"message": "similar to another article: acknowledge to approve", "guard": report})
    s.execute(text("UPDATE article SET status = 'approved', duplicate_ack_by = :u, updated_at = now() WHERE id = :a"),
              {"u": user_id if warns else None, "a": article_id})
    audit.record(s, "article.approve", "article", article_id, user_id=user_id, account_id=art["account_id"],
                 after={"version_id": v["id"], "guard": report["status"], "duplicate_ack": bool(warns)})
    return report


def request_changes(s: Session, article_id: int, user_id: int, note: str) -> None:
    art = versions.load(s, article_id)
    if art["status"] not in ("in_review", "approved"):
        raise HTTPException(409, f"article is {art['status']}")
    versions.set_status(s, article_id, "changes_requested")
    audit.record(s, "article.request_changes", "article", article_id, user_id=user_id, account_id=art["account_id"],
                 reason=note)


def kill(s: Session, article_id: int, user_id: int, reason: str) -> None:
    art = versions.load(s, article_id)
    if art["status"] in ("published", "retracted", "killed"):
        raise HTTPException(409, f"article is {art['status']}")
    versions.set_status(s, article_id, "killed")
    audit.record(s, "article.kill", "article", article_id, user_id=user_id, account_id=art["account_id"], reason=reason)


def publish_blockers(s: Session, account_id: int, now: datetime) -> list[str]:
    acc = s.execute(text("SELECT * FROM editorial_account WHERE id = :a"), {"a": account_id}).mappings().one()
    out = []
    if settings_store.emergency_stopped(s):
        out.append("emergency stop is active")
    if acc["status"] != "active":
        out.append(f"account is {acc['status']}")
    if acc["authorization_status"] != "authorized":
        out.append(f"account authorization is {acc['authorization_status']}")
    elif acc["authorization_expires_at"] is not None and acc["authorization_expires_at"] <= now:
        out.append("account authorization has expired")
    tz = ZoneInfo(acc["timezone"])
    day_start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today = s.execute(text("""SELECT count(*) FROM publication_record WHERE account_id = :a AND published_at >= :d"""),
                      {"a": account_id, "d": day_start}).scalar()
    if today >= acc["daily_post_budget"]:
        out.append(f"daily post budget reached ({today}/{acc['daily_post_budget']})")
    return out


def mark_published(s: Session, article_id: int, user_id: int, x_url: str, published_at: datetime, now: datetime,
                   final_text: str | None = None) -> int:
    art = versions.load(s, article_id)
    if art["status"] != "approved":
        raise HTTPException(409, f"article is {art['status']}, only approved articles can be published")
    blockers = publish_blockers(s, art["account_id"], now)
    if blockers:
        raise HTTPException(423, "; ".join(blockers))
    m = X_URL.match(x_url.strip())
    if not m:
        raise HTTPException(422, "not an X post URL (https://x.com/<handle>/status/<id>)")
    if m.group(1).lower() != art["handle"].lstrip("@").lower():
        raise HTTPException(422, f"the post belongs to @{m.group(1)}, not to @{art['handle'].lstrip('@')}")
    if published_at > now + timedelta(minutes=5):
        raise HTTPException(422, "published_at is in the future")
    v = versions.current_version(s, article_id)
    if art["gate_status"] != "pass" or v["fact_packet_id"] != art["current_fact_packet_id"]:
        raise HTTPException(409, "the facts changed after approval: review again")
    version_text = versions.parts_of(v["headline"], v["body"], v["thread"])
    posted = final_text if final_text is not None else "\n\n".join(t for k, t in version_text.items()
                                                                  if k != "headline" and t)
    parts = {"body": posted} if final_text is not None else version_text
    report = versions.guard(s, art, v["fact_packet_id"], parts, list(v["facts_used"]))
    hard = [f for f in report["findings"] if f["severity"] == "fail" and f["code"] != "NEAR_DUPLICATE"]
    if hard:
        raise HTTPException(409, {"message": "the posted text fails the claims guard", "guard": report})
    h = hashlib.sha256(posted.encode()).hexdigest()
    pid = s.execute(text("""
        INSERT INTO publication_record (article_id, article_version_id, account_id, x_post_id, x_url, published_at,
          recorded_by, final_text, final_text_hash, matches_version, guard_report)
        VALUES (:a, :v, :acc, :pid, :url, :at, :u, :t, :h, :mv, CAST(:g AS jsonb)) RETURNING id"""),
        {"a": article_id, "v": v["id"], "acc": art["account_id"], "pid": m.group(2), "url": x_url.strip(),
         "at": published_at, "u": user_id, "t": posted, "h": h, "mv": final_text is None or posted == v["body"],
         "g": json.dumps(report, ensure_ascii=False, default=str)}).scalar_one()
    versions.set_status(s, article_id, "published")
    s.execute(text("UPDATE assignment SET status = 'done' WHERE id = :i"), {"i": art["assignment_id"]})
    try:
        transition(s, art["news_event_id"], "covered", actor_kind="user", user_id=user_id, reason=f"published {pid}")
    except InvalidTransition:
        pass  # already covered by another account
    s.execute(text("""UPDATE story_event SET pinned_fact_packet_id = coalesce(pinned_fact_packet_id, :p)
                      WHERE news_event_id = :e"""), {"p": v["fact_packet_id"], "e": art["news_event_id"]})
    audit.record(s, "publication.record", "publication", pid, user_id=user_id, account_id=art["account_id"],
                 after={"article_id": article_id, "version_id": v["id"], "x_url": x_url, "matches_version":
                        final_text is None or posted == v["body"]})
    return pid


def retract(s: Session, publication_id: int, user_id: int, reason: str, now: datetime) -> None:
    row = s.execute(text("""UPDATE publication_record SET status = 'retracted', retracted_at = :n, retracted_by = :u,
                                   retract_reason = :r WHERE id = :i AND status = 'published'
                            RETURNING article_id, account_id"""),
                    {"n": now, "u": user_id, "r": reason, "i": publication_id}).mappings().first()
    if row is None:
        raise HTTPException(409, "publication is not live")
    versions.set_status(s, row["article_id"], "retracted")
    audit.record(s, "publication.retract", "publication", publication_id, user_id=user_id,
                 account_id=row["account_id"], reason=reason)
