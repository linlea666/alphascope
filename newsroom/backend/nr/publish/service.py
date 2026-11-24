"""Review and publication. Publishing is manual in Phase 1: an operator posts on X, then records the post here.
Every check that protects readers runs again at this point: the guard on the final text, the event's gate, the
account's authorization and budget, and the emergency stop."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.articles import versions
from nr.events.states import InvalidTransition, transition

X_URL = re.compile(r"^https://(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})(?:[/?#].*)?$")


def post_id_for(s: Session, platform: str, handle: str, url: str) -> str:
    """The post's id from its URL, after checking the URL belongs to the account's platform (and, on X, to the
    account itself)."""
    if platform == "x":
        m = X_URL.match(url)
        if not m:
            raise HTTPException(422, "not an X post URL (https://x.com/<handle>/status/<id>)")
        if m.group(1).lower() != handle.lstrip("@").lower():
            raise HTTPException(422, f"the post belongs to @{m.group(1)}, not to @{handle.lstrip('@')}")
        return m.group(2)
    hosts = settings_store.get(s, "platforms").get(platform, {}).get("hosts", [])
    parsed = urlparse(url)
    if parsed.scheme != "https" or not any(parsed.hostname == h or (parsed.hostname or "").endswith("." + h)
                                           for h in hosts):
        raise HTTPException(422, f"not a {platform} post URL (expected https on {', '.join(hosts)})")
    ids = re.findall(r"\d{6,}", parsed.path)  # OKX Square post ids are long numbers; fall back to the URL itself
    return ids[-1] if ids else hashlib.sha256(url.encode()).hexdigest()[:24]


def submit(s: Session, article_id: int, user_id: int) -> None:
    art = versions.load(s, article_id)
    if art["deleted_at"] is not None:
        raise HTTPException(409, "the article is in the trash")
    if s.execute(text("SELECT source FROM assignment WHERE id = :i"), {"i": art["assignment_id"]}).scalar() == "trial":
        raise HTTPException(409, "a trial draft cannot go to review: assign the event to the account to use it")
    if art["status"] not in ("draft_ready", "changes_requested") or not art["current_version_id"]:
        raise HTTPException(409, f"article is {art['status']}")
    versions.set_status(s, article_id, "in_review")
    audit.record(s, "article.submit", "article", article_id, user_id=user_id, account_id=art["account_id"])


def live_guard(s: Session, art: dict, v: dict) -> dict:
    return versions.guard(s, art, v["fact_packet_id"], versions.parts_of(v["headline"], v["body"], v["thread"]),
                          list(v["facts_used"]))


def refresh_to_current_facts(s: Session, article_id: int, user_id: int) -> None:
    """A stale draft's text, checked again as a new version on the event's latest facts: if every figure still holds,
    it goes on; if not, the guard says which one moved (and a rewrite from fresh facts is the way forward)."""
    v = versions.current_version(s, article_id)
    out = versions.save_version(s, article_id, versions.Draft(
        body=v["body"], headline=v["headline"], alt_headlines=v["alt_headlines"], thread=v["thread"], angle=v["angle"],
        added_value=v["added_value"], difference_from_others=v["difference_from_others"],
        why_shareable=v["why_shareable"], risks=v["risks"], facts_used=v["facts_used"], why_now=v["why_now"],
        open_question=v["open_question"], lede_fact_ids=v["lede_fact_ids"], card_template=v["card_template"],
        style_key=v["style_key"], reply_text=v["reply_text"]),
        # the same text, by the same writer: only the facts it is checked against are newer
        origin=v["origin"], user_id=v["author_user_id"], agent_key=v["agent_key"], prompt_ref=v["prompt_ref"], model=v["model"])
    if out["guard_status"] == "fail":
        s.flush()
        raise HTTPException(409, {"message": "the facts moved: a figure in this draft no longer matches; rewrite it "
                                             "from the latest facts", "guard": out["guard"]})


def quick_approve(s: Session, article_id: int, user_id: int, ack_duplicate: bool = False) -> dict:
    """One click from a finished draft to ready-to-post: submit and approve with every check of both steps. A draft
    whose facts changed is re-checked on the latest ones first."""
    status = versions.load(s, article_id)["status"]
    if status == "stale":
        refresh_to_current_facts(s, article_id, user_id)
        status = versions.load(s, article_id)["status"]
    if status in ("draft_ready", "changes_requested"):
        submit(s, article_id, user_id)
    return approve(s, article_id, user_id, ack_duplicate=ack_duplicate)


def ai_approve(s: Session, article_id: int, review_id: int) -> str | None:
    """The desk reviewer approved the current version: ready-to-post under the same checks a person's approval runs.
    Returns why it stayed with a person instead (a trial, a failing gate or guard, a near-duplicate)."""
    art = versions.load(s, article_id)
    if art["status"] != "draft_ready":
        return f"article is {art['status']}"
    if s.execute(text("SELECT source FROM assignment WHERE id = :i"), {"i": art["assignment_id"]}).scalar() == "trial":
        return "trial drafts are promoted by a person"
    if art["gate_status"] != "pass":
        return "the event's credibility gate is not passing"
    report = live_guard(s, art, versions.current_version(s, article_id))
    if report["status"] == "fail" or any(f["code"] == "NEAR_DUPLICATE_WARN" for f in report["findings"]):
        return "the claims guard wants a person"
    versions.set_status(s, article_id, "approved")
    audit.record(s, "article.approve", "article", article_id, actor_kind="ai", account_id=art["account_id"],
                 after={"review_id": review_id, "guard": report["status"]})
    return None


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
    # authorization is optional (posts go out by hand, RPA or a browser extension): only an explicit revocation or
    # an expiry date that has passed stops publishing
    if acc["authorization_status"] in ("revoked", "expired"):
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


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def check_posted(s: Session, art: dict, v: dict, final_text: str | None) -> tuple[str, dict, bool]:
    """The last check, on what actually went out: when the posted text differs from the approved version (edited on
    X, or by a robot), the claims guard reads the posted text itself. Returns the text, the report, and whether it
    matches the approved version."""
    version_text = versions.parts_of(v["headline"], v["body"], v["thread"])
    approved = "\n\n".join(t for k, t in version_text.items() if k != "headline" and t)
    if final_text is None or not final_text.strip() or _same(final_text, approved) or _same(final_text, v["body"]):
        return approved, versions.guard(s, art, v["fact_packet_id"], version_text, list(v["facts_used"])), True
    return final_text, versions.guard(s, art, v["fact_packet_id"], {"body": final_text}, list(v["facts_used"])), False


def hard_findings(report: dict) -> list[dict]:
    return [f for f in report["findings"] if f["severity"] == "fail" and f["code"] != "NEAR_DUPLICATE"]


def mark_published(s: Session, article_id: int, user_id: int, x_url: str | None, published_at: datetime, now: datetime,
                   final_text: str | None = None, channel: str = "manual", reply_url: str | None = None,
                   api_token_id: int | None = None, allow_expired: bool = False) -> int:
    """Record a post that went out (by hand, RPA, an extension). The post's link is optional for a manual record."""
    art = versions.load(s, article_id)
    if art["status"] == "expired" and not allow_expired:
        raise HTTPException(409, "this draft expired: confirm that you still posted it")
    if art["status"] not in ("approved", "expired"):
        raise HTTPException(409, f"article is {art['status']}, only approved articles can be published")
    blockers = publish_blockers(s, art["account_id"], now)
    if blockers:
        raise HTTPException(423, "; ".join(blockers))
    x_url = (x_url or "").strip()
    post_id = post_id_for(s, art["platform"], art["handle"], x_url) if x_url else None
    if published_at > now + timedelta(minutes=5):
        raise HTTPException(422, "published_at is in the future")
    v = versions.current_version(s, article_id)
    if art["gate_status"] != "pass" or v["fact_packet_id"] != art["current_fact_packet_id"]:
        raise HTTPException(409, "the facts changed after approval: review again")
    posted, report, matches = check_posted(s, art, v, final_text)
    if hard_findings(report):
        raise HTTPException(409, {"message": "the posted text fails the claims guard", "guard": report})
    h = hashlib.sha256(posted.encode()).hexdigest()
    assets = list(s.execute(text("""SELECT id FROM media_asset WHERE fact_packet_id = :p AND template_key = :t
                                    AND language = :l AND kind = 'card_png' ORDER BY id DESC LIMIT 1"""),
                            {"p": v["fact_packet_id"], "t": v["card_template"], "l": art["language"]}).scalars())
    pid = s.execute(text("""
        INSERT INTO publication_record (article_id, article_version_id, account_id, x_post_id, x_url, published_at,
          recorded_by, final_text, final_text_hash, matches_version, guard_report, asset_ids, channel, reply_url,
          api_token_id)
        VALUES (:a, :v, :acc, :pid, :url, :at, :u, :t, :h, :mv, CAST(:g AS jsonb), :assets, :ch, :ru, :tok) RETURNING id"""),
        {"a": article_id, "v": v["id"], "acc": art["account_id"], "pid": post_id, "url": x_url or None,
         "at": published_at, "u": user_id, "t": posted, "h": h, "mv": matches,
         "g": json.dumps(report, ensure_ascii=False, default=str), "assets": assets, "ch": channel,
         "ru": (reply_url or "").strip() or None, "tok": api_token_id}).scalar_one()
    versions.set_status(s, article_id, "published")
    s.execute(text("UPDATE assignment SET status = 'done' WHERE id = :i"), {"i": art["assignment_id"]})
    s.execute(text("""UPDATE publication_plan SET status = 'done', publication_id = :p
                      WHERE article_id = :a AND status IN ('planned', 'claimed')"""), {"p": pid, "a": article_id})
    try:
        transition(s, art["news_event_id"], "covered", actor_kind="user", user_id=user_id, reason=f"published {pid}")
    except InvalidTransition:
        pass  # already covered by another account
    s.execute(text("""UPDATE story_event SET pinned_fact_packet_id = coalesce(pinned_fact_packet_id, :p)
                      WHERE news_event_id = :e"""), {"p": v["fact_packet_id"], "e": art["news_event_id"]})
    audit.record(s, "publication.record", "publication", pid, user_id=user_id, account_id=art["account_id"],
                 after={"article_id": article_id, "version_id": v["id"], "x_url": x_url, "channel": channel,
                        "matches_version": matches})
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
