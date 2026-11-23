from datetime import timedelta

from sqlalchemy import text

from nr.auth.passwords import hash_password
from nr.crypto import encrypt
from nr.seed import seed
from tests.fakes import T0, WHALE, FakeFeed, context, ev, position
from tests.pipeline import pipeline

NOW = T0 + timedelta(hours=3)


def make_user(s, username, roles, perms=None) -> int:
    uid = s.execute(text("INSERT INTO app_user (username, password_hash) VALUES (:u, :p) RETURNING id"),
                    {"u": username, "p": hash_password("correct horse battery")}).scalar_one()
    for r in roles:
        s.execute(text("INSERT INTO user_role VALUES (:u, :r)"), {"u": uid, "r": r})
    for acc, plist in (perms or {}).items():
        for p in plist:
            s.execute(text("INSERT INTO account_permission VALUES (:u, :a, :p)"), {"u": uid, "a": acc, "p": p})
    return uid


def newsroom(sf, *, accounts_active=True, authorized=True, model=True) -> dict:
    """Seeded newsroom with both example accounts active and a fake model wired to both agents."""
    with sf() as s:
        seed(s)
        if accounts_active:
            s.execute(text("""UPDATE editorial_account SET status = 'active', authorization_status = :a,
                                     authorization_expires_at = :e"""),
                      {"a": "authorized" if authorized else "pending", "e": NOW + timedelta(days=30)})
        if model:
            for name in ("primary", "fallback"):
                mid = s.execute(text("""
                    INSERT INTO ai_model_config (name, protocol, base_url, model, api_key_secret, input_usd_per_mtok,
                                                 output_usd_per_mtok, max_retries)
                    VALUES (:n, 'openai_chat', 'https://gateway.test/v1', :m, :k, 3, 15, 1) RETURNING id"""),
                    {"n": name, "m": f"model-{name}", "k": encrypt("sk-test")}).scalar_one()
                col = "model_config_id" if name == "primary" else "fallback_model_config_id"
                s.execute(text(f"UPDATE ai_agent_profile SET {col} = :m"), {"m": mid})
        ids = {r[0]: r[1] for r in s.execute(text("SELECT handle, id FROM editorial_account"))}
        admin = make_user(s, "chief", ["editor_in_chief"])
        s.commit()
    return {"zh": ids["alphascope_cn"], "en": ids["alphascope_global"], "chief": admin}


def candidate_event(sf) -> int:
    """A $30M BTC long by a trader with a verified style: passes the gate and becomes a candidate."""
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300, mark=100000, entry=98000, lev=20, liq=91000)],
                                   styles=[{"style": "trend", "version": "s2", "status": "verified", "confidence": "1",
                                            "n": 40, "oos": None, "computed_at": T0.isoformat()}])
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid, state = s.execute(text("SELECT id, state FROM news_event")).one()
    assert state == "candidate"
    return eid


def eic_output(eid, recs, silent=(), decision="cover"):
    return {"decision": decision, "priority": "p1",
            "explanation": "A verified trend trader opened a $30M BTC long, ten times its usual size.",
            "key_fact_ids": ["position.notional_usd"],
            "newsworthiness": [{"factor": "size", "assessment": "large for this trader",
                                "fact_ids": ["position.notional_usd"]}],
            "recommended_accounts": [{"account_id": a, "angle": angle, "reason": "fits the beat", "format": "single_post",
                                      "urgency": "now"} for a, angle in recs],
            "silent_accounts": [{"account_id": a, "reason": "not this audience"} for a in silent],
            "follow_up": {"watch_for": ["adds", "close"], "recheck_after_minutes": 60, "story": "new"},
            "risk_flags": []}


def draft_output(lang, body, facts=("position.notional_usd",), headline=None):
    return {"language": lang, "headline_candidates": [headline or ("巨鲸开多 BTC" if lang == "zh" else "Whale opens BTC long")],
            "body": body, "thread": None, "angle": "size vs history", "added_value": "context from the archive",
            "difference_from_others": "focuses on the trader's record", "why_shareable": "rare size",
            "risks": [], "facts_used": list(facts), "card_suggestion": "position_card"}
