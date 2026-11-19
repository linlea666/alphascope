"""Daily intelligence digest: what watched traders did, where significant traders and the crowd disagree, who
newly became timing-significant, and sentinel hits. Stored per day (the overview shows the latest) and emailed when
enabled in settings."""

import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.alerts.mailer import send
from app.market import divergence
from app.config import get_settings
from fastapi.encoders import jsonable_encoder
from app.research.frames import notice_rows, daily_review
from app.traders import trader_label

OWNER = "default"


def build(session: Session, now: datetime) -> dict:
    since = now - timedelta(hours=24)
    moves = session.execute(text("""
        SELECT w.address, coalesce(a.alias, w.display_name) AS name, w.venue, e.coin, e.kind, e.side,
               e.notional_change, e.entry_px, e.event_time
        FROM market_event e JOIN wallet w ON w.id = e.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        WHERE e.wallet_id IN (SELECT wallet_id FROM watch_item) AND e.event_time >= :s AND e.kind <> 'first_seen'
        ORDER BY e.notional_change DESC LIMIT 15"""), {"o": OWNER, "s": since}).mappings().all()
    significant = {wid: (scale, q) for wid, scale, q in session.execute(text("""
        SELECT DISTINCT ON (wallet_id) wallet_id, scale, q_value FROM timing_score
        WHERE status = 'scored' AND q_value < 0.1 AND coin_group = 'all' ORDER BY wallet_id, q_value""")).all()}
    prev = session.execute(text("""
        SELECT payload FROM daily_digest WHERE day < :d ORDER BY day DESC LIMIT 1"""), {"d": now.date()}).scalar()
    new_ids = [w for w in significant if prev is not None and w not in set(prev.get("significant_ids", []))]
    new_rows = []
    if new_ids:
        new_rows = [{**dict(r), "scale": significant[r["id"]][0], "q": float(significant[r["id"]][1])}
                    for r in session.execute(text("""
                        SELECT w.id, w.address, coalesce(a.alias, w.display_name) AS display_name, w.venue FROM wallet w
                        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o WHERE w.id = ANY(:ids)"""),
                        {"ids": new_ids, "o": OWNER}).mappings().all()]
        new_rows.sort(key=lambda r: r["q"])
        for r in new_rows:
            r.pop("id")
    hits = session.execute(text("""
        SELECT s.name, s.coin, s.side, w.address, coalesce(a.alias, w.display_name) AS name_w, h.avg_px, h.notional
        FROM sentinel_hit h JOIN zone_sentinel s ON s.id = h.sentinel_id JOIN wallet w ON w.id = h.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        WHERE h.found_at >= :s ORDER BY h.notional DESC LIMIT 10"""), {"o": OWNER, "s": since}).mappings().all()
    health = session.execute(text("""
        SELECT w.address, coalesce(a.alias, w.display_name) AS name, w.venue, h.status, h.prev_status, h.signals
        FROM trader_health h JOIN wallet w ON w.id = h.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        WHERE h.changed_at >= :s AND h.prev_status IS NOT NULL AND h.status NOT IN ('insufficient')
        ORDER BY (h.wallet_id IN (SELECT wallet_id FROM watch_item)) DESC, h.changed_at DESC LIMIT 10"""),
        {"o": OWNER, "s": since}).mappings().all()
    num = lambda v: None if v is None else float(v)  # noqa: E731
    return {
        "market_review":daily_review(session,get_settings().research_coins,now),
        "major_markets": {coin: jsonable_encoder(notice_rows(session,coin,now,since)) for coin in get_settings().research_coins},
        "generated_at": now.isoformat(), "since": since.isoformat(),
        "watch_moves": [{**dict(m), "notional_change": num(m["notional_change"]), "entry_px": num(m["entry_px"]),
                         "event_time": m["event_time"].isoformat()} for m in moves],
        "divergence": divergence.current(session, "all", now)[:5],
        "new_significant": new_rows[:10], "new_significant_total": len(new_rows),
        "baseline": prev is None,  # first digest: nothing to compare "new" against
        "significant_total": len(significant), "significant_ids": sorted(significant),
        "sentinel_hits": [{**dict(h), "avg_px": num(h["avg_px"]), "notional": num(h["notional"])} for h in hits],
        "health_changes": [dict(h) for h in health],
    }


def _usd(v: float | None) -> str:
    v = abs(v or 0)
    return f"{v / 1e8:.2f}亿" if v >= 1e8 else f"{v / 1e4:.1f}万" if v >= 1e4 else f"{v:,.0f}"


