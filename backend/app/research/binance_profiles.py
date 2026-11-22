"""Read explicitly shared UM profiles, keeping only BTCUSDT and ETHUSDT positions."""
import json
import re
from time import sleep
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from app import settings_store as store
from app.alerts.mailer import send
from app.market.structure import Row, build
from app.research.exchange import number, retry_time
from app.research.profile_directory import SCAN, select_due

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
    cfg = {**DEFAULT, **store.get(s, KEY)}
    cfg.pop("attempts", None)
    pins = s.scalars(text("SELECT profile_id FROM binance_profile_directory WHERE pinned ORDER BY profile_id")).all()
    return {**cfg, "ids": pins, "has_credential": store.has_secret(s, KEY),
            "discovery": store.get(s, SCAN),
            "directory_count": s.scalar(text("SELECT count(*) FROM binance_profile_directory"))}


def read(client, path, params, headers=None):
    response = client.get(BASE + path, params=params, headers=headers)
    if response.status_code in (403, 451):
        raise Unavailable("source_restricted")
    if response.status_code == 401 or response.is_redirect:
        raise Unavailable("access_denied" if headers else "source_restricted")
    if response.status_code in (418, 429):
        raise Unavailable("rate_limited", retry_time(response.headers.get("retry-after")))
    response.raise_for_status()
    body = response.json()
    if str(body.get("code")) in ("100001005", "100002002"):
        raise Unavailable("access_denied" if headers else "source_restricted")
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


def fetch_profile(client, pid, headers, verify_only=False):
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
        if verify_only and rows and (total is None or total >= offset):
            return True, records
        if offset == total or (len(rows) < 9 and total is None):
            return True, records
        if len(rows) < 9 or (total is not None and (offset > total or total > 36)):
            break
    raise ValueError("incomplete profile")


def poll(sf):
    # Claim and publish in short transactions under the advisory lock that also serializes configuration changes;
    # the upstream requests in between (up to four pages, two seconds apart) hold no lock or open transaction.
    with sf.begin() as s:
        if not s.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
            return
        cfg = {**DEFAULT, **store.get(s, KEY)}
        now = datetime.now(UTC)
        if not cfg["enabled"] or cfg["status"] in STOPPED:
            return
        if cfg.get("last_attempt") and datetime.fromisoformat(cfg["last_attempt"]) > now - timedelta(seconds=55):
            return
        if cfg.get("resume_at") and datetime.fromisoformat(cfg["resume_at"]) > now:
            return
        turn = cfg.get("next_turn", 0)
        pid = select_due(s, cfg["minutes"], turn)
        if not pid:
            return
        s.execute(text("UPDATE binance_profile_directory SET attempted=:at WHERE profile_id=:pid"), {"at":now,"pid":pid})
        store.put(s, KEY, {"next_turn": (turn+1)%4, "last_attempt": now.isoformat(), "last_profile": pid})
        secret = store.get_secret(s, KEY)
    changes, shared, rows = {"resume_at": None}, None, None
    try:
        if not secret:
            raise Unavailable("access_denied")
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            shared, rows = fetch_profile(client, pid, json.loads(secret))
        changes["status"] = "ready" if shared else "hidden"
        if shared:
            changes["last_success"] = datetime.now(UTC).isoformat()
    except Unavailable as exc:
        changes.update(status=exc.reason, resume_at=exc.resume.isoformat() if exc.resume else None)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        changes["status"] = "upstream_error"
    with sf.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK})
        current = {**DEFAULT, **store.get(s, KEY)}
        # The owner saved, cleared or replaced the credential meanwhile: this result belongs to the old one.
        if store.get_secret(s, KEY) != secret or not current["enabled"]:
            return
        if rows is not None:
            s.execute(text("""INSERT INTO binance_profile_snapshot(profile_id,shared,positions)
                VALUES (:pid,:shared,CAST(:rows AS jsonb))"""), {"pid": pid, "shared": shared, "rows": json.dumps(rows)})
            near = any(p["liq"] and 0 < (p["mark"] - p["liq"]) * (1 if p["side"] == "LONG" else -1)
                       / p["mark"] <= 0.05 for p in rows)
            s.execute(text("UPDATE binance_profile_directory SET priority_risk=:risk WHERE profile_id=:pid"),
                      {"risk":bool(near), "pid":pid})
        if changes["status"] in STOPPED and current["status"] not in STOPPED:
            changes["alert"] = alert(s, changes["status"])
        store.put(s, KEY, changes)


STOPPED = ("access_denied", "source_restricted")


def alert(s, status):
    """Collection stops until the owner saves a new credential, so say so once instead of failing silently."""
    smtp, password = store.get(s, "smtp"), store.get_secret(s, "smtp")
    if not (smtp.get("host") and smtp.get("username") and password and smtp.get("recipients")):
        return "not_configured"
    reason = "登录凭证已失效或被拒绝" if status == "access_denied" else "币安拒绝了本机地址的访问"
    link = f"{smtp['public_url'].rstrip('/')}/zh/settings" if smtp.get("public_url") else "设置页"
    try:
        send(smtp, password, list(smtp["recipients"]), "[AlphaScope] 币安分享仓位采集已暂停",
               f"{reason}，BTC/ETH 分享仓位采集已暂停。\n请在 {link} 重新粘贴并验证凭证，验证通过后自动恢复。")
        return "sent"
    except Exception:  # noqa: BLE001 - the status itself is already recorded
        return "failed"


def structure(s, coin):
    cfg = state(s)
    cutoff = datetime.now(UTC) - timedelta(minutes=2 * cfg["minutes"])
    fresh = s.execute(text("""SELECT * FROM (SELECT DISTINCT ON (profile_id) profile_id,observed_at,shared,positions
        FROM binance_profile_snapshot WHERE observed_at>=:cutoff ORDER BY profile_id,observed_at DESC,id DESC) recent
        WHERE shared"""), {"cutoff":cutoff}).mappings().all() if cfg["enabled"] else []
    positions = [{**p, "profile_id": r["profile_id"], "observed_at": r["observed_at"]} for r in fresh
                 for p in r["positions"] if p["coin"] == coin]
    ref = max(positions, key=lambda p: p["observed_at"])["mark"] if positions else None
    rows = [Row(int(p["profile_id"]), 1 if p["side"] == "LONG" else -1, p["notional"], p["entry"],
                p["liq"], p["lev"], None) for p in positions]
    return {"coin": coin, "source": "binance_shared_profile", "ref": ref, "configured": cfg["directory_count"],
            "fresh": len(fresh), "enabled": cfg["enabled"], "last_success": cfg.get("last_success"), "status": cfg["status"],
            "holders": len({p["profile_id"] for p in positions}),
            "liq_coverage": (sum(p["notional"] for p in positions if p["liq"] and
                (p["liq"] < ref if p["side"] == "LONG" else p["liq"] > ref)) /
                sum(p["notional"] for p in positions)) if positions else None,
            "discovery": cfg["discovery"].get(coin),
            "positions": positions, "structure": build(rows, ref, entry_span=0.5, liq_span=0.5) if ref else None}
