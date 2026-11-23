"""Versioned output schemas. Models fill these; code decides what happens next. None of them has a field that
publishes, approves or changes a state, so a manipulated output can at most produce a bad draft that the guard and
a human then reject."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "ai.v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountRec(Strict):
    account_id: int
    angle: str = Field(max_length=300)
    reason: str = Field(max_length=500)
    format: Literal["single_post", "thread", "card_post"]
    urgency: Literal["now", "within_2h", "today"]


class AccountSilence(Strict):
    account_id: int
    reason: str = Field(max_length=400)


class Factor(Strict):
    factor: str = Field(max_length=60)
    assessment: str = Field(max_length=400)
    fact_ids: list[str]


class FollowUp(Strict):
    watch_for: list[str] = Field(max_length=6)
    recheck_after_minutes: int | None = None
    story: Literal["new", "continue", "none"]


class EicEvaluation(Strict):
    decision: Literal["cover", "hold", "no_coverage"]
    priority: Literal["p0", "p1", "p2", "p3"]
    explanation: str = Field(max_length=1200)
    key_fact_ids: list[str] = Field(min_length=1)
    newsworthiness: list[Factor] = Field(max_length=8)
    recommended_accounts: list[AccountRec]
    silent_accounts: list[AccountSilence]
    follow_up: FollowUp
    risk_flags: list[Literal["identity_risk", "stale_risk", "liquidation_unconfirmed", "low_sample", "duplicate_risk",
                             "other"]]


class DraftOut(Strict):
    language: Literal["zh", "en"]
    headline_candidates: list[str] = Field(min_length=1, max_length=5)
    body: str = Field(min_length=1, max_length=4000)
    thread: list[str] | None = None
    angle: str = Field(max_length=300)
    added_value: str = Field(max_length=500)
    difference_from_others: str = Field(max_length=500)
    why_shareable: str = Field(max_length=500)
    risks: list[str]
    facts_used: list[str] = Field(min_length=1)
    card_suggestion: Literal["position_card", "close_card", "liq_card"] | None = None


class ModelTestOut(Strict):
    ok: bool
    echo: str
