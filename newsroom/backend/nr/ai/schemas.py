"""Versioned output schemas. Models fill these; code decides what happens next. None of them has a field that
publishes, approves or changes a state, so a manipulated output can at most produce a bad draft that the guard and
a human then reject."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "ai.v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


ReporterRole = Literal["reporter", "reporter_breaking", "reporter_investigator", "reporter_data",
                      "reporter_derivatives", "reporter_historian"]


class AccountRec(Strict):
    account_id: int
    reporter: ReporterRole | None = None
    angle: str = Field(max_length=600)
    reason: str = Field(max_length=800)
    format: Literal["single_post", "thread", "card_post"]
    urgency: Literal["now", "within_2h", "today"]


class AccountSilence(Strict):
    account_id: int
    reason: str = Field(max_length=800)


class Factor(Strict):
    factor: str = Field(max_length=60)
    assessment: str = Field(max_length=800)
    fact_ids: list[str]


class FollowUp(Strict):
    watch_for: list[str] = Field(max_length=6)
    recheck_after_minutes: int | None = None
    story: Literal["new", "continue", "none"]


class EicEvaluation(Strict):
    decision: Literal["cover", "hold", "no_coverage"]
    priority: Literal["p0", "p1", "p2", "p3"]
    explanation: str = Field(max_length=2000)
    key_fact_ids: list[str] = Field(min_length=1)
    newsworthiness: list[Factor] = Field(max_length=8)
    recommended_accounts: list[AccountRec]
    silent_accounts: list[AccountSilence]
    follow_up: FollowUp
    risk_flags: list[Literal["identity_risk", "stale_risk", "liquidation_unconfirmed", "low_sample", "duplicate_risk",
                             "other"]]
    open_questions: list[str] = Field(default_factory=list, max_length=3)


class DraftOut(Strict):
    language: Literal["zh", "en"]
    headline_candidates: list[str] = Field(min_length=1, max_length=5)
    body: str = Field(min_length=1, max_length=4000)
    thread: list[str] | None = None
    angle: str = Field(max_length=600)
    added_value: str = Field(max_length=800)
    difference_from_others: str = Field(max_length=800)
    why_shareable: str = Field(max_length=800)
    risks: list[str]
    facts_used: list[str] = Field(min_length=1)
    card_suggestion: Literal["position_card", "close_card", "liq_card"] | None = None


class DraftOutV2(DraftOut):
    """Reporter prompts from v2 on: a lede built on what is different this time, and a question for the follow-up."""
    lede_fact_ids: list[str] = Field(default_factory=list, max_length=4)
    why_now: str = Field("", max_length=300)
    open_question: str | None = Field(None, max_length=200)


class ReviewScores(Strict):
    ai_tone: int = Field(ge=1, le=5)
    native: int = Field(ge=1, le=5)
    hook: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    style_fit: int = Field(ge=1, le=5)
    risk: int = Field(ge=1, le=5)
    publishable: int = Field(ge=1, le=5)


class ReviewIssue(Strict):
    quote: str = Field(max_length=400)
    problem: str = Field(max_length=400)
    fix: str = Field(max_length=400)


class ReviewOut(Strict):
    """The desk reviewer's verdict on one draft version. It judges; it never edits text or numbers."""
    verdict: Literal["approve", "revise", "reject"]
    scores: ReviewScores
    issues: list[ReviewIssue] = Field(default_factory=list, max_length=6)
    summary: str = Field(max_length=400)
    revision_note: str = Field("", max_length=1500)


class ModelTestOut(Strict):
    ok: bool
    echo: str
