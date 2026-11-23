"""A/B experiment: do contrast facts and the v2 reporter prompt make drafts clearly better (docs/14)?

For a stratified sample of recent events, each pair holds two trial drafts by the same reporter role and model for the
same account: A writes from the facts-1 view of the packet with prompt v1 (what the newsroom did before), B from the
full packet with prompt v2 and story memory. Editors judge the pairs blind; the arm behind each slot stays on the
server until the results are read.
"""

import json
import random
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import audit
from nr.desk import assignment
from nr.desk.drafting import generate_draft
from nr.facts.build import RateLimited, build_event_facts
from nr.feed.client import FeedClient
from nr.jobs.queue import enqueue

# stratum, event classes, share of the sample
STRATA = (("open_add", ("whale_open", "whale_add", "flip", "notable_trader_move"), 10 / 30),
          ("underwater_add", ("underwater_add",), 10 / 30),
          ("near_liquidation", ("near_liquidation",), 5 / 30),
          ("close", ("whale_close", "liquidation"), 5 / 30))
ARMS = {"a": {"prompt_version": 1, "variant": "baseline"}, "b": {"prompt_version": 2, "variant": None}}
DIMENSIONS = ("information", "lede", "evidence", "publishable")
# families of contrast facts; an event "has contrast" when at least two families say something
FAMILIES = {"archive": ("contrast.",), "radar": ("radar.prior_positions",), "book": ("book.positions",),
            "crowd": ("crowd.share_of_same_side", "crowd.same_side_rank", "crowd.timing_same_side"),
            "leaderboard": ("trader.alltime_pnl_usd",)}
CRITERIA = {"b_win_rate": 0.65, "stop_b_win_rate": 0.55, "stop_contrast_coverage": 0.40, "max_extra_tokens": 0.5}


def families(packet: dict) -> list[str]:
    ids = [f["id"] for f in packet["facts"]]
    return [k for k, prefixes in FAMILIES.items() if any(i.startswith(prefixes) for i in ids)]


def quotas(n: int) -> dict[str, int]:
    q = {k: round(n * share) for k, _, share in STRATA}
    q["open_add"] += n - sum(q.values())
    return q


def candidates(s: Session, classes: tuple[str, ...], venues: list[str], now: datetime, taken: set[int],
               limit: int) -> list[int]:
    """Recent position events, one per position, largest score first."""
    return [r[0] for r in s.execute(text("""
        SELECT id FROM (
            SELECT DISTINCT ON (episode_id) id, score FROM news_event
            WHERE column_key IS NULL AND event_class = ANY(:c) AND venue = ANY(:v) AND detected_at > :since
              AND state IN ('candidate', 'evaluated', 'assigned', 'covered', 'dismissed', 'blocked')
            ORDER BY episode_id, score DESC NULLS LAST) x
        ORDER BY score DESC NULLS LAST LIMIT :n"""),
        {"c": list(classes), "v": venues, "since": now - timedelta(hours=48), "n": limit}) if r[0] not in taken]


