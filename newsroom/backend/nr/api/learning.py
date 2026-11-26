"""Learning from the reviewer and the readers (docs/17): candidate style examples a person adopts, and the prompt
coach's drafts with the experiment that tests each one. Nothing here changes what the desk writes until a person
adopts it."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.admin import _agent_version
from nr.api.common import now, one, rows
from nr.auth.deps import require_roles
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.desk import examples
from nr.experiments.contrast_ab import results as experiment_results
from nr.jobs.queue import enqueue

router = APIRouter(prefix="/api/learning", tags=["learning"])
CHIEF = require_roles("admin", "editor_in_chief")
ADMIN = require_roles("admin")


@router.get("/examples")
def list_examples(status: str = Query("candidate", pattern="^(candidate|adopted|dismissed)$"),
                  s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    return rows(s, """
        SELECT c.*, st.name AS style_name, (SELECT z.name FROM editorial_style z WHERE z.key = 'zh_' || substring(c.style_key from 4) ORDER BY z.version DESC LIMIT 1) AS style_zh, acc.handle FROM style_example_candidate c
        LEFT JOIN LATERAL (SELECT name FROM editorial_style WHERE key = c.style_key ORDER BY version DESC LIMIT 1) st ON true
        JOIN article_version v ON v.id = c.article_version_id JOIN article a ON a.id = v.article_id
        JOIN editorial_account acc ON acc.id = a.account_id
        WHERE c.status = :st ORDER BY c.id DESC LIMIT 100""", {"st": status})


@router.post("/examples/{candidate_id}/adopt")
def adopt_example(candidate_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    c = one(s, "SELECT style_key, status FROM style_example_candidate WHERE id = :i", {"i": candidate_id})
    if c is None:
        raise HTTPException(404, "candidate not found")
    if c["status"] != "candidate":
        raise HTTPException(409, f"candidate is {c['status']}")
    version = examples.adopt(s, candidate_id, user.id)
    audit.record(s, "style.example_adopt", "style", c["style_key"], user_id=user.id,
                 after={"candidate": candidate_id, "version": version})
    s.commit()
    return {"style": c["style_key"], "version": version}


@router.post("/examples/{candidate_id}/dismiss")
def dismiss_example(candidate_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    n = s.execute(text("""UPDATE style_example_candidate SET status = 'dismissed', decided_by = :u, decided_at = now()
                          WHERE id = :i AND status = 'candidate'"""), {"u": user.id, "i": candidate_id}).rowcount
    if not n:
        raise HTTPException(409, "not a candidate")
    s.commit()
    return {"ok": True}


def arm_scores(s: Session, name: str) -> dict:
    """The AI reviewer's mean scores for each arm's drafts (scored blind to the arm)."""
    out = {}
    for arm in ("a", "b"):
        r = one(s, f"""
            SELECT count(*) AS n, round(avg((r.scores->>'publishable')::numeric), 2) AS publishable,
                   round(avg((r.scores->>'hook')::numeric), 2) AS hook, round(avg((r.scores->>'native')::numeric), 2) AS native,
                   round(avg((r.scores->>'ai_tone')::numeric), 2) AS ai_tone, round(avg((r.scores->>'clarity')::numeric), 2) AS clarity,
                   count(*) FILTER (WHERE r.verdict = 'approve') AS approved
            FROM experiment_pair x JOIN article_review r ON r.article_version_id = x.{arm}_version_id
            WHERE x.experiment = :x""", {"x": name})
        out[arm] = r
    return out


@router.get("/proposals")
def list_proposals(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    props = rows(s, """
        SELECT p.*, (SELECT array_agg(agent_key) FROM ai_agent_profile a WHERE a.active AND a.prompt_key = p.prompt_key)
               AS agents
        FROM prompt_proposal p ORDER BY p.id DESC LIMIT 50""")
    for p in props:
        if p["experiment_name"] and one(s, "SELECT 1 FROM experiment WHERE name = :x", {"x": p["experiment_name"]}):
            r = experiment_results(s, p["experiment_name"])
            p["experiment"] = {"pairs": r["pairs"], "ready": r.get("ready", 0), "votes": r.get("votes", 0),
                               "b_win_rate": r.get("b_win_rate"),
                               "guard_pass": {a: r["arms"][a]["guard_pass"] for a in "ab"} if r.get("arms") else None,
                               "ai_scores": arm_scores(s, p["experiment_name"])}
    return props


@router.post("/proposals/run")
def run_coach(agent_key: str | None = Query(None, max_length=48), s: Session = Depends(get_session),
              user: CurrentUser = Depends(ADMIN)):
    """Run the prompt coach now (it also runs every Monday)."""
    job = enqueue(s, "prompt_coach", {"only": agent_key}, dedupe_key="prompt_coach:manual", priority=40, now=now())
    audit.record(s, "prompt.coach_run", "prompt", agent_key or "all", user_id=user.id)
    s.commit()
    return {"job": job}


@router.post("/proposals/{proposal_id}/adopt")
def adopt_proposal(proposal_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    """Every active role on this prompt moves to the drafted version (a new agent version, as any switch)."""
    p = one(s, "SELECT * FROM prompt_proposal WHERE id = :i", {"i": proposal_id})
    if p is None:
        raise HTTPException(404, "proposal not found")
    if p["status"] in ("adopted", "rejected"):
        raise HTTPException(409, f"proposal is {p['status']}")
    changed = []
    for cur in s.execute(text("SELECT * FROM ai_agent_profile WHERE active AND prompt_key = :k"),
                         {"k": p["prompt_key"]}).mappings().all():
        if cur["prompt_version"] != p["version"]:
            _agent_version(s, cur, user.id, prompt_version=p["version"])
            changed.append(cur["agent_key"])
    s.execute(text("""UPDATE prompt_proposal SET status = 'adopted', decided_by = :u, decided_at = now()
                      WHERE prompt_key = :k AND version = :v"""), {"u": user.id, "k": p["prompt_key"], "v": p["version"]})
    audit.record(s, "prompt.coach_adopt", "prompt", f"{p['prompt_key']}@v{p['version']}", user_id=user.id,
                 after={"agents": changed})
    s.commit()
    return {"agents": changed}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    p = one(s, "SELECT prompt_key, version FROM prompt_proposal WHERE id = :i", {"i": proposal_id})
    if p is None:
        raise HTTPException(404, "proposal not found")
    s.execute(text("""UPDATE prompt_proposal SET status = 'rejected', decided_by = :u, decided_at = now()
                      WHERE prompt_key = :k AND version = :v AND status IN ('draft', 'testing')"""),
              {"u": user.id, "k": p["prompt_key"], "v": p["version"]})
    audit.record(s, "prompt.coach_reject", "prompt", f"{p['prompt_key']}@v{p['version']}", user_id=user.id)
    s.commit()
    return {"ok": True}
