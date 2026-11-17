"""OKX public copy-trading / market API client with a per-process request budget.

The priapi endpoints are undocumented: responses are checked for code == "0" and the expected shape, and
anything else raises OkxError so a crawl fails loudly instead of storing a half-understood page.
"""

import asyncio
import logging
import random
import time

import httpx

from app.sources.hl_client import UsageStats, WeightBucket, shared_bucket

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
LEADERBOARD = "/priapi/v5/content/public/community/leaderboard"
PROFILE = "/priapi/v5/content/public/user-info"
HISTORY = "/priapi/v5/ecotrade/public/community/user/position-history"
CURRENT = "/priapi/v5/ecotrade/public/community/user/position-current"
ASSET = "/priapi/v5/ecotrade/public/community/user/asset"
WINDOW_SPANS = {"7d": "7", "30d": "30", "90d": "90", "365d": "365"}


class OkxError(Exception):
    pass


class OkxClient:
    def __init__(self, base_url: str, requests_per_minute: float, timeout: float = 20.0, max_retries: int = 4,
                 transport: httpx.AsyncBaseTransport | None = None, bucket: WeightBucket | None = None):
        self.bucket = bucket or shared_bucket(requests_per_minute, "okx")
        self.max_retries = max_retries
        self.usage = UsageStats()
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport,
                                         headers={"User-Agent": UA, "Accept": "application/json"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def get(self, path: str, params: dict) -> object:
        """`data` of a successful response; retries timeouts, 429 and 5xx with backoff."""
        name = path.rsplit("/", 1)[-1]
        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire(1)
            self.usage.requests += 1
            self.usage.weight += 1
            self.usage.by_type[name] = self.usage.by_type.get(name, 0) + 1
            try:
                resp = await self._client.get(path, params={**params, "t": int(time.time() * 1000)})
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                body = resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if (status is not None and status != 429 and status < 500) or attempt == self.max_retries:
                    self.usage.errors += 1
                    raise OkxError(f"{name}: {exc}") from exc
                self.usage.retries += 1
                retry_after = float(exc.response.headers.get("Retry-After", 0) or 0) if status == 429 else 0
                backoff = max(retry_after, min(30.0, 2 ** attempt)) + random.uniform(0, 0.5)
                log.warning("okx %s failed (%s); retry %d in %.1fs", name, exc, attempt + 1, backoff)
                await asyncio.sleep(backoff)
                continue
            if not isinstance(body, dict) or str(body.get("code")) != "0":
                self.usage.errors += 1
                raise OkxError(f"{name}: code={body.get('code') if isinstance(body, dict) else '?'} "
                               f"msg={body.get('msg') if isinstance(body, dict) else body!r:.200}")
            return body.get("data")
        raise RuntimeError("unreachable")

    async def leaderboard(self, window: str, page: int, size: int) -> dict:
        data = await self.get(LEADERBOARD, {"page": page, "size": size, "sortBy": "pnl_ratio",
                                            "timeSpan": WINDOW_SPANS[window]})
        if not isinstance(data, dict) or not isinstance(data.get("ranks"), list):
            raise OkxError("leaderboard: missing ranks")
        return data

    async def profile(self, author_id: str) -> dict:
        data = await self.get(PROFILE, {"authorId": author_id})
        if not isinstance(data, dict):
            raise OkxError("user-info: unexpected shape")
        return data

    async def history(self, unique_name: str, after: str | None = None, limit: int = 100) -> list[dict]:
        params = {"instType": "SWAP", "limit": limit, "sortType": 1, "uniqueName": unique_name}
        if after:
            params["after"] = after
        data = await self.get(HISTORY, params)
        if not isinstance(data, list):
            raise OkxError("position-history: data is not a list")
        return data

    async def current(self, unique_name: str) -> list[dict]:
        """Open positions, flattened from OKX's per-currency groups (each group has posData)."""
        data = await self.get(CURRENT, {"uniqueName": unique_name})
        if not isinstance(data, list):
            raise OkxError("position-current: data is not a list")
        out = []
        for group in data:
            if not isinstance(group, dict) or not isinstance(group.get("posData", []), list):
                raise OkxError("position-current: unexpected group")
            out.extend(p for p in group.get("posData") or [] if isinstance(p, dict))
        return out

    async def asset(self, unique_name: str) -> list[dict]:
        data = await self.get(ASSET, {"uniqueName": unique_name})
        if not isinstance(data, list):
            raise OkxError("asset: data is not a list")
        return data

    async def instruments(self) -> list[dict]:
        data = await self.get("/api/v5/public/instruments", {"instType": "SWAP"})
        if not isinstance(data, list):
            raise OkxError("instruments: data is not a list")
        return data

    async def tickers(self) -> list[dict]:
        data = await self.get("/api/v5/market/tickers", {"instType": "SWAP"})
        if not isinstance(data, list):
            raise OkxError("tickers: data is not a list")
        return data

    async def candles(self, inst_id: str, bar: str, after: int | None = None, limit: int = 100) -> list[list]:
        """Candles older than `after` (ms), newest first: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]."""
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        if after:
            params["after"] = after
        data = await self.get("/api/v5/market/history-candles", params)
        if not isinstance(data, list):
            raise OkxError("history-candles: data is not a list")
        return data

    async def index_price(self, ccy: str) -> float | None:
        data = await self.get("/api/v5/market/index-tickers", {"instId": f"{ccy}-USD"})
        try:
            return float(data[0]["idxPx"])
        except (TypeError, KeyError, IndexError, ValueError):
            return None
