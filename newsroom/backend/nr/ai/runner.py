"""One structured AI call with budgets, retries, schema validation, one repair attempt and a fallback model.

Order: primary model -> (if the output is invalid) one repair call with the validation errors -> fallback model ->
give up. Invalid output never becomes a draft; its raw text is kept only in ai_call_log for diagnosis.
"""

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from string import Template

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import settings_store
from nr.ai.envelope import extract_json, user_message
from nr.ai.providers import anthropic_compat, openai_compat, openai_responses
from nr.ai.providers.base import ChatRequest, ChatResult, ProviderError
from nr.crypto import decrypt

_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
_LOCK = threading.Lock()
MAX_TOKENS_CAP = 16000


class AIUnavailable(Exception):
    """AI switched off, or no model configured for the agent: the job waits, nothing else is affected."""


class BudgetExceeded(Exception):
    pass


class AIFailed(Exception):
    pass


@dataclass
class CallOutcome:
    parsed: BaseModel
    call_id: int
    model: str
    prompt_ref: str
    agent_key: str


def model_config(s: Session, model_id: int | None) -> dict | None:
    if model_id is None:
        return None
    row = s.execute(text("SELECT * FROM ai_model_config WHERE id = :i AND enabled"), {"i": model_id}).mappings().first()
    if row is None:
        return None
    cfg = dict(row)
    cfg["api_key"] = decrypt(cfg["api_key_secret"]) if cfg["api_key_secret"] else ""
    return cfg


PAUSE_AFTER = 3  # consecutive gateway errors before a model sits out
PAUSE_FOR = timedelta(hours=1)


def model_paused(s: Session, model_id: int, now: datetime) -> str | None:
    """Why a model should sit this call out: its last test failed (as a fallback only) or its last calls all hit
    gateway errors within the hour. Saves a doomed request on every job, e.g. a gateway that blocks this server."""
    last = s.execute(text("""SELECT status, created_at, error FROM ai_call_log WHERE model_config_id = :m
                             ORDER BY id DESC LIMIT :n"""), {"m": model_id, "n": PAUSE_AFTER}).mappings().all()
    if len(last) == PAUSE_AFTER and all(r["status"] in ("http_error", "timeout") for r in last) \
            and now - last[0]["created_at"] < PAUSE_FOR:
        return f"paused after {PAUSE_AFTER} gateway errors: {(last[0]['error'] or '')[:120]}"
    return None


def default_transport(cfg: dict, req: ChatRequest) -> ChatResult:
    mod = {"anthropic_messages": anthropic_compat, "openai_responses": openai_responses}.get(cfg["protocol"], openai_compat)
    return mod.call(cfg, req)


def _semaphore(cfg: dict) -> threading.BoundedSemaphore:
    with _LOCK:
        if cfg["id"] not in _SEMAPHORES:
            _SEMAPHORES[cfg["id"]] = threading.BoundedSemaphore(max(1, cfg["max_concurrency"]))
        return _SEMAPHORES[cfg["id"]]


def cost(cfg: dict, inp: int | None, out: int | None) -> Decimal:
    return (Decimal(inp or 0) * Decimal(cfg["input_usd_per_mtok"]) + Decimal(out or 0)
            * Decimal(cfg["output_usd_per_mtok"])) / Decimal(1_000_000)


def spent(s: Session, now: datetime, agent_key: str | None = None, job_id: int | None = None) -> Decimal:
    if job_id is not None:
        return s.execute(text("SELECT coalesce(sum(cost_usd), 0) FROM ai_call_log WHERE job_id = :j"),
                         {"j": job_id}).scalar()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return s.execute(text(f"""SELECT coalesce(sum(cost_usd), 0) FROM ai_call_log WHERE created_at >= :d
                              {"AND agent_key = :a" if agent_key else ""}"""), {"d": day, "a": agent_key}).scalar()


