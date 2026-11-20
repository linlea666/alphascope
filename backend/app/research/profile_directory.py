"""Public BTC/ETH current-position lists. Preserve discoveries even from a partial scan."""
import re
from datetime import UTC, datetime, timedelta
from time import sleep

import httpx
from sqlalchemy import text

from app import settings_store
from app.research.exchange import details, retry_time

SCAN = "binance_profile_discovery"
ID = re.compile(r"[0-9]{19}")
PAGE_LIMIT = 100


def scan_coin(s, coin, start_page=1):
    seen = set()
    people = {}
    total = None
    pages = 0
    status = "partial"
    retry_after = None
    next_page = 1
    for page in range(start_page, start_page + PAGE_LIMIT):
        try:
            if page > start_page:
                sleep(1)
            response = details(coin, "ALL", "all", "traders", "DESC", page)
            rows = response["rows"]
            if total is None:
                total = response["total"]
            consistent = total >= 0 and total == response["total"]
            for row in rows:
                pid = str(row["public_id"] or "")
                identity = (pid, row["side"])
                if identity in seen:
                    consistent = False
                seen.add(identity)
                if ID.fullmatch(pid):
                    people[pid] = row["name"]
                elif pid:
                    consistent = False
            pages += 1
            if not consistent:
                break
            processed = (start_page - 1) * 10 + len(seen)
            if processed == total:
                status = "complete" if start_page == 1 else "partial"
                break
            if len(rows) < 10 or processed > total:
                break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (418, 429):
                retry_after = retry_time(exc.response.headers.get("retry-after")).isoformat()
                status = "rate_limited"
            break
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            break
    else:
        next_page = start_page + PAGE_LIMIT
    now = datetime.now(UTC)
    col = {"BTC": "btc_seen", "ETH": "eth_seen"}[coin]
    if people:
        s.execute(text(f"""INSERT INTO binance_profile_directory(profile_id,name,{col}) VALUES(:id,:name,:seen)
            ON CONFLICT(profile_id) DO UPDATE SET name=coalesce(EXCLUDED.name,binance_profile_directory.name),
            {col}=EXCLUDED.{col}"""), [dict(id=pid,name=name,seen=now) for pid,name in sorted(people.items())])
    return {"status":status, "pages":pages, "total_rows":total, "public_profiles":len(people),
            "at":now.isoformat(), "resume_at":retry_after, "next_page":next_page, "start_page":start_page}


def discover(sf):
    for coin in ("BTC", "ETH"):
        with sf.begin() as s:
            if not s.scalar(text("SELECT pg_try_advisory_xact_lock(88592023)")):
                return
            meta = settings_store.get(s, SCAN)
            now = datetime.now(UTC)
            if meta.get("resume_at") and datetime.fromisoformat(meta["resume_at"]) > now:
                return
            prev = meta.get(coin, {})
            if prev and datetime.fromisoformat(prev["at"]) > now - timedelta(minutes=29):
                continue
            report = scan_coin(s, coin, prev.get("next_page", 1))
            settings_store.put(s, SCAN, {coin:report, "resume_at":report["resume_at"]})


def select_due(s, minutes, turn):
    # Four turns per cycle: priority/current, newcomers, priority/current, least recently attempted.
    # Cold turns sort by the oldest attempted OR discovery time, so new arrivals cannot starve older profiles.
    return s.scalar(text("""SELECT profile_id FROM binance_profile_directory
        WHERE attempted IS NULL OR attempted <= now() - make_interval(mins => :m * CASE
          WHEN pinned OR greatest(btc_seen, eth_seen) >= now()-interval '2 hours' THEN 1 ELSE 8 END)
        ORDER BY CASE WHEN :turn=3 THEN 0 WHEN :turn=1 THEN CASE WHEN attempted IS NULL THEN 0 ELSE 1 END
          WHEN pinned THEN 0 WHEN priority_risk AND greatest(btc_seen,eth_seen)>=now()-interval '2 hours' THEN 1
          WHEN greatest(btc_seen,eth_seen)>=now()-interval '2 hours' THEN 2 ELSE 3 END,
          coalesce(attempted,first_seen),profile_id LIMIT 1"""), {"m":minutes, "turn":turn})
