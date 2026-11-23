"""OpenAI Responses API (POST {base}/responses), used by Codex-style gateways. Set extra_body {"stream": true} for
gateways that only answer with server-sent events."""

import json
import time

import httpx

from nr.ai.providers.base import ChatRequest, ChatResult, ProviderError, classify_http


def body_for(cfg: dict, req: ChatRequest) -> dict:
    body = {"model": cfg["model"], "instructions": req.system, "max_output_tokens": req.max_tokens,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": req.user}]}]}
    if cfg["json_mode"] == "json_object":
        body["text"] = {"format": {"type": "json_object"}}
    elif cfg["json_mode"] == "json_schema" and req.json_schema:
        body["text"] = {"format": {"type": "json_schema", "name": req.schema_name, "schema": req.json_schema,
                                   "strict": False}}
    body.update(cfg.get("extra_body") or {})
    return body


def parse_response(d: dict) -> tuple[str, dict, str | None]:
    """(text, usage, problem) from a final response object."""
    parts, refusal = [], None
    for item in d.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                parts.append(c.get("text") or "")
            elif c.get("type") == "refusal":
                refusal = c.get("refusal") or "refused"
    text = d.get("output_text") or "".join(parts)
    problem = None
    if refusal:
        problem = "refused"
    elif d.get("status") == "incomplete":
        problem = "truncated" if (d.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else "incomplete"
    return text, d.get("usage") or {}, problem


def parse_stream(lines) -> dict:
    """Collect a streamed response: text deltas plus the final response object."""
    text, final = [], None
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        ev = json.loads(data)
        if ev.get("type") == "response.output_text.delta":
            text.append(ev.get("delta") or "")
        elif ev.get("type") in ("response.completed", "response.incomplete", "response.failed"):
            final = ev.get("response") or {}
    final = final or {}
    if not final.get("output") and not final.get("output_text"):
        final["output_text"] = "".join(text)
    return final


def call(cfg: dict, req: ChatRequest) -> ChatResult:
    body = body_for(cfg, req)
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["auth_header"] == "bearer" else \
        {"x-api-key": cfg["api_key"]}
    url = f"{cfg['base_url'].rstrip('/')}/responses"
    t0 = time.monotonic()
    try:
        if body.get("stream"):
            with httpx.stream("POST", url, json=body, headers=headers, timeout=cfg["timeout_s"]) as r:
                if r.status_code >= 400:
                    r.read()
                    raise classify_http(r.status_code, r.text, r.headers.get("retry-after"))
                d = parse_stream(r.iter_lines())
                status = r.status_code
        else:
            r = httpx.post(url, json=body, headers=headers, timeout=cfg["timeout_s"])
            if r.status_code >= 400:
                raise classify_http(r.status_code, r.text, r.headers.get("retry-after"))
            d, status = r.json(), r.status_code
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", str(exc), retryable=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError("http_error", f"{type(exc).__name__}: {exc}", retryable=True) from exc
    except ValueError as exc:
        raise ProviderError("invalid_json", f"unexpected response: {exc}") from exc
    text, usage, problem = parse_response(d)
    if problem == "refused":
        raise ProviderError("refused", "the model declined")
    if problem:
        raise ProviderError("truncated", f"response {problem}")
    return ChatResult(text, usage.get("input_tokens"), usage.get("output_tokens"), d.get("status"), status,
                      int((time.monotonic() - t0) * 1000), d.get("model") or cfg["model"])