def compose(day: date, p: dict, public_url: str) -> tuple[str, str]:
    kinds = {"open": "开仓", "increase": "加仓", "decrease": "减仓", "close": "平仓", "flip": "反手"}
    lines = [f"AlphaScope 每日情报 · {day}", ""]
    lines.append("【收藏的人昨天做了什么】")
    for m in p["watch_moves"][:10] or []:
        lines.append(f"· {trader_label(m['name'], m['address'])} {kinds.get(m['kind'], m['kind'])} {m['coin']} "
                     f"{'多' if m['side'] == 'long' else '空'} US${_usd(m['notional_change'])}")
    if not p["watch_moves"]:
        lines.append("· 没有观测到变动")
    lines += ["", "【聪明钱 vs 大众（做多人数占比）】"]
    for d in p["divergence"][:3]:
        lines.append(f"· {d['coin']}：节奏显著组 {d['smart_long_share']:.0%}（{d['smart_traders']} 人） vs "
                     f"全体 {d['crowd_long_share']:.0%}，差 {d['divergence']:+.0%}")
    lines += ["", f"【新进入节奏显著名单】{p['new_significant_total']} 人"]
    for r in p["new_significant"][:5]:
        lines.append(f"· {trader_label(r['display_name'], r['address'])} · {r['scale']} q={r['q']:.3f}")
    if p.get("health_changes"):
        names = {"declining": "走下坡", "dormant": "沉寂", "drifting": "风格漂移", "hot": "近期火热", "stable": "稳定"}
        lines += ["", "【交易员状态变化】"]
        for h in p["health_changes"][:5]:
            lines.append(f"· {trader_label(h['name'], h['address'])}：{names.get(h['prev_status'], h['prev_status'])} → "
                         f"{names.get(h['status'], h['status'])}")
    if p["sentinel_hits"]:
        lines += ["", "【价位哨兵命中】"]
        for h in p["sentinel_hits"][:5]:
            lines.append(f"· {h['name']}：{trader_label(h['name_w'], h['address'])} 均价 {h['avg_px'] or 0:,.1f}")
    for coin, notices in p.get("major_markets", {}).items():
        lines += ["", f"【{coin} · 市场行为】"]
        for n in notices[:3]:
            e = n["evidence"]
            lines.append(f"· #{n['id']} {n['venue']} {n['kind']} · 历史分位 {e['rank']:.0%} · {e['n']} 个账户")
        if not notices:
            lines.append("· 本期没有已登记异动")
    review=p.get("market_review",{})
    for coin, data in review.get("markets",{}).items():
        for venue, frame in data["venues"].items():
            d=frame["change"] or {}
            if d.get("net_units") is not None:
                lines.append(f"· {coin} {venue} 24h 共持仓 {d['common_n']} 个账户，净变化 {d['net_units']:+,.2f} {coin}；证据 #{frame['frame']}")
        for score in data["outcomes"]:
            lines.append(f"· {coin} {score['horizon']}h 到期 {score['n']} 条，Brier {score['brier']:.3f}，基准 {score['baseline']:.3f}")
    verdict={"opposite":"方向相反","aligned":"同向","flat":"至少一侧净变化为零","insufficient":"等待两个币种的 24h 共同持仓样本"}
    for venue,v in review.get("comparison",{}).items():
        lines.append(f"· {venue} BTC / ETH 固定持仓账户方向：{verdict[v]}")
    if public_url:
        lines += ["", f"网页：{public_url.rstrip('/')}/zh"]
    lines += ["", "只来自我们观测的样本；历史关系不代表未来，不构成交易建议。"]
    return f"[AlphaScope] 每日情报 {day}", "\n".join(lines)


def run(sf: sessionmaker[Session], now: datetime | None = None, sender=send, email: bool = True) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        payload = build(s, now)
        s.execute(text("""
            INSERT INTO daily_digest (day, payload) VALUES (:d, CAST(:p AS jsonb))
            ON CONFLICT (day) DO UPDATE SET payload = EXCLUDED.payload, created_at = now()"""),
            {"d": now.date(), "p": json.dumps(payload, default=str)})
        cfg = settings_store.get(s, "digest")
        smtp = settings_store.get(s, "smtp")
        password = settings_store.get_secret(s, "smtp")
        status = "disabled"
        if email and cfg.get("enabled"):
            recipients = list(cfg.get("recipients") or []) or list(smtp.get("recipients") or [])
            if smtp.get("host") and smtp.get("username") and password and recipients:
                subject, body = compose(now.date(), payload, smtp.get("public_url") or "")
                try:
                    sender(smtp, password, recipients, subject, body)
                    s.execute(text("UPDATE daily_digest SET emailed_at = :t WHERE day = :d"), {"t": now, "d": now.date()})
                    status = "sent"
                except Exception:  # noqa: BLE001
                    status = "failed"
            else:
                status = "not_configured"
        s.commit()
    return {"day": str(now.date()), "email": status, "watch_moves": len(payload["watch_moves"]),
            "new_significant": payload["new_significant_total"]}
