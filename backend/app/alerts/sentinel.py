"""Price-zone sentinels: a saved zone query that reports traders who open the side inside the zone after the
sentinel was created. Built on trade_decision, so it follows fill archiving (Hyperliquid) and OKX history syncs:
a hit appears when the trader's trades are archived, not at the instant of the trade."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.alerts.mailer import send
from app.api.common import venue_clause
from app.market.stats import COHORTS

OWNER = "default"
EMAIL_LIMIT = 20  # traders listed per email


def find_hits(session: Session, s: dict, now: datetime) -> int:
    cohort = "" if s["cohort"] == "all" else " AND " + COHORTS[s["cohort"]].format(col="d.wallet_id")
    rows = session.execute(text(f"""
        INSERT INTO sentinel_hit (sentinel_id, wallet_id, first_at, avg_px, notional, found_at)
        SELECT :id, d.wallet_id, min(d.started_at), sum(d.vwap * d.size) / nullif(sum(d.size), 0), sum(d.notional), :now
        FROM trade_decision d JOIN wallet w ON w.id = d.wallet_id
        WHERE d.coin = :coin AND d.started_at >= :since AND d.vwap BETWEEN :lo AND :hi
          AND d.kind IN ('open', 'add', 'flip')
          AND CASE WHEN :side = 'long' THEN d.pos_after > 0 ELSE d.pos_after < 0 END{venue_clause(s["venue"])}{cohort}
          AND d.wallet_id NOT IN (SELECT wallet_id FROM sentinel_hit WHERE sentinel_id = :id)
        GROUP BY d.wallet_id HAVING sum(d.notional) >= :mn
        RETURNING wallet_id"""),
        {"id": s["id"], "now": now, "coin": s["coin"], "since": s["created_at"], "lo": s["px_min"], "hi": s["px_max"],
         "side": s["side"], "mn": s["min_notional"]}).all()
    session.execute(text("""UPDATE zone_sentinel SET last_checked_at = :now,
        last_hit_at = CASE WHEN :n > 0 THEN :now ELSE last_hit_at END WHERE id = :id"""),
        {"now": now, "n": len(rows), "id": s["id"]})
    return len(rows)


def _fmt(v) -> str:
    v = float(v or 0)
    return f"{v / 1e8:.2f}亿" if v >= 1e8 else f"{v / 1e4:.1f}万" if v >= 1e4 else f"{v:,.0f}"


def compose(s: dict, hits: list[dict], public_url: str) -> tuple[str, str]:
    side = "做多" if s["side"] == "long" else "做空"
    subject = f"[AlphaScope 价位哨兵] {s['name']}：{len(hits)} 位交易员在 {s['coin']} {_fmt(s['px_min'])}–{_fmt(s['px_max'])} {side}"
    lines = [subject, ""]
    for h in hits[:EMAIL_LIMIT]:
        name = h["name"] or h["address"]
        link = f"{public_url.rstrip('/')}/zh/trader/{h['address']}" if public_url else h["address"]
        lines.append(f"· {name}  均价 {float(h['avg_px'] or 0):,.1f}  名义 US${_fmt(h['notional'])}  "
                     f"首次 {h['first_at']:%Y-%m-%d %H:%M} UTC  {link}")
    if len(hits) > EMAIL_LIMIT:
        lines.append(f"……另有 {len(hits) - EMAIL_LIMIT} 位，见网页「仓位提醒 → 价位哨兵」")
    lines += ["", "命中来自已归档的成交 / OKX 历史仓位，会比实际开仓晚一些。只是研究线索，不是交易建议。"]
    return subject, "\n".join(lines)


def run(sf: sessionmaker[Session], now: datetime | None = None, sender=send) -> dict:
    now = now or datetime.now(UTC)
    found = sent = failed = skipped = 0
    with sf() as session:
        sentinels = [dict(r) for r in session.execute(text("""
            SELECT * FROM zone_sentinel WHERE enabled AND owner = :o ORDER BY id"""), {"o": OWNER}).mappings().all()]
        for s in sentinels:
            found += find_hits(session, s, now)
        session.commit()
        smtp = settings_store.get(session, "smtp")
        password = settings_store.get_secret(session, "smtp")
        configured = bool(smtp.get("host") and smtp.get("username") and password)
        for s in sentinels:
            hits = [dict(r) for r in session.execute(text("""
                SELECT h.wallet_id, h.first_at, h.avg_px, h.notional, w.address,
                       coalesce(a.alias, w.display_name) AS name
                FROM sentinel_hit h JOIN wallet w ON w.id = h.wallet_id
                LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
                WHERE h.sentinel_id = :id AND h.notify_status = 'pending' ORDER BY h.notional DESC"""),
                {"id": s["id"], "o": OWNER}).mappings().all()]
            if not hits:
                continue
            recipients = list(s["recipients"] or []) or list(smtp.get("recipients") or [])
            status = "skipped"
            if configured and recipients:
                subject, body = compose(s, hits, smtp.get("public_url") or "")
                try:
                    sender(smtp, password, recipients, subject, body)
                    status = "sent"
                except Exception:  # noqa: BLE001
                    status = "failed"
            session.execute(text("""UPDATE sentinel_hit SET notify_status = :st
                WHERE sentinel_id = :id AND wallet_id = ANY(:w)"""),
                {"st": status, "id": s["id"], "w": [h["wallet_id"] for h in hits]})
            sent += status == "sent"
            failed += status == "failed"
            skipped += status == "skipped"
        session.commit()
    return {"sentinels": len(sentinels), "hits": found, "sent": sent, "failed": failed, "skipped": skipped}
