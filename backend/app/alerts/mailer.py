"""Alert emails: subscription filters, coalescing, quiet hours, rate limit, composition and SMTP delivery."""

import html
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KIND_ZH = {"open": "开仓", "increase": "加仓", "decrease": "减仓", "close": "平仓", "flip": "反手",
           "leverage": "调整杠杆", "liq_risk": "接近强平"}
SIZE_KINDS = {"open", "increase", "decrease", "close", "flip"}
MAX_HOLD_FACTOR = 5  # a burst of changes is sent at the latest after 5 x coalesce seconds


@dataclass
class Sub:
    events: list[str]
    min_notional: float
    min_change_pct: float
    coins: list[str]
    recipients: list[str]
    quiet_start: int | None
    quiet_end: int | None
    enabled: bool = True


@dataclass
class Ev:
    id: int
    coin: str
    kind: str
    t: datetime
    szi_before: Decimal
    szi_after: Decimal
    px: Decimal | None
    notional_change: Decimal | None
    details: dict


def passes(sub: Sub, e: Ev) -> bool:
    if not sub.enabled or e.kind not in sub.events:
        return False
    if sub.coins and e.coin not in sub.coins:
        return False
    if e.kind in SIZE_KINDS:
        if sub.min_notional and (e.notional_change is None or float(e.notional_change) < sub.min_notional):
            return False
        base = max(abs(e.szi_before), abs(e.szi_after))
        pct = float(abs(e.szi_after - e.szi_before) / base * 100) if base else 100.0
        if sub.min_change_pct and pct < sub.min_change_pct:
            return False
    return True


def in_quiet_hours(sub: Sub, now: datetime, tz: str) -> bool:
    if sub.quiet_start is None or sub.quiet_end is None or sub.quiet_start == sub.quiet_end:
        return False
    h = now.astimezone(ZoneInfo(tz)).hour
    if sub.quiet_start < sub.quiet_end:
        return sub.quiet_start <= h < sub.quiet_end
    return h >= sub.quiet_start or h < sub.quiet_end  # window across midnight


def ready(events: list[Ev], now: datetime, coalesce_seconds: int) -> bool:
    """Send once the wallet has been quiet for `coalesce_seconds`, or the burst is MAX_HOLD_FACTOR times older."""
    if not events:
        return False
    first, last = min(e.t for e in events), max(e.t for e in events)
    return now - last >= timedelta(seconds=coalesce_seconds) or \
        now - first >= timedelta(seconds=coalesce_seconds * MAX_HOLD_FACTOR)


def _fmt_usd(v: float | Decimal | None) -> str:
    if v is None:
        return "—"
    v = float(v)
    if abs(v) >= 10_000:
        return f"${v / 10_000:,.2f}万"
    return f"${v:,.0f}"


def _fmt_px(v: Decimal | float | None) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:,.2f}" if v >= 100 else f"{v:,.6g}"


def describe(e: Ev) -> str:
    side_after = "多" if e.szi_after > 0 else "空" if e.szi_after < 0 else ""
    side_before = "多" if e.szi_before > 0 else "空" if e.szi_before < 0 else ""
    if e.kind == "open":
        return f"开{side_after} {e.coin} {_fmt_usd(e.notional_change)} @ {_fmt_px(e.px)}"
    if e.kind == "close":
        return f"平{side_before} {e.coin} {_fmt_usd(e.notional_change)} @ {_fmt_px(e.px)}"
    if e.kind == "flip":
        return f"反手 {e.coin} {side_before}→{side_after} @ {_fmt_px(e.px)}"
    if e.kind in ("increase", "decrease"):
        return f"{KIND_ZH[e.kind]}{side_after} {e.coin} {_fmt_usd(e.notional_change)} @ {_fmt_px(e.px)}"
    if e.kind == "leverage":
        return f"{e.coin} 杠杆 {e.details.get('leverage_before')}× → {e.details.get('leverage_after')}×"
    return f"{e.coin} 接近强平（强平价 {_fmt_px(Decimal(e.details['liquidation_px']) if e.details.get('liquidation_px') else None)}）"