def create(sf: sessionmaker, client: FeedClient, name: str, account_id: int, model_id: int, n: int, now: datetime,
           user_id: int, seed: int | None = None) -> dict:
    """Pick the sample (refreshing each event's facts first), open one trial article per event and queue both arms."""
    rng = random.Random(seed)
    with sf() as s:
        acc = s.execute(text("SELECT venues FROM editorial_account WHERE id = :a"), {"a": account_id}).scalar()
        if acc is None:
            raise ValueError("unknown account")
        if not s.execute(text("SELECT 1 FROM ai_model_config WHERE id = :m AND enabled"), {"m": model_id}).scalar():
            raise ValueError("unknown or disabled model")
        taken = set(s.execute(text("SELECT news_event_id FROM experiment_pair WHERE experiment = :x"),
                              {"x": name}).scalars())
    picked: list[tuple[str, int]] = []
    skipped: dict[str, int] = {}
    stratum_of = {c: k for k, classes, _ in STRATA for c in classes}
    # each stratum takes its quota; a last pass fills what thin strata left over from any class
    passes = [(k, classes, quotas(n)[k]) for k, classes, _ in STRATA] + [("fill", tuple(stratum_of), None)]
    for stratum, classes, want in passes:
        want = n - len(picked) if want is None else want
        with sf() as s:
            ids = candidates(s, classes, list(acc), now, taken, want * 4)
        got = 0
        for eid in ids:
            if got >= want or "rate_limited" in skipped:
                break
            try:
                built = build_event_facts(sf, client, eid, now)
            except RateLimited:
                skipped["rate_limited"] = 1
                break
            if built["gate"] != "pass":
                skipped["gate_blocked"] = skipped.get("gate_blocked", 0) + 1
                taken.add(eid)
                continue
            with sf() as s:
                cls = s.execute(text("SELECT event_class FROM news_event WHERE id = :e"), {"e": eid}).scalar()
            picked.append((stratum_of[cls], eid))
            taken.add(eid)
            got += 1
    pairs = []
    with sf() as s:
        for stratum, eid in picked:
            aid = assignment.create(s, eid, account_id, source="trial", status="accepted", user_id=user_id,
                                    reason=f"experiment:{name}", model_config_id=model_id)
            art = assignment.accept(s, aid, user_id)
            pid = s.execute(text("""INSERT INTO experiment_pair (experiment, news_event_id, stratum, article_id, a_first)
                                    VALUES (:x, :e, :st, :a, :f) RETURNING id"""),
                            {"x": name, "e": eid, "st": stratum, "a": art, "f": rng.random() < 0.5}).scalar_one()
            for arm in ("a", "b"):
                enqueue(s, "draft_generate", {"assignment_id": aid, "user_id": user_id, "mode": "draft",
                                              "model_config_id": model_id, "compare": arm == "b",
                                              "experiment_pair": pid, "arm": arm, **ARMS[arm]},
                        dedupe_key=f"exp:{pid}:{arm}", priority=40, now=now)
            pairs.append(pid)
        audit.record(s, "experiment.create", "experiment", name, user_id=user_id,
                     after={"account_id": account_id, "model_id": model_id, "pairs": len(pairs), "skipped": skipped})
        s.commit()
    return {"experiment": name, "pairs": len(pairs), "by_stratum": {k: sum(1 for st, _ in picked if st == k)
                                                                    for k, _, _ in STRATA}, "skipped": skipped}


def run_arm(sf: sessionmaker, payload: dict, now: datetime, job_id: int | None = None, transport=None) -> dict:
    """One arm of one pair (a draft_generate job carrying experiment_pair): the version id, or the error, is kept."""
    col = "a" if payload["arm"] == "a" else "b"
    try:
        out = generate_draft(sf, payload["assignment_id"], now, job_id=job_id, transport=transport,
                             user_id=payload.get("user_id"), mode="draft", model_config_id=payload["model_config_id"],
                             compare=payload.get("compare", False), prompt_version=payload["prompt_version"],
                             variant=payload.get("variant"))
    except Exception as exc:
        with sf() as s:
            s.execute(text(f"UPDATE experiment_pair SET {col}_error = :e WHERE id = :p"),
                      {"e": f"{type(exc).__name__}: {exc}"[:500], "p": payload["experiment_pair"]})
            s.commit()
        raise
    with sf() as s:
        s.execute(text(f"UPDATE experiment_pair SET {col}_version_id = :v, {col}_error = NULL WHERE id = :p"),
                  {"v": out["version_id"], "p": payload["experiment_pair"]})
        s.commit()
    return out


# ---- blind review ------------------------------------------------------------------------------------------------

def slots(pair: dict) -> dict[str, str]:
    """Slot shown to the editor -> arm. The mapping never leaves the server before the results."""
    return {"1": "a", "2": "b"} if pair["a_first"] else {"1": "b", "2": "a"}


