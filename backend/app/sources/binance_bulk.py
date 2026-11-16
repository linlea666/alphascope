"""Binance USDT-M futures klines from the public bulk archive (data.binance.vision).

Verified 2026-11-16: the archive is reachable where the REST API answers HTTP 451; files are zipped
CSVs with a .CHECKSUM (sha256) next to them; monthly files cover complete months, daily files appear
the day after. Hyperliquid vs Binance hourly closes differed by a median 0.7 bps (max 12 bps) in Aug 2026.
"""

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

BASE_URL = "https://data.binance.vision/data/futures/um"
SOURCE = "binance_um"


class ChecksumError(ValueError):
    pass


@dataclass(frozen=True)
class Kline:
    t: datetime
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741
    c: Decimal
    v: Decimal
    n: int


def monthly_path(symbol: str, interval: str, month: date) -> str:
    return f"monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month:%Y-%m}.zip"


def daily_path(symbol: str, interval: str, day: date) -> str:
    return f"daily/klines/{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"


def _ts(raw: str) -> datetime:
    v = int(raw)
    if v > 10**14:  # some archives switched to microseconds
        v //= 1000
    return datetime.fromtimestamp(v / 1000, tz=UTC)


def parse_klines(zip_bytes: bytes) -> list[Kline]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one csv in archive, found {names}")
        text = z.read(names[0]).decode()
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():  # header line (present in newer files only)
            continue
        out.append(Kline(_ts(row[0]), Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4]),
                         Decimal(row[5]), int(row[8]) if len(row) > 8 and row[8] else 0))
    return out


async def fetch_verified(client: httpx.AsyncClient, path: str) -> tuple[bytes, str] | None:
    """Download a file and its checksum; None if not published (404). Raises ChecksumError on mismatch."""
    r = await client.get(f"{BASE_URL}/{path}.CHECKSUM")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    expected = r.text.split()[0].strip().lower()
    body = await client.get(f"{BASE_URL}/{path}")
    if body.status_code == 404:
        return None
    body.raise_for_status()
    actual = hashlib.sha256(body.content).hexdigest()
    if actual != expected:
        raise ChecksumError(f"{path}: sha256 {actual} != {expected}")
    return body.content, actual


async def exists(client: httpx.AsyncClient, path: str) -> bool:
    r = await client.get(f"{BASE_URL}/{path}.CHECKSUM")
    return r.status_code == 200
