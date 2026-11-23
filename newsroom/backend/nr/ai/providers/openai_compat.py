import time

import httpx

from nr.ai.providers.base import ChatRequest, ChatResult, ProviderError, classify_http


def call(cfg: dict, req: ChatRequest) -> ChatResult:
    body = {"model": cfg["model"], "max_tokens": req.max_tokens,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]}
    if cfg["json_mode"] == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif cfg["json_mode"] == "json_schema" and req.json_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": req.schema_name,
                                                                           "schema": req.json_schema}}
    body.update(cfg.get("extra_body") or {})
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["auth_header"] == "bearer" else \
        {"x-api-key": cfg["api_key"]}
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{cfg['base_url'].rstrip('/')}/chat/completions", json=body, headers=headers,
                       timeout=cfg["timeout_s"])
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", str(exc), retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("http_error", f"{type(exc).__name__}: {exc}", retryable=True) from exc
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code >= 400:
        raise classify_http(r.status_code, r.text, r.headers.get("retry-after"))
    try:
        d = r.json()
        choice = d["choices"][0]
        text = choice["message"].get("content") or ""
    except (ValueError, KeyError, IndexError) as exc:
        raise ProviderError("invalid_json", f"unexpected response: {r.text[:300]}") from exc
    u = d.get("usage") or {}
    if choice.get("finish_reason") == "length":
        # thinking models (DeepSeek's default included) spend part of max_tokens on reasoning before any answer
        reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        raise ProviderError("truncated", f"output hit max_tokens ({u.get('completion_tokens')} output tokens"
                            + (f", {reasoning} of them reasoning: turn thinking off or raise max output tokens)"
                               if reasoning else ")"))
    if choice["message"].get("refusal"):
        raise ProviderError("refused", str(choice["message"]["refusal"])[:300])
    return ChatResult(text, u.get("prompt_tokens"), u.get("completion_tokens"), choice.get("finish_reason"),
                      r.status_code, ms, d.get("model") or cfg["model"])