def log_call(s: Session, *, job_id, agent_key, prompt_ref, cfg, purpose, event_id, article_id, attempt, fallback,
             req_hash, result: ChatResult | None, status: str, error: str | None, raw: str | None, now: datetime) -> int:
    return s.execute(text("""
        INSERT INTO ai_call_log (job_id, agent_key, prompt_ref, model_config_id, protocol, model, purpose, news_event_id,
          article_id, attempt, fallback_used, request_hash, input_tokens, output_tokens, cost_usd, latency_ms, status,
          error, raw_output, created_at)
        VALUES (:j, :ak, :pr, :mc, :proto, :model, :pu, :e, :a, :at, :fb, :rh, :it, :ot, :c, :ms, :st, :err, :raw, :n)
        RETURNING id"""),
        {"j": job_id, "ak": agent_key, "pr": prompt_ref, "mc": cfg["id"] if cfg else None,
         "proto": cfg["protocol"] if cfg else None, "model": (result.model if result else cfg["model"]) if cfg else None,
         "pu": purpose, "e": event_id, "a": article_id, "at": attempt, "fb": fallback, "rh": req_hash,
         "it": result.input_tokens if result else None, "ot": result.output_tokens if result else None,
         "c": cost(cfg, result.input_tokens, result.output_tokens) if (cfg and result) else 0,
         "ms": result.latency_ms if result else None, "st": status, "err": (error or "")[:2000] or None,
         "raw": (raw or "")[:65536] or None, "n": now}).scalar_one()


def resolve_prompt(s: Session, key: str, version: int, language: str | None) -> dict | None:
    """The text for this language, else the shared one, else any text of the version."""
    row = s.execute(text("""SELECT * FROM prompt_template WHERE key = :k AND version = :v
                            ORDER BY (language = :l) DESC NULLS LAST, (language IS NULL) DESC, language LIMIT 1"""),
                    {"k": key, "v": version, "l": language}).mappings().first()
    return dict(row) if row else None


def prompt_ref(prompt: dict) -> str:
    return f"{prompt['key']}@v{prompt['version']}" + (f".{prompt['language']}" if prompt.get("language") else "")


