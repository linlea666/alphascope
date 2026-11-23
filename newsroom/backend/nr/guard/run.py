"""The claims guard g1: a hard gate. A version that fails cannot be approved or published; there is no override.
False positives are fixed by adding the fact to the packet or by rewording."""

import re
from datetime import UTC, datetime, timedelta

from nr.guard import numbers, phrases
from nr.guard.xlength import LIMIT, weighted_length

GUARD_VERSION = "g2"
WARN_ONLY = {"STYLE_PHRASE", "NEAR_DUPLICATE_WARN"}
RECENT = re.compile(r"刚刚|刚才|\bjust now\b|\bmoments ago\b", re.I)
RECENT_HOURS = 3  # "刚刚" stops being true a few hours after the observation, also for a draft published later


def run(parts: dict[str, str], packet: dict, *, facts_used: list[str], version_packet_id: int, current_packet_id: int,
        long_post_allowed: bool, allow: list[str], forbidden: list[str], duplicate: dict | None,
        dup_block: float, dup_warn: float, platform: dict | None = None, now: datetime | None = None) -> dict:
    """parts: {"headline": ..., "body": ..., "thread.1": ...}. Returns the guard report stored on the version."""
    findings = []
    for name, txt in parts.items():
        if not txt:
            continue
        for f in numbers.check(txt, packet["facts"], allow) + phrases.check(txt, packet, forbidden):
            findings.append({"part": name, "code": f.code, "span": list(f.span), "text": f.text, "detail": f.detail,
                             "severity": "warn" if f.code in WARN_ONLY else "fail"})
    recency = next((f for f in packet["facts"] if f["id"] == "change.recency"), None)
    seen = datetime.fromisoformat(recency["as_of"]) if recency and recency.get("as_of") else None
    for name, txt in parts.items():
        m = RECENT.search(txt or "")
        if m and (seen is None or (now or datetime.now(UTC)) - seen > timedelta(hours=RECENT_HOURS)):
            findings.append({"part": name, "code": "RECENCY_STALE", "span": list(m.span()), "text": m.group(0),
                             "detail": f"'just now' only within {RECENT_HOURS}h of the observation", "severity": "fail"})
    ids = {f["id"] for f in packet["facts"]}
    unknown = sorted(set(facts_used) - ids)
    if unknown:
        findings.append({"part": "facts_used", "code": "UNKNOWN_FACT", "span": None, "text": ", ".join(unknown),
                         "detail": "cites facts that are not in the packet", "severity": "fail"})
    if version_packet_id != current_packet_id:
        findings.append({"part": "facts_used", "code": "FACT_PACKET_SUPERSEDED", "span": None, "text": "",
                         "detail": "written from an older fact packet version; refresh against the current facts",
                         "severity": "fail"})
    rule = platform or {"limit": LIMIT, "weighted": True}
    body = parts.get("body") or ""
    length = weighted_length(body) if rule["weighted"] else len(body)
    if not long_post_allowed:  # every post of a thread is a post of its own
        for name, txt in parts.items():
            if name == "headline" or not txt:
                continue
            n = weighted_length(txt) if rule["weighted"] else len(txt)
            if n > rule["limit"]:
                findings.append({"part": name, "code": "TOO_LONG", "span": None, "text": str(n),
                                 "detail": f"length {n} > {rule['limit']} for this platform (account without long posts)",
                                 "severity": "fail"})
    if duplicate and duplicate["similarity"] >= dup_warn:
        block = duplicate["similarity"] >= dup_block
        findings.append({"part": "body", "code": "NEAR_DUPLICATE" if block else "NEAR_DUPLICATE_WARN", "span": None,
                         "text": f"{duplicate['similarity']:.2f}", "detail": f"similar to {duplicate['kind']} article "
                         f"{duplicate['article_id']} (account {duplicate['account_id']})",
                         "severity": "fail" if block else "warn"})
    status = "fail" if any(f["severity"] == "fail" for f in findings) else "warn" if findings else "pass"
    return {"status": status, "version": GUARD_VERSION, "findings": findings,
            "x_length": length, "length_limit": rule["limit"], "duplicate": duplicate}
