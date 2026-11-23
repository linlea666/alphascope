import time

import httpx

from nr.ai.providers.base import ChatRequest, ChatResult, ProviderError, classify_http


def call(cfg: dict, req: ChatRequest) -> ChatResult:
    # no temperature/top_p and no assistant prefill: newer models reject both
    body = {"model": cfg["model"], "max_tokens": req.max_tokens, "system": req.system,
            "messages": [{"role": "user", "content": req.user}]}
    if cfg["json_mode"] == "json_schema" and req.json_schema:
        body["output_config"] = {"format": {"type": "json_schema", "schema": req.json_schema}}
    body.update(cfg.get("extra_body") or {})
    headers = {"anthropic-version": cfg["anthropic_version"], "content-type": "application/json"}
    if cfg["auth_header"] == "bearer":
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    else:
        headers["x-api-key"] = cfg["api_key"]
    base = cfg["base_url"].rstrip("/")
    url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
    t0 = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=cfg["timeout_s"])
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", str(exc), retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("http_error", f"{type(exc).__name__}: {exc}", retryable=True) from exc
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code >= 400:
        raise classify_http(r.status_code, r.text, r.headers.get("retry-after"))
    try:
        d = r.json()
        text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("invalid_json", f"unexpected response: {r.text[:300]}") from exc
    stop = d.get("stop_reason")
    if stop == "refusal":
        raise ProviderError("refused", "the model declined")
    if stop == "max_tokens":
        raise ProviderError("truncated", "output hit max_tokens")
    u = d.get("usage") or {}
    return ChatResult(text, u.get("input_tokens"), u.get("output_tokens"), stop, r.status_code, ms,
                      d.get("model") or cfg["model"])
