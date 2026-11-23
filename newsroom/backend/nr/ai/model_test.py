"""The "test model" button: a tiny schema-constrained call that proves the endpoint, key, model name and JSON mode
all work, without touching any newsroom data. The key is never echoed."""

import json
import secrets
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.ai.envelope import extract_json
from nr.ai.prompts import MODEL_TEST_SYSTEM
from nr.ai.providers.base import ChatRequest, ProviderError
from nr.ai.runner import cost, default_transport, model_config
from nr.ai.schemas import ModelTestOut


def test_model(s: Session, model_id: int, now: datetime, transport: Callable | None = None) -> dict:
    cfg = model_config(s, model_id)
    if cfg is None:
        return {"ok": False, "stage": "config", "detail": "model not found or disabled"}
    if not cfg["api_key"]:
        return {"ok": False, "stage": "config", "detail": "no API key stored"}
    nonce = secrets.token_hex(4)
    req = ChatRequest(system=MODEL_TEST_SYSTEM, max_tokens=200, json_schema=ModelTestOut.model_json_schema(),
                      schema_name="ModelTestOut",
                      user=f'Return exactly this JSON object: {{"ok": true, "echo": "{nonce}"}}')
    out = {"ok": False, "model": cfg["model"], "protocol": cfg["protocol"], "json_mode": cfg["json_mode"]}
    try:
        r = (transport or default_transport)(cfg, req)
        out.update(latency_ms=r.latency_ms, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                   reported_model=r.model, cost_usd=float(cost(cfg, r.input_tokens, r.output_tokens)))
        try:
            parsed = ModelTestOut.model_validate(extract_json(r.text))
        except ValueError as exc:
            out.update(stage="parse", detail=f"reply is not the requested JSON: {str(exc)[:200]}", reply=r.text[:300])
        else:
            out.update(ok=parsed.ok and parsed.echo == nonce, stage="done",
                       detail="ok" if parsed.echo == nonce else "reply did not echo the nonce")
    except ProviderError as exc:
        out.update(stage="call", status=exc.status, detail=str(exc)[:300].replace(cfg["api_key"], "***")
                   if cfg["api_key"] else str(exc)[:300])
    s.execute(text("""UPDATE ai_model_config SET last_test_at = :n, last_test_ok = :ok, last_test_detail = CAST(:d AS jsonb)
                      WHERE id = :i"""), {"n": now, "ok": out["ok"], "d": json.dumps(out), "i": model_id})
    return out
