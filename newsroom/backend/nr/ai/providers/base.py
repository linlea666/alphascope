"""Provider-neutral chat call. Two wire formats cover OpenAI-compatible and Anthropic-compatible gateways (for
example third-party multi-model gateways); the base URL, model and key come from ai_model_config."""

import re
from dataclasses import dataclass


@dataclass
class ChatRequest:
    system: str
    user: str
    max_tokens: int
    json_schema: dict | None = None
    schema_name: str = "output"


@dataclass
class ChatResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None
    http_status: int
    latency_ms: int
    model: str


class ProviderError(Exception):
    def __init__(self, status: str, message: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retryable, self.retry_after = status, retryable, retry_after


def classify_http(code: int, body: str, retry_after: str | None) -> ProviderError:
    after = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None
    title = re.search(r"<title>(.*?)</title>", body or "", re.S | re.I)
    if title:  # an HTML page means a proxy or firewall answered, not the model API
        hint = " (blocked by the gateway's firewall: ask the provider to allow this server's IP)" \
            if "cloudflare" in body.lower() or "attention required" in title.group(1).lower() else ""
        return ProviderError("http_error", f"HTTP {code}: {title.group(1).strip()[:120]}{hint}",
                             retryable=code == 429 or code >= 500, retry_after=after)
    return ProviderError("http_error", f"HTTP {code}: {body[:300]}", retryable=code == 429 or code >= 500,
                         retry_after=after)