def next_pair(s: Session, name: str, user_id: int) -> dict | None:
    p = s.execute(text("""
        SELECT x.* FROM experiment_pair x
        WHERE x.experiment = :x AND x.a_version_id IS NOT NULL AND x.b_version_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM experiment_vote v WHERE v.pair_id = x.id AND v.user_id = :u)
        ORDER BY x.id LIMIT 1"""), {"x": name, "u": user_id}).mappings().first()
    if p is None:
        return None
    ev = s.execute(text("""SELECT ne.id, ne.event_class, ne.coin, ne.direction, ne.venue, fp.content AS packet
                           FROM news_event ne JOIN article_version v ON v.id = :v
                           JOIN fact_packet fp ON fp.id = v.fact_packet_id WHERE ne.id = :e"""),
                   {"v": p["b_version_id"], "e": p["news_event_id"]}).mappings().one()
    drafts = []
    for slot, arm in slots(p).items():
        v = s.execute(text("""SELECT v.headline, v.body, v.thread, a.language FROM article_version v
                               JOIN article a ON a.id = v.article_id WHERE v.id = :i"""),
                      {"i": p[f"{arm}_version_id"]}).mappings().one()
        drafts.append({"slot": slot, **dict(v)})
    lang = drafts[0]["language"]
    return {"pair_id": p["id"], "event": {k: ev[k] for k in ("id", "event_class", "coin", "direction", "venue")},
            "facts": [{"id": f["id"], "label": f.get("label"), "display": (f["display"].get(lang) or [None])[0]}
                      for f in ev["packet"]["facts"] if f["kind"] != "series"],
            "constraints": ev["packet"]["constraints"], "drafts": drafts}


def vote(s: Session, name: str, pair_id: int, user_id: int, winner: str, misleading: list[str],
         scores: dict[str, dict[str, int]], note: str) -> None:
    p = s.execute(text("SELECT * FROM experiment_pair WHERE id = :p AND experiment = :x"),
                  {"p": pair_id, "x": name}).mappings().first()
    if p is None or p["a_version_id"] is None or p["b_version_id"] is None:
        raise LookupError("pair not ready")
    m = slots(p)
    by_arm = {m[k]: v for k, v in scores.items() if k in m}
    s.execute(text("""
        INSERT INTO experiment_vote (pair_id, user_id, winner, misleading_a, misleading_b, scores, note)
        VALUES (:p, :u, :w, :ma, :mb, CAST(:sc AS jsonb), :n)
        ON CONFLICT (pair_id, user_id) DO UPDATE SET winner = EXCLUDED.winner, misleading_a = EXCLUDED.misleading_a,
            misleading_b = EXCLUDED.misleading_b, scores = EXCLUDED.scores, note = EXCLUDED.note, created_at = now()"""),
        {"p": pair_id, "u": user_id, "w": "tie" if winner == "tie" else m[winner],
         "ma": any(m.get(x) == "a" for x in misleading), "mb": any(m.get(x) == "b" for x in misleading),
         "sc": json.dumps(by_arm), "n": note})
    for arm, sc in by_arm.items():  # the same judgement as an ordinary rating of each version, marked blind
        vals = [int(sc[d]) for d in DIMENSIONS if d in sc]
        if vals:
            s.execute(text("""
                INSERT INTO draft_rating (article_version_id, user_id, score, tags, note, blind)
                VALUES (:v, :u, :sc, '{}', :n, true)
                ON CONFLICT (article_version_id, user_id) DO UPDATE SET score = EXCLUDED.score, blind = true,
                    updated_at = now()"""),
                {"v": p[f"{arm}_version_id"], "u": user_id, "sc": round(sum(vals) / len(vals)), "n": note})