def call_json(sf: sessionmaker, *, agent_key: str, purpose: str, schema: type[BaseModel], task_vars: dict, data: dict,
              semantic: Callable[[BaseModel], list[str]] | None, now: datetime, job_id: int | None = None,
              event_id: int | None = None, article_id: int | None = None, transport: Callable | None = None,
              model_override: int | None = None, inherit_models_from: str | None = None,
              prompt_version: int | None = None, language: str | None = None,
              sleep: Callable[[float], None] | None = None) -> CallOutcome:
    transport = transport or default_transport
    with sf() as s:
        if not settings_store.get(s, "ai")["enabled"]:
            raise AIUnavailable("AI is switched off in settings")
        agent = s.execute(text("SELECT * FROM ai_agent_profile WHERE agent_key = :k AND active"),
                          {"k": agent_key}).mappings().first()
        if agent is None:
            raise AIUnavailable(f"no active agent profile {agent_key}")
        primary, fallback = model_config(s, agent["model_config_id"]), model_config(s, agent["fallback_model_config_id"])
        if primary is None and inherit_models_from:  # a reporter role without its own model uses the base reporter's
            base = s.execute(text("SELECT model_config_id, fallback_model_config_id FROM ai_agent_profile "
                                  "WHERE agent_key = :k AND active"), {"k": inherit_models_from}).first()
            if base:
                primary, fallback = model_config(s, base[0]), model_config(s, base[1])
        if model_override is not None:  # comparison runs: exactly this model, no fallback
            primary, fallback = model_config(s, model_override), None
        if fallback is not None and (fallback["last_test_ok"] is False or model_paused(s, fallback["id"], now)):
            fallback = None  # a fallback known not to work only adds a failure to every job
        if primary is not None and model_override is None and (why := model_paused(s, primary["id"], now)):
            if fallback is None:
                raise AIUnavailable(f"model {primary['name']} {why}")
            primary, fallback = fallback, None
        if primary is None:
            raise AIUnavailable(f"agent {agent_key} has no enabled model")
        prompt = resolve_prompt(s, agent["prompt_key"], prompt_version or agent["prompt_version"], language)
        if prompt is None:
            raise AIUnavailable(f"agent {agent_key} has no prompt v{prompt_version or agent['prompt_version']}")
        budgets = settings_store.get(s, "budgets")
        desk = settings_store.get(s, "desk")["language"]
    task_vars = {"desk_language": {"zh": "Chinese", "en": "English"}.get(desk, desk),
                 "desk_language_zh": {"zh": "中文", "en": "英文"}.get(desk, desk), **task_vars}
    ref = prompt_ref(prompt)
    task = Template(prompt["user_template"]).safe_substitute(task_vars)
    base_user = user_message(task, data)
    plan = [(primary, False)] + ([(fallback, True)] if fallback and fallback["id"] != primary["id"] else [])
    last_error, attempt = "no attempt", 0
    for cfg, is_fallback in plan:
        # only json_schema mode sends the schema on the wire; every other mode must read it in the prompt
        first = base_user if cfg["json_mode"] == "json_schema" else (
            base_user + "\n\nReply with one JSON object that validates against this JSON Schema "
            "(every required field, exact types, no extra fields):\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":")))
        user, repaired, max_tokens = first, False, agent["max_output_tokens"]
        while True:
            attempt += 1
            req = ChatRequest(system=prompt["system_text"], user=user, max_tokens=max_tokens,
                              json_schema=schema.model_json_schema(), schema_name=schema.__name__)
            est = cost(cfg, (len(req.system) + len(req.user)) // 3, req.max_tokens)
            with sf() as s:
                over = []
                if spent(s, now) + est > Decimal(str(budgets["global_daily_usd"])):
                    over.append("global daily budget")
                agent_cap = budgets.get("agent_daily_usd", {}).get(agent_key)
                if agent_cap is not None and spent(s, now, agent_key=agent_key) + est > Decimal(str(agent_cap)):
                    over.append(f"{agent_key} daily budget")
                if job_id is not None and spent(s, now, job_id=job_id) + est > Decimal(agent["budget_usd_per_task"]):
                    over.append("per-task budget")
                if over:
                    log_call(s, job_id=job_id, agent_key=agent_key, prompt_ref=ref, cfg=cfg, purpose=purpose,
                             event_id=event_id, article_id=article_id, attempt=attempt, fallback=is_fallback,
                             req_hash=None, result=None, status="budget_blocked", error=", ".join(over), raw=None,
                             now=now)
                    s.commit()
                    raise BudgetExceeded(", ".join(over))
            req_hash = hashlib.sha256((req.system + req.user).encode()).hexdigest()
            result, status, err = None, "ok", None
            for retry in range(cfg["max_retries"] + 1):
                try:
                    with _semaphore(cfg):
                        result = transport(cfg, req)
                    status, err = "ok", None
                    break
                except ProviderError as exc:
                    status, err = exc.status, str(exc)
                    if not exc.retryable or retry == cfg["max_retries"]:
                        break
                    (sleep or time.sleep)(exc.retry_after or min(2 ** retry * 2, 30))
            parsed, errors = None, []
            if result is not None:
                try:
                    parsed = schema.model_validate(extract_json(result.text))
                    errors = semantic(parsed) if semantic else []
                    status = "semantic_invalid" if errors else "ok"
                except ValueError as exc:  # JSONDecodeError and "no JSON object" are ValueErrors
                    status, errors = ("schema_invalid" if isinstance(exc, ValidationError) else "invalid_json",
                                      [str(exc)[:1500]])
                err = "; ".join(errors) or None
            with sf() as s:
                call_id = log_call(s, job_id=job_id, agent_key=agent_key, prompt_ref=ref, cfg=cfg,
                                   purpose=purpose, event_id=event_id, article_id=article_id, attempt=attempt,
                                   fallback=is_fallback, req_hash=req_hash, result=result, status=status, error=err,
                                   raw=result.text if result else None, now=now)
                s.commit()
            if status == "ok":
                return CallOutcome(parsed, call_id, cfg["model"], ref, agent_key)
            last_error = f"{status}: {err}"
            if status == "truncated" and max_tokens == agent["max_output_tokens"] and max_tokens < MAX_TOKENS_CAP:
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)  # one roomier try before giving up on this model
                continue
            if result is None or repaired:
                break  # provider failure, or the repair also failed: move to the fallback model
            repaired = True
            user = first + "\n\nYour previous reply was rejected. Errors:\n" + json.dumps(
                errors, ensure_ascii=False) + "\nReply again with one JSON object that fixes every error."
    raise AIFailed(last_error)

