"""alphascope-live: low-latency position monitor, email alerts and (S3) copy simulation.

Every cycle (default 30 s, owner-configurable): one allMids call for prices, one clearinghouseState per monitored
wallet (subscriptions, plus leaders of running simulations), diff against the last snapshot -> position events,
then deliver due alert emails. The first snapshot of a wallet is a silent baseline, so restarts never spam.

Run with: python -m app.live
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.alerts.diff import Pos, diff, parse_clearinghouse
from app.alerts.mailer import Ev, Sub, compose, in_quiet_hours, passes, ready, send
from app.config import Settings, get_settings
from app.sim.engine import step_simulations
from app.db import session_factory
from app.sources.hl_client import HyperliquidInfoClient, WeightBucket

log = logging.getLogger("alphascope.live")
OWNER = "default"
SEND_ATTEMPTS = 3


def monitored_wallets(session: Session, venue: str = "hyperliquid") -> list[tuple[int, str]]:
    sims = """UNION SELECT w.id, w.address FROM copy_sim c JOIN wallet w ON w.id = c.leader_wallet_id
              WHERE c.status = 'running' AND w.venue = :v""" \
        if session.execute(text("SELECT to_regclass('copy_sim')")).scalar() else ""
    return [tuple(r) for r in session.execute(text(f"""
        SELECT w.id, w.address FROM alert_subscription s JOIN wallet w ON w.id = s.wallet_id
        WHERE s.enabled AND s.owner = :o AND w.venue = :v {sims}"""), {"o": OWNER, "v": venue}).all()]


def load_state(session: Session, wallet_id: int) -> tuple[bool, dict[str, Pos], dict[str, bool]]:
    seen = session.execute(text("SELECT 1 FROM live_account WHERE wallet_id = :w AND error IS NULL"),
                           {"w": wallet_id}).scalar() is not None
    rows = session.execute(text("""
        SELECT coin, szi, entry_px, leverage, liquidation_px, position_value, unrealized_pnl, leverage_type, liq_risk
        FROM live_position WHERE wallet_id = :w"""), {"w": wallet_id}).all()
    return seen, {r.coin: Pos(r.szi, r.entry_px, r.leverage, r.liquidation_px, r.position_value, r.unrealized_pnl,
                              r.leverage_type) for r in rows}, {r.coin: r.liq_risk for r in rows}


def store_snapshot(session: Session, wallet_id: int, positions: dict[str, Pos], risk: dict[str, bool],
                   account_value, ntl, now: datetime) -> None:
    session.execute(text("""
        INSERT INTO live_account (wallet_id, account_value, total_ntl_pos, fetched_at, error)
        VALUES (:w, :av, :ntl, :t, NULL)
        ON CONFLICT (wallet_id) DO UPDATE SET account_value = EXCLUDED.account_value,
          total_ntl_pos = EXCLUDED.total_ntl_pos, fetched_at = EXCLUDED.fetched_at, error = NULL"""),
        {"w": wallet_id, "av": account_value, "ntl": ntl, "t": now})
    session.execute(text("DELETE FROM live_position WHERE wallet_id = :w"), {"w": wallet_id})
    if positions:
        session.execute(text("""
            INSERT INTO live_position (wallet_id, coin, szi, entry_px, position_value, unrealized_pnl, leverage,
              leverage_type, liquidation_px, liq_risk, updated_at)
            VALUES (:w, :coin, :szi, :entry, :value, :upnl, :lev, :lt, :liq, :risk, :t)"""),
            [{"w": wallet_id, "coin": c, "szi": p.szi, "entry": p.entry_px, "value": p.position_value,
              "upnl": p.unrealized_pnl, "lev": p.leverage, "lt": p.leverage_type, "liq": p.liquidation_px,
              "risk": risk.get(c, False), "t": now} for c, p in positions.items()])


async def poll_once(sf: sessionmaker[Session], client: HyperliquidInfoClient, liq_risk_pct: float) -> dict:
    now = datetime.now(UTC)
    with sf() as s:
        wallets = monitored_wallets(s)
    if not wallets:
        return {"wallets": 0, "events": 0, "errors": 0, "mids": {}}
    mids_raw = (await client.info({"type": "allMids"})).data
    mids = {k: Decimal(v) for k, v in mids_raw.items() if not k.startswith("@")}
    n_events = errors = 0
    for wid, address in wallets:
        try:
            r = await client.info({"type": "clearinghouseState", "user": address})
        except Exception as exc:  # noqa: BLE001 — one wallet must not stop the cycle
            errors += 1
            with sf() as s:
                s.execute(text("""
                    INSERT INTO live_account (wallet_id, fetched_at, error) VALUES (:w, :t, :e)
                    ON CONFLICT (wallet_id) DO UPDATE SET error = EXCLUDED.error"""),
                    {"w": wid, "t": now, "e": repr(exc)[:300]})
                s.commit()
            continue
        after, av, ntl = parse_clearinghouse(r.data)
        with sf() as s:
            seen, before, was_risk = load_state(s, wid)
            events, risk = diff(before, after, mids, liq_risk_pct, was_risk)
            if seen and events:
                s.execute(text("""
                    INSERT INTO position_event (wallet_id, coin, kind, t, szi_before, szi_after, px, notional_change,
                                                details, notify_status)
                    VALUES (:w, :coin, :kind, :t, :b, :a, :px, :ntl, CAST(:d AS jsonb), 'pending')"""),
                    [{"w": wid, "coin": e.coin, "kind": e.kind, "t": now, "b": e.szi_before, "a": e.szi_after,
                      "px": e.px, "ntl": e.notional_change, "d": json.dumps(e.details)} for e in events])
                n_events += len(events)
            store_snapshot(s, wid, after, risk, av, ntl, now)
            s.commit()
    return {"wallets": len(wallets), "events": n_events, "errors": errors, "mids": mids}


def notify_pending(sf: sessionmaker[Session], now: datetime | None = None, sender=send) -> dict:
    """Deliver due alert emails. `sender` is injectable for tests."""
    now = now or datetime.now(UTC)
    sent = held = skipped = failed = 0
    with sf() as s:
        live = settings_store.get(s, "live")
        smtp = settings_store.get(s, "smtp")
        password = settings_store.get_secret(s, "smtp")
        configured = bool(smtp.get("host") and smtp.get("username") and password)
        pending = s.execute(text("""
            SELECT e.id, e.wallet_id, e.coin, e.kind, e.t AS event_t, e.szi_before, e.szi_after, e.px, e.notional_change, e.details,
                   sub.events, sub.min_notional, sub.min_change_pct, sub.coins, sub.recipients, sub.quiet_start,
                   sub.quiet_end, sub.enabled
            FROM position_event e
            LEFT JOIN alert_subscription sub ON sub.wallet_id = e.wallet_id AND sub.owner = :o
            WHERE e.notify_status = 'pending' ORDER BY e.t"""), {"o": OWNER}).all()
        by_wallet: dict[int, list] = {}
        for row in pending:
            by_wallet.setdefault(row.wallet_id, []).append(row)
        sent_last_hour = s.execute(text("""
            SELECT count(*) FROM notification WHERE status = 'sent' AND sent_at > :t"""),
            {"t": now - timedelta(hours=1)}).scalar_one()
        for wid, rows in by_wallet.items():
            first = rows[0]
            if first.events is None or not first.enabled or not configured:
                s.execute(text("UPDATE position_event SET notify_status = 'skipped' WHERE id = ANY(:ids)"),
                          {"ids": [r.id for r in rows]})
                skipped += len(rows)
                continue
            sub = Sub(list(first.events), float(first.min_notional or 0), float(first.min_change_pct or 0),
                      list(first.coins or []), list(first.recipients or []), first.quiet_start, first.quiet_end)
            # event_t: Row.t is SQLAlchemy's tuple accessor, not the column
            evs = [Ev(r.id, r.coin, r.kind, r.event_t, r.szi_before, r.szi_after, r.px, r.notional_change, r.details or {})
                   for r in rows]
            keep = [e for e in evs if passes(sub, e)]
            drop = [e.id for e in evs if not passes(sub, e)]
            if drop:
                s.execute(text("UPDATE position_event SET notify_status = 'skipped' WHERE id = ANY(:ids)"), {"ids": drop})
                skipped += len(drop)
            if not keep:
                continue
            if not ready(keep, now, int(live["coalesce_seconds"])) or in_quiet_hours(sub, now, live["timezone"]) \
                    or sent_last_hour >= int(live["max_emails_per_hour"]):
                held += len(keep)
                continue
            w = s.execute(text("""
                SELECT w.address, coalesce(a.alias, w.display_name) AS name, la.account_value FROM wallet w
                LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
                LEFT JOIN live_account la ON la.wallet_id = w.id WHERE w.id = :w"""), {"w": wid, "o": OWNER}).one()
            positions = [dict(p) for p in s.execute(text("""
                SELECT coin, szi, entry_px, position_value, unrealized_pnl, leverage FROM live_position
                WHERE wallet_id = :w ORDER BY abs(position_value) DESC NULLS LAST"""), {"w": wid}).mappings().all()]
            name = w.name or f"{w.address[:6]}…{w.address[-4:]}"
            subject, text_body, html_body = compose(name, w.address, keep, positions, w.account_value,
                                                    smtp.get("public_url") or "", live["timezone"])
            recipients = sub.recipients or list(smtp.get("recipients") or [])
            error = None
            attempts = 0
            for attempts in range(1, SEND_ATTEMPTS + 1):
                try:
                    sender(smtp, password, recipients, subject, text_body, html_body)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = repr(exc)[:500]
                    time.sleep(min(2 * attempts, 5) if sender is send else 0)
            status = "sent" if error is None else "failed"
            s.execute(text("""
                INSERT INTO notification (wallet_id, event_ids, recipients, subject, status, attempts, error, sent_at)
                VALUES (:w, :ids, :r, :subj, :st, :n, :err, :sent)"""),
                {"w": wid, "ids": [e.id for e in keep], "r": recipients, "subj": subject, "st": status, "n": attempts,
                 "err": error, "sent": now if status == "sent" else None})
            s.execute(text("UPDATE position_event SET notify_status = :st, notified_at = :t WHERE id = ANY(:ids)"),
                      {"st": status, "t": now, "ids": [e.id for e in keep]})
            if status == "sent":
                sent += 1
                sent_last_hour += 1
            else:
                failed += 1
        s.commit()
    return {"sent": sent, "held": held, "skipped": skipped, "failed": failed}


async def fetch_market_meta(client: HyperliquidInfoClient) -> tuple[dict[str, Decimal], dict[str, int]]:
    """Hourly funding rate and max leverage per perp coin (one metaAndAssetCtxs call)."""
    meta, ctxs = (await client.info({"type": "metaAndAssetCtxs"})).data
    funding: dict[str, Decimal] = {}
    max_lev: dict[str, int] = {}
    for asset, ctx in zip(meta["universe"], ctxs):
        if ctx.get("funding") is not None:
            funding[asset["name"]] = Decimal(ctx["funding"])
        if asset.get("maxLeverage"):
            max_lev[asset["name"]] = int(asset["maxLeverage"])
    return funding, max_lev


def write_status(sf: sessionmaker[Session], status: dict) -> None:
    with sf() as s:
        settings_store.put(s, "live_status", status)
        s.commit()


async def run(settings: Settings) -> None:
    sf = session_factory()
    # Own bucket: the live monitor never waits behind the batch worker's long-running jobs.
    budget = settings.hl_weight_per_minute * settings.live_weight_safety
    async with HyperliquidInfoClient(settings.hl_info_url, budget, settings.hl_timeout_seconds, settings.hl_max_retries,
                                     bucket=WeightBucket(budget)) as client:
        market_hour: datetime | None = None
        funding: dict[str, Decimal] | None = None
        max_lev: dict[str, int] = {}
        while True:
            t0 = time.monotonic()
            with sf() as s:
                live = settings_store.get(s, "live")
            interval = max(10, int(live.get("interval_seconds") or 30))
            status: dict = {"cycle_started_at": datetime.now(UTC).isoformat(), "interval_seconds": interval}
            try:
                r = await poll_once(sf, client, float(live.get("liq_risk_pct") or 5))
                status.update({k: v for k, v in r.items() if k != "mids"})
                # funding rates and per-coin max leverage change slowly: refresh once per hour
                hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                if market_hour != hour:
                    try:
                        funding, max_lev = await fetch_market_meta(client)
                        market_hour = hour
                    except Exception:  # noqa: BLE001 — keep the previous values, retry next cycle
                        log.warning("metaAndAssetCtxs failed", exc_info=True)
                # copy simulations react to the same snapshot
                status["sim"] = step_simulations(sf, r.get("mids") or {}, funding, max_lev)
                status["notify"] = notify_pending(sf)
            except Exception as exc:  # noqa: BLE001 — keep the monitor alive
                log.exception("live cycle failed")
                status["error"] = repr(exc)[:500]
            status["cycle_seconds"] = round(time.monotonic() - t0, 2)
            status["api_weight_total"] = client.usage.weight
            write_status(sf, status)
            log.info("live: %s", {k: status.get(k) for k in ("wallets", "events", "errors", "cycle_seconds", "notify")})
            await asyncio.sleep(max(1.0, interval - (time.monotonic() - t0)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(get_settings()))


if __name__ == "__main__":
    main()