def compose(name: str, address: str, events: list[Ev], positions: list[dict], account_value: Decimal | None,
            public_url: str, tz: str) -> tuple[str, str, str]:
    """(subject, plain text, html)."""
    events = sorted(events, key=lambda e: e.t)
    head = describe(events[0])
    subject = f"【AlphaScope】{name} {head}" + (f"（另有 {len(events) - 1} 项变动）" if len(events) > 1 else "")
    zone = ZoneInfo(tz)
    link = f"{public_url.rstrip('/')}/zh/trader/{address}#replay" if public_url else ""
    lines = [f"{name}（{address}）仓位变动：", ""]
    rows = []
    for e in events:
        local = e.t.astimezone(zone).strftime("%m-%d %H:%M:%S")
        size = f"{_fmt_px(e.szi_before)} → {_fmt_px(e.szi_after)}"
        lines.append(f"- {local} {describe(e)}（仓位 {size}）")
        rows.append(f"<tr><td>{local}</td><td>{html.escape(KIND_ZH.get(e.kind, e.kind))}</td><td>{html.escape(e.coin)}</td>"
                    f"<td style='text-align:right'>{size}</td><td style='text-align:right'>{_fmt_usd(e.notional_change)}</td>"
                    f"<td style='text-align:right'>{_fmt_px(e.px)}</td></tr>")
    lines += ["", f"账户净值：{_fmt_usd(account_value)}", "当前持仓："]
    prow = []
    for p in positions:
        side = "多" if p["szi"] > 0 else "空"
        lines.append(f"  {side} {p['coin']} {_fmt_usd(p.get('position_value'))} 开仓 {_fmt_px(p.get('entry_px'))} "
                     f"杠杆 {p.get('leverage') or '—'}× 浮盈 {_fmt_usd(p.get('unrealized_pnl'))}")
        prow.append(f"<tr><td>{side}</td><td>{html.escape(p['coin'])}</td>"
                    f"<td style='text-align:right'>{_fmt_usd(p.get('position_value'))}</td>"
                    f"<td style='text-align:right'>{_fmt_px(p.get('entry_px'))}</td>"
                    f"<td style='text-align:right'>{p.get('leverage') or '—'}×</td>"
                    f"<td style='text-align:right'>{_fmt_usd(p.get('unrealized_pnl'))}</td></tr>")
    if not positions:
        lines.append("  （无持仓）")
    if link:
        lines += ["", f"查看详情与 K 线回放：{link}"]
    lines += ["", f"检测方式：每隔几十秒轮询一次持仓，两次轮询之间开平的仓位看不到。时间为 {tz}。"]
    td = "padding:4px 8px;border-bottom:1px solid #eee"
    style = f"<style>td,th{{{td};font-size:13px}} th{{text-align:left;color:#666}}</style>"
    body = (f"<html><head>{style}</head><body style='font-family:-apple-system,Segoe UI,PingFang SC,sans-serif'>"
            f"<p><b>{html.escape(name)}</b> <span style='color:#888'>{address}</span></p>"
            f"<table cellspacing=0><tr><th>时间</th><th>动作</th><th>币种</th><th>仓位</th><th>变动金额</th><th>价格</th></tr>"
            f"{''.join(rows)}</table>"
            f"<p>账户净值：{_fmt_usd(account_value)}</p>"
            f"<table cellspacing=0><tr><th>方向</th><th>币种</th><th>名义价值</th><th>开仓价</th><th>杠杆</th><th>浮盈</th></tr>"
            f"{''.join(prow) or '<tr><td colspan=6>无持仓</td></tr>'}</table>"
            + (f"<p><a href='{html.escape(link)}'>查看详情与 K 线回放</a></p>" if link else "")
            + f"<p style='color:#999;font-size:12px'>每隔几十秒轮询一次持仓，两次轮询之间开平的仓位看不到。时间为 {tz}。</p>"
            "</body></html>")
    return subject, "\n".join(lines), body


def send(cfg: dict, password: str | None, recipients: list[str], subject: str, text_body: str,
         html_body: str | None = None, timeout: float = 20.0) -> None:
    """Send one email; raises on failure. cfg: host, port, security (starttls | ssl | none), username, from_name."""
    if not cfg.get("host") or not cfg.get("username"):
        raise ValueError("SMTP is not configured")
    if not recipients:
        raise ValueError("no recipients")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("from_name") or "AlphaScope", cfg["username"]))
    msg["To"] = ", ".join(recipients)
    msg["Message-ID"] = make_msgid(domain="alphascope.local")
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    port = int(cfg.get("port") or 587)
    if cfg.get("security") == "ssl":
        with smtplib.SMTP_SSL(cfg["host"], port, timeout=timeout, context=context) as s:
            if password:
                s.login(cfg["username"], password)
            s.send_message(msg)
        return
    with smtplib.SMTP(cfg["host"], port, timeout=timeout) as s:
        s.ehlo()
        if cfg.get("security", "starttls") == "starttls":
            s.starttls(context=context)
            s.ehlo()
        if password:
            s.login(cfg["username"], password)
        s.send_message(msg)
