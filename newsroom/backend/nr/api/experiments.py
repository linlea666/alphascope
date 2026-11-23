"""Blind A/B experiments on drafts (nr/experiments) and editor ratings of draft versions."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import one, rows
from nr.auth.deps import active_user, require_roles
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.experiments import contrast_ab
from nr.jobs.queue import enqueue

router = APIRouter(prefix="/api", tags=["experiments"])
CHIEF = require_roles("admin", "editor_in_chief")
JUDGES = require_roles("admin", "editor_in_chief", "editor", "reviewer")
RATING_TAGS = ("information", "lede", "evidence", "story", "tone", "wrong_angle", "misleading")


class ExperimentIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,47}$")
    account_id: int
    model_config_id: int
    n: int = Field(30, ge=4, le=40)


@router.get("/experiments")
def list_experiments(s: Session = Depends(get_session), user: CurrentUser = Depends(JUDGES)):
    pending = rows(s, """SELECT payload->>'name' AS experiment, status, last_error, created_at FROM job
                         WHERE kind = 'experiment_create' AND status IN ('queued', 'running', 'failed', 'dead')
                         ORDER BY id DESC LIMIT 5""")
    return {"experiments": contrast_ab.experiments(s, user.id), "pending": pending}


@router.post("/experiments")
def create_experiment(body: ExperimentIn, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    if s.execute(text("SELECT 1 FROM experiment_pair WHERE experiment = :x"), {"x": body.name}).scalar():
        raise HTTPException(409, "an experiment with this name exists")
    if one(s, "SELECT id FROM editorial_account WHERE id = :a", {"a": body.account_id}) is None:
        raise HTTPException(422, "unknown account")
    if one(s, "SELECT id FROM ai_model_config WHERE id = :m AND enabled", {"m": body.model_config_id}) is None:
        raise HTTPException(422, "unknown or disabled model")
    for key in ("reporter", "reporter_breaking", "reporter_derivatives"):  # arm b needs the v2 prompts
        if not s.execute(text("SELECT 1 FROM prompt_template WHERE key = :k AND version = 2"), {"k": key}).scalar():
            raise HTTPException(409, f"prompt {key}@v2 is missing: run the seed (newsroom/start.sh update)")
    job = enqueue(s, "experiment_create", {**body.model_dump(), "user_id": user.id},
                  dedupe_key=f"experiment:{body.name}", priority=30)
    audit.record(s, "experiment.request", "experiment", body.name, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"job": job}


@router.get("/experiments/{name}/next")
def next_pair(name: str, s: Session = Depends(get_session), user: CurrentUser = Depends(JUDGES)):
    total = s.execute(text("SELECT count(*) FROM experiment_pair WHERE experiment = :x"), {"x": name}).scalar()
    if not total:
        raise HTTPException(404, "experiment not found")
    mine = s.execute(text("""SELECT count(*) FROM experiment_vote v JOIN experiment_pair x ON x.id = v.pair_id
                             WHERE x.experiment = :x AND v.user_id = :u"""), {"x": name, "u": user.id}).scalar()
    return {"experiment": name, "pairs": total, "my_votes": mine, "pair": contrast_ab.next_pair(s, name, user.id)}


class Scores(BaseModel):
    information: int = Field(ge=1, le=5)
    lede: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    publishable: int = Field(ge=1, le=5)


class VoteIn(BaseModel):
    winner: Literal["1", "2", "tie"]
    misleading: list[Literal["1", "2"]] = Field(default_factory=list)
    scores: dict[Literal["1", "2"], Scores]
    note: str = Field("", max_length=1000)


@router.post("/experiments/{name}/pairs/{pair_id}/vote")
def vote(name: str, pair_id: int, body: VoteIn, s: Session = Depends(get_session), user: CurrentUser = Depends(JUDGES)):
    try:
        contrast_ab.vote(s, name, pair_id, user.id, body.winner, body.misleading,
                         {k: v.model_dump() for k, v in body.scores.items()}, body.note)
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.record(s, "experiment.vote", "experiment_pair", pair_id, user_id=user.id)
    s.commit()
    return {"ok": True}


@router.get("/experiments/{name}/results")
def results(name: str, s: Session = Depends(get_session), user: CurrentUser = Depends(JUDGES)):
    """Unblinded figures: the chief always sees them, other judges once they have judged every ready pair."""
    if not user.roles & {"admin", "editor_in_chief"} and contrast_ab.next_pair(s, name, user.id) is not None:
        raise HTTPException(403, "results open once you have judged every pair")
    out = contrast_ab.results(s, name)
    if not out["pairs"]:
        raise HTTPException(404, "experiment not found")
    return out


# ---- ratings of any draft version ---------------------------------------------------------------------------------

class RatingIn(BaseModel):
    score: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=7)
    note: str = Field("", max_length=1000)


@router.put("/articles/{article_id}/versions/{version_id}/rating")
def rate_version(article_id: int, version_id: int, body: RatingIn, s: Session = Depends(get_session),
                 user: CurrentUser = Depends(JUDGES)):
    if not s.execute(text("SELECT 1 FROM article_version WHERE id = :v AND article_id = :a"),
                     {"v": version_id, "a": article_id}).scalar():
        raise HTTPException(404, "version not found")
    if bad := [t for t in body.tags if t not in RATING_TAGS or not re.fullmatch(r"[a-z_]+", t)]:
        raise HTTPException(422, f"unknown tags {bad}")
    s.execute(text("""
        INSERT INTO draft_rating (article_version_id, user_id, score, tags, note)
        VALUES (:v, :u, :sc, :t, :n)
        ON CONFLICT (article_version_id, user_id) DO UPDATE SET score = EXCLUDED.score, tags = EXCLUDED.tags,
            note = EXCLUDED.note, updated_at = now()"""),
        {"v": version_id, "u": user.id, "sc": body.score, "t": body.tags, "n": body.note})
    audit.record(s, "article.rate", "article_version", version_id, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"ok": True}


@router.get("/articles/{article_id}/ratings")
def article_ratings(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return rows(s, """SELECT r.article_version_id, r.score, r.tags, r.note, r.blind, r.updated_at, u.username,
                             r.user_id = :me AS mine
                      FROM draft_rating r JOIN article_version v ON v.id = r.article_version_id
                      JOIN app_user u ON u.id = r.user_id WHERE v.article_id = :a ORDER BY r.updated_at DESC""",
                {"a": article_id, "me": user.id})
