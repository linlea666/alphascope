"""Provider-neutral chat call. Two wire formats cover OpenAI-compatible and Anthropic-compatible gateways (for
example third-party multi-model gateways); the base URL, model and key come from ai_model_config."""

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
    return ProviderError("http_error", f"HTTP {code}: {body[:300]}", retryable=code == 429 or code >= 500,
                         retry_after=after)
