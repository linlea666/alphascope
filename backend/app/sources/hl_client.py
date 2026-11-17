"""Hyperliquid info API client with weight-aware rate limiting.

Hyperliquid limits REST traffic to 1200 weight/minute per IP. Weights (docs, verified 2026-11):
clearinghouseState/l2Book/allMids/orderStatus = 2, userRole = 60, other info requests = 20,
plus +1 per 20 items for userFills*/ledger/funding style responses and +1 per 60 candles.
"""

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

BASE_WEIGHTS = {
    "clearinghouseState": 2,
    "spotClearinghouseState": 2,
    "l2Book": 2,
    "allMids": 2,
    "orderStatus": 2,
    "exchangeStatus": 2,
    "userRole": 60,
}
DEFAULT_WEIGHT = 20
ITEMS_PER_EXTRA_WEIGHT = {
    "userFills": 20,
    "userFillsByTime": 20,
    "userFunding": 20,
    "userNonFundingLedgerUpdates": 20,
    "historicalOrders": 20,
    "candleSnapshot": 60,
}


def base_weight(request_type: str) -> int:
    return BASE_WEIGHTS.get(request_type, DEFAULT_WEIGHT)


def extra_weight(request_type: str, response: object) -> int:
    per = ITEMS_PER_EXTRA_WEIGHT.get(request_type)
    if per is None or not isinstance(response, list):
        return 0
    return len(response) // per


class WeightBucket:
    """Token bucket refilled continuously at `per_minute` weight/minute.

    Thread-safe and event-loop agnostic: the worker runs several jobs in parallel threads, each with
    its own asyncio loop, and they must all draw from one per-process budget (see shared_bucket).
    """

    def __init__(self, per_minute: float, clock=time.monotonic):
        self.capacity = per_minute
        self.rate = per_minute / 60.0
        self.tokens = per_minute
        self.clock = clock
        self.updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def wait_time(self, weight: float) -> float:
        with self._lock:
            self._refill()
            return 0.0 if self.tokens >= weight else (weight - self.tokens) / self.rate

    def try_take(self, weight: float) -> float:
        """Take `weight` if available and return 0, otherwise return the seconds to wait."""
        weight = min(weight, self.capacity)
        with self._lock:
            self._refill()
            if self.tokens >= weight:
                self.tokens -= weight
                return 0.0
            return (weight - self.tokens) / self.rate

    async def acquire(self, weight: float) -> None:
        while (delay := self.try_take(weight)) > 0:
            await asyncio.sleep(delay)

    def charge(self, weight: float) -> None:
        """Post-hoc charge (response-size surcharge); may drive the bucket negative."""
        with self._lock:
            self._refill()
            self.tokens -= weight


_shared: dict[tuple[str, float], WeightBucket] = {}
_shared_lock = threading.Lock()


def shared_bucket(per_minute: float, name: str = "hyperliquid") -> WeightBucket:
    """One bucket per process and budget, shared by every job that calls the same API."""
    with _shared_lock:
        if (name, per_minute) not in _shared:
            _shared[(name, per_minute)] = WeightBucket(per_minute)
        return _shared[(name, per_minute)]


@dataclass
class UsageStats:
    requests: int = 0
    weight: int = 0
    retries: int = 0
    errors: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"requests": self.requests, "weight": self.weight, "retries": self.retries,
                "errors": self.errors, "by_type": dict(self.by_type)}


@dataclass
class InfoResponse:
    data: object
    body: bytes
    weight: int


class HyperliquidInfoClient:
    def __init__(self, url: str, weight_per_minute: float, timeout: float = 60.0, max_retries: int = 4,
                 transport: httpx.AsyncBaseTransport | None = None, bucket: WeightBucket | None = None):
        self.url = url
        self.bucket = bucket or shared_bucket(weight_per_minute)
        self.max_retries = max_retries
        self.usage = UsageStats()
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport,
                                         headers={"Content-Type": "application/json"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def info(self, payload: dict) -> InfoResponse:
        rtype = payload["type"]
        weight = base_weight(rtype)
        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire(weight)
            self.usage.requests += 1
            self.usage.weight += weight
            try:
                resp = await self._client.post(self.url, json=payload)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                retriable = status is None or status == 429 or status >= 500
                if not retriable or attempt == self.max_retries:
                    self.usage.errors += 1
                    raise
                self.usage.retries += 1
                backoff = min(60.0, 2 ** attempt * 2) + random.uniform(0, 1)
                log.warning("info %s failed (%s); retry %d in %.1fs", rtype, exc, attempt + 1, backoff)
                await asyncio.sleep(backoff)
                continue
            extra = extra_weight(rtype, data)
            if extra:
                self.bucket.charge(extra)
                self.usage.weight += extra
            self.usage.by_type[rtype] = self.usage.by_type.get(rtype, 0) + weight + extra
            return InfoResponse(data=data, body=resp.content, weight=weight + extra)
        raise RuntimeError("unreachable")