def results(s: Session, name: str) -> dict:
    pairs = [dict(r) for r in s.execute(text("SELECT * FROM experiment_pair WHERE experiment = :x ORDER BY id"),
                                        {"x": name}).mappings()]
    if not pairs:
        return {"experiment": name, "pairs": 0}
    votes = [dict(r) for r in s.execute(text("""SELECT v.* FROM experiment_vote v JOIN experiment_pair x ON x.id = v.pair_id
                                                WHERE x.experiment = :x"""), {"x": name}).mappings()]
    ready = [p for p in pairs if p["a_version_id"] and p["b_version_id"]]
    vids = [p[f"{a}_version_id"] for p in pairs for a in "ab" if p[f"{a}_version_id"]]
    vers = {r["id"]: dict(r) for r in s.execute(text("""
        SELECT v.id, v.guard_status, v.lede_fact_ids, fp.content AS packet FROM article_version v
        JOIN fact_packet fp ON fp.id = v.fact_packet_id WHERE v.id = ANY(:i)"""), {"i": vids}).mappings()}
    tokens = {r[0]: int(r[1] or 0) for r in s.execute(text("""
        SELECT split_part(prompt_ref, '@v', 2), sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0))
        FROM ai_call_log WHERE article_id = ANY(:a) AND purpose = 'draft_generate' GROUP BY 1"""),
        {"a": [p["article_id"] for p in pairs]})}
    arms = {}
    for arm in "ab":
        done = [vers[p[f"{arm}_version_id"]] for p in pairs if p[f"{arm}_version_id"]]
        sc = {d: [v["scores"][arm][d] for v in votes if arm in v["scores"] and d in v["scores"][arm]] for d in DIMENSIONS}
        arms[arm] = {"drafts": len(done), "errors": sum(1 for p in pairs if p[f"{arm}_error"] and not p[f"{arm}_version_id"]),
                     "guard_pass": round(sum(1 for v in done if v["guard_status"] != "fail") / len(done), 3) if done else None,
                     "misleading": sum(1 for v in votes if v[f"misleading_{arm}"]),
                     "mean_scores": {d: round(sum(x) / len(x), 2) if x else None for d, x in sc.items()},
                     "tokens": tokens.get(str(ARMS[arm]["prompt_version"]), 0)}
    with_contrast = [p for p in ready if len(families(vers[p["b_version_id"]]["packet"])) >= 2]
    fam = {k: sum(1 for p in ready if k in families(vers[p["b_version_id"]]["packet"])) for k in FAMILIES}
    b_wins = sum(1 for v in votes if v["winner"] == "b") + 0.5 * sum(1 for v in votes if v["winner"] == "tie")
    win = round(b_wins / len(votes), 3) if votes else None
    coverage = round(len(with_contrast) / len(ready), 3) if ready else None
    extra = round(arms["b"]["tokens"] / arms["a"]["tokens"] - 1, 3) if arms["a"]["tokens"] else None
    checks = {
        "b_win_rate": win is not None and win >= CRITERIA["b_win_rate"],
        "guard_not_worse": None if arms["a"]["guard_pass"] is None or arms["b"]["guard_pass"] is None
        else arms["b"]["guard_pass"] >= arms["a"]["guard_pass"],
        "not_more_misleading": arms["b"]["misleading"] <= arms["a"]["misleading"],
        "tokens_within_budget": extra is not None and extra <= CRITERIA["max_extra_tokens"],
    }
    stop = [k for k, hit in (("contrast_coverage_below_40pct", coverage is not None and coverage < CRITERIA["stop_contrast_coverage"]),
                             ("b_win_rate_below_55pct", win is not None and len(votes) >= 10 and win < CRITERIA["stop_b_win_rate"]))
            if hit]
    return {"experiment": name, "pairs": len(pairs), "ready": len(ready), "votes": len(votes),
            "voters": len({v["user_id"] for v in votes}), "b_win_rate": win, "arms": arms,
            "contrast_coverage": coverage, "families": fam, "extra_tokens_b": extra,
            "by_stratum": {k: sum(1 for p in pairs if p["stratum"] == k) for k, _, _ in STRATA},
            "checks": checks, "stop": stop, "criteria": CRITERIA,
            "verdict": "stop" if stop else "pass" if votes and all(v is not False for v in checks.values()) else "open"}


def experiments(s: Session, user_id: int) -> list[dict]:
    return [dict(r) for r in s.execute(text("""
        SELECT x.experiment, min(x.created_at) AS created_at, count(*) AS pairs,
               count(*) FILTER (WHERE x.a_version_id IS NOT NULL AND x.b_version_id IS NOT NULL) AS ready,
               count(*) FILTER (WHERE (x.a_error IS NOT NULL AND x.a_version_id IS NULL)
                                   OR (x.b_error IS NOT NULL AND x.b_version_id IS NULL)) AS failed,
               (SELECT count(*) FROM experiment_vote v JOIN experiment_pair y ON y.id = v.pair_id
                WHERE y.experiment = x.experiment AND v.user_id = :u) AS my_votes,
               (SELECT count(*) FROM experiment_vote v JOIN experiment_pair y ON y.id = v.pair_id
                WHERE y.experiment = x.experiment) AS votes
        FROM experiment_pair x GROUP BY x.experiment ORDER BY min(x.created_at) DESC"""), {"u": user_id}).mappings()]
