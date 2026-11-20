"""Read explicitly shared UM profiles, keeping only BTCUSDT and ETHUSDT positions."""
import json
import re
from time import sleep
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import text

from app import settings_store as store
from app.market.structure import Row, build
from app.research.exchange import number

BASE = "https://www.binance.com/bapi/asset/v1"
KEY = "binance_profiles"
COINS = ("BTC", "ETH")
DEFAULT = {"ids": [], "enabled": False, "minutes": 30, "status": "idle"}
LOCK = 88592022


class Unavailable(Exception):
    def __init__(self, reason, resume=None):
        super().__init__(reason)
        self.reason = reason
        self.resume = resume


def credentials(value):
    cookie = re.search(r"""(?:^|[;\s'"])p20t=([A-Za-z0-9._%-]+)""", value)
    csrf = re.search(r"""csrftoken\s*[:=]\s*['"]?([a-zA-Z0-9]{16,64})\b""", value, re.I)
    if not cookie or not csrf:
        raise ValueError("Missing p20t or csrftoken")
    return {"cookie": "p20t=" + cookie[1], "csrftoken": csrf[1]}


def state(s):
    return {**DEFAULT, **store.get(s, KEY), "has_credential": store.has_secret(s, KEY)}


def retry_time(header):
    now = datetime.now(UTC)
    minimum = now + timedelta(hours=1)
    try:
        if header and header.isdigit():
            return max(minimum, now + timedelta(seconds=int(header)))
        return max(minimum, parsedate_to_datetime(header))
    except (ValueError, OverflowError, TypeError):
        return minimum


def read(client, path, params, headers=None):
    response = client.get(BASE + path, params=params, headers=headers)
    if response.status_code in (401, 403, 451) or response.is_redirect:
        raise Unavailable("access_denied")
    if response.status_code in (418, 429):
        raise Unavailable("rate_limited", retry_time(response.headers.get("retry-after")))
    response.raise_for_status()
    body = response.json()
    if str(body.get("code")) in ("100001005", "100002002"):
        raise Unavailable("access_denied")
    if str(body.get("code")) != "000000" or body.get("success") is False:
        raise Unavailable("upstream_error")
    return body["data"]


def position(row):
    symbol = row["symbol"]
    if symbol not in ("BTCUSDT", "ETHUSDT"):
        return None
    amount = number(row["amount"])
    if amount == 0:
        return None
    side = "LONG" if amount > 0 else "SHORT"
    if row.get("side") in ("LONG", "SHORT") and row["side"] != side:
        raise ValueError("inconsistent position side")
    mark = number(row["markPrice"])
    if not mark or mark <= 0:
        raise ValueError("missing mark")
    def positive(key):
        v = number(row.get(key) or None)
        return v if v is not None and v > 0 else None
    return {"coin": symbol[:-4], "side": side, "qty": abs(amount), "entry": positive("entryPrice"),
            "mark": mark, "liq": positive("liqPrice"), "lev": positive("leverage"), "notional": abs(amount) * mark}


def fetch_profile(client, pid, headers):
    profile = read(client, "/friendly/future/smart-money/profile", {"topTraderId": pid})
    if str(profile.get("topTraderId")) != pid or not isinstance(profile.get("sharingPosition"), bool):
        raise ValueError("unverified profile")
    if not profile["sharingPosition"] or profile.get("enable") is False:
        return False, []
    records, seen = [], set()
    offset = 0
    expected = None
    for page in range(1, 5):
        if page > 1:
            sleep(2)
        data = read(client, "/private/future/smart-money/profile/query-positions",
                    {"topTraderId": pid, "marketType": "UM", "page": page, "rows": 9}, headers)
        if isinstance(data, dict):
            total = next((int(data[k]) for k in ("total", "totalCount", "count") if k in data), None)
            rows = next((data[k] for k in ("positions", "rows", "list", "items", "data") if isinstance(data.get(k), list)), None)
        else:
            rows, total = data, None
        if not isinstance(rows, list) or len(rows) > 9 or (page > 1 and total != expected):
            raise ValueError("incomplete response")
        expected = total
        for row in rows:
            identity = (row["symbol"], row.get("side"))
            if identity in seen:
                raise ValueError("duplicate position")
            seen.add(identity)
            item = position(row)
            if item:
                records.append(item)
        offset += len(rows)
        if offset == total or (len(rows) < 9 and total is None):
            return True, records
        if len(rows) < 9 or (total is not None and (offset > total or total > 36)):
            break
    raise ValueError("incomplete profile")


def poll(sf):
    # Serialize configuration and publication across API processes and worker restarts.
    with sf.begin() as s:
        if not s.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
            return
        cfg = state(s)
        now = datetime.now(UTC)
        if not cfg["enabled"] or not cfg["ids"] or cfg["status"] == "access_denied":
            return
        if cfg.get("last_attempt") and datetime.fromisoformat(cfg["last_attempt"]) > now - timedelta(seconds=55):
            return
        if cfg.get("resume_at") and datetime.fromisoformat(cfg["resume_at"]) > now:
            return
        attempts = cfg.get("attempts", {})
        pid = min(cfg["ids"], key=lambda i: attempts.get(i, ""))
        if pid in attempts and datetime.fromisoformat(attempts[pid]) + timedelta(minutes=cfg["minutes"]) > now:
            return
        attempts[pid] = now.isoformat()
        changes = {"attempts": attempts, "last_attempt": now.isoformat(), "last_profile": pid, "resume_at": None}
        try:
            secret = store.get_secret(s, KEY)
            if not secret:
                raise Unavailable("access_denied")
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                shared, rows = fetch_profile(client, pid, json.loads(secret))
            s.execute(text("""INSERT INTO binance_profile_snapshot(profile_id,shared,positions)
                VALUES (:pid,:shared,CAST(:rows AS jsonb))"""), {"pid": pid, "shared": shared, "rows": json.dumps(rows)})
            changes["status"] = "ready" if shared else "hidden"
            if shared:
                changes["last_success"] = datetime.now(UTC).isoformat()
        except Unavailable as exc:
            changes.update(status=exc.reason, resume_at=exc.resume.isoformat() if exc.resume else None)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            changes["status"] = "upstream_error"
        store.put(s, KEY, changes)


def structure(s, coin):
    cfg = state(s)
    snaps = s.execute(text("""SELECT DISTINCT ON (profile_id) profile_id, observed_at, shared, positions
        FROM binance_profile_snapshot WHERE profile_id=ANY(:ids) ORDER BY profile_id, observed_at DESC"""),
        {"ids": cfg["ids"]}).mappings().all() if cfg["enabled"] and cfg["ids"] else []
    cutoff = datetime.now(UTC) - timedelta(minutes=2 * cfg["minutes"])
    fresh = [r for r in snaps if r["shared"] and r["observed_at"] >= cutoff]
    positions = [{**p, "profile_id": r["profile_id"], "observed_at": r["observed_at"]} for r in fresh
                 for p in r["positions"] if p["coin"] == coin]
    ref = max(positions, key=lambda p: p["observed_at"])["mark"] if positions else None
    rows = [Row(int(p["profile_id"]), 1 if p["side"] == "LONG" else -1, p["notional"], p["entry"],
                p["liq"], p["lev"], None) for p in positions]
    return {"coin": coin, "source": "binance_shared_profile", "ref": ref, "configured": len(cfg["ids"]),
            "fresh": len(fresh), "enabled": cfg["enabled"], "last_success": cfg.get("last_success"), "status": cfg["status"],
            "positions": positions, "structure": build(rows, ref, entry_span=0.5, liq_span=0.5) if ref else None}
