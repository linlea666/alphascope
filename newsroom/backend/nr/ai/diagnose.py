"""What a failed AI call means for a person: one code per cause, which the web app turns into words and a next step."""

import re

RULES = [  # (code, test on status and error text), first match wins
    ("firewall", lambda st, e: "cloudflare" in e or "attention required" in e),
    ("bad_key", lambda st, e: bool(re.search(r"http 401|authentication|invalid api key|api key.*invalid|unauthorized", e))),
    ("no_channel", lambda st, e: "无可用渠道" in e or bool(re.search(r"model.{0,20}(not found|does not exist)|unknown model", e))),
    ("quota", lambda st, e: bool(re.search(r"http 402|insufficient|quota|balance", e))),
    ("rate_limit", lambda st, e: "http 429" in e or "rate limit" in e),
    ("server", lambda st, e: bool(re.search(r"http 5\d\d", e))),
    ("timeout", lambda st, e: st == "timeout"),
    ("truncated", lambda st, e: st == "truncated"),
    ("format", lambda st, e: st in ("invalid_json", "schema_invalid")),
    ("too_long", lambda st, e: st == "semantic_invalid" and "characters" in e),
    ("rule", lambda st, e: st == "semantic_invalid"),
    ("budget", lambda st, e: st == "budget_blocked"),
]


def diagnose(status: str | None, error: str | None) -> str:
    st, e = status or "", (error or "").lower()
    return next((code for code, test in RULES if test(st, e)), "other")
