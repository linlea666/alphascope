"""Cards v2: dark trading-terminal images like the screenshots that travel on crypto X. Every figure on them is a
display string of a packet fact; curves are drawn from packet series. A card that its packet cannot fill is refused."""

from datetime import datetime
from decimal import Decimal

TERMINAL = {"bg": "#0b0e11", "panel": "#161a1e", "line": "#2b3139", "text": "#eaecef", "muted": "#848e9c",
            "up": "#2ebd85", "down": "#f6465d", "accent": "#f0b90b"}
SIDE = {"zh": {"long": "多", "short": "空"}, "en": {"long": "Long", "short": "Short"}}
L = {
    "zh": {"notional": "仓位价值", "leverage": "杠杆", "upl": "浮动盈亏", "account": "账户价值", "liq_dist": "距强平",
           "coin": "币种", "side": "方向", "size": "数量", "entry": "开仓均价", "mark": "标记价", "liq": "强平价",
           "curve": "累计盈亏（排行榜口径，含浮盈）", "path": "仓位变化（轮询观测）", "record": "交易员战绩",
           "alltime": "累计盈亏", "month": "近30天盈亏", "cycles": "该币完整周期", "wins": "盈利周期",
           "last": "上一笔同方向", "now": "当前仓位", "crowd": "多空对决", "tracked": "我们追踪的地址",
           "longs": "多头", "shorts": "空头", "traders": "人", "timers": "择时高手", "rank": "这笔在同方向排第",
           "source": "数据：Hyperliquid 链上公开数据 · AlphaScope 追踪", "book": "整盘持仓", "total": "合计浮盈亏",
           "value": "仓位价值", "token": "币种"},
    "en": {"notional": "Position value", "leverage": "Leverage", "upl": "Unrealized P&L", "account": "Account value",
           "liq_dist": "To liquidation", "coin": "Coin", "side": "Side", "size": "Size", "entry": "Avg entry",
           "mark": "Mark", "liq": "Liq. price", "curve": "All-time P&L (leaderboard, incl. unrealized)",
           "path": "Position size (polled)", "record": "Trader record", "alltime": "All-time P&L",
           "month": "30-day P&L", "cycles": "Complete cycles", "wins": "Winning", "last": "Last trade this way",
           "now": "Current position", "crowd": "Longs vs shorts", "tracked": "wallets we track", "longs": "Longs",
           "shorts": "Shorts", "traders": "wallets", "timers": "Proven timers", "rank": "This one ranks #",
           "source": "Data: Hyperliquid on-chain · tracked by AlphaScope", "book": "Open positions",
           "total": "Total unrealized P&L", "value": "Value", "token": "Token"},
}
V2_REQUIRES = {"dashboard_card": "position.notional_usd", "record_card": "trader.alltime_pnl_usd",
               "crowd_card": "crowd.same_side_traders", "book_card": "book.pos2.notional_usd"}


def _facts(packet: dict) -> dict:
    return {f["id"]: f for f in packet["facts"]}


def _d(f: dict, fid: str, lang: str) -> str | None:
    return f[fid]["display"][lang][0] if fid in f else None


def _short(f: dict, fid: str, lang: str) -> str | None:
    """A price without its unit (98,500) for a tight table cell."""
    if fid not in f:
        return None
    ds = f[fid]["display"][lang]
    return next((d for d in ds if "美元" not in d and "万" not in d), ds[0])


def _signed(f: dict, fid: str, lang: str) -> str | None:
    d, v = _d(f, fid, lang), _num(f, fid)
    return f"+{d}" if d and v is not None and v > 0 and not d.startswith("+") else d


def _num(f: dict, fid: str) -> float | None:
    try:
        return float(Decimal(str(f[fid]["value"]))) if fid in f else None
    except ArithmeticError:
        return None


def _curve(points: list[tuple[float, float]], x0: float, y0: float, x1: float, y1: float,
           signed: bool = False) -> dict | None:
    """Polyline and filled area in the box (x0, y0)-(x1, y1); y grows downwards in SVG. A P&L curve (signed) is green
    or red by where it ends; a size path by whether it grew."""
    if len(points) < 2:
        return None
    ts, vs = [p[0] for p in points], [p[1] for p in points]
    lo, hi = min(min(vs), 0.0), max(max(vs), 0.0)
    span_t, span_v = (ts[-1] - ts[0]) or 1, (hi - lo) or 1
    xy = [(x0 + (x1 - x0) * (t - ts[0]) / span_t, y1 - (y1 - y0) * (v - lo) / span_v) for t, v in points]
    zero = y1 - (y1 - y0) * (0 - lo) / span_v
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    return {"line": line, "area": f"{xy[0][0]:.1f},{zero:.1f} {line} {xy[-1][0]:.1f},{zero:.1f}",
            "zero": round(zero, 1), "up": vs[-1] >= 0 if signed else vs[-1] >= vs[0]}


def _pnl_curve(f: dict, x0, y0, x1, y1) -> dict | None:
    s = f.get("trader.pnl_series")
    if not s:
        return None
    return _curve([(datetime.fromisoformat(p["t"]).timestamp(), float(p["pnl"])) for p in s["value"]], x0, y0, x1, y1,
                  signed=True)


def _size_curve(f: dict, x0, y0, x1, y1) -> dict | None:
    s = f.get("episode.path")
    if not s:
        return None
    return _curve([(datetime.fromisoformat(p["t"]).timestamp(), float(p["size"])) for p in s["value"]], x0, y0, x1, y1)


def _tone(f: dict, fid: str) -> str:
    v = _num(f, fid)
    return "up" if v is None or v >= 0 else "down"


def _base(packet: dict, lang: str, brand: str, title: str) -> dict:
    ev = packet["event"]
    f = _facts(packet)
    when = _d(f, "change.recency", lang) or ""
    return {"c": TERMINAL, "brand": brand, "title": title, "subject": packet["subject"]["trader_ref"],
            "coin": ev["coin"], "footer": L[lang]["source"] + (f" · {when}" if when else "")}


def dashboard_inputs(packet: dict, lang: str, brand: str, font: str) -> dict:
    f, t, ev = _facts(packet), L[lang], packet["event"]
    side = SIDE[lang].get(ev["direction"], "")
    lev = _d(f, "position.leverage", lang)
    tiles = [(t["notional"], _d(f, "position.notional_usd", lang), "text"),
             (t["leverage"], lev, "text"),
             (t["upl"], _signed(f, "position.unrealized_pnl_usd", lang), _tone(f, "position.unrealized_pnl_usd")),
             (t["account"], _d(f, "trader.account_value_usd", lang), "text") if "trader.account_value_usd" in f
             else (t["liq_dist"], _d(f, "position.liq_distance", lang), "text")]
    row = [(t["coin"], ev["coin"], "text"), (t["side"], f"{side} {lev or ''}".strip(),
                                             "up" if ev["direction"] == "long" else "down"),
           (t["size"], _d(f, "position.size", lang), "text"), (t["entry"], _short(f, "position.entry_px", lang), "text"),
           (t["mark"], _short(f, "position.mark_px", lang), "text"), (t["liq"], _short(f, "position.liq_px", lang), "accent"),
           (t["upl"], _signed(f, "position.unrealized_pnl_usd", lang), _tone(f, "position.unrealized_pnl_usd"))]
    curve, label = _pnl_curve(f, 70, 448, 1130, 590), t["curve"]
    last = _signed(f, "trader.alltime_pnl_usd", lang)
    if curve is None:
        curve, label, last = _size_curve(f, 70, 448, 1130, 590), t["path"], None
    title = f"{ev['coin']} · {side}" if lang == "zh" else f"{ev['coin']} {side}"
    return {**_base(packet, lang, brand, title), "file": "dashboard", "font": font,
            "tiles": [{"label": a, "value": b or "—", "tone": c} for a, b, c in tiles],
            "row": [{"label": a, "value": b or "—", "tone": c} for a, b, c in row],
            "curve": curve, "curve_label": label, "curve_last": last}


def record_inputs(packet: dict, lang: str, brand: str, font: str) -> dict:
    f, t = _facts(packet), L[lang]
    stats = [(t["month"], _signed(f, "trader.month_pnl_usd", lang), _tone(f, "trader.month_pnl_usd"))]
    if "contrast.coin_cycles" in f:
        stats.append((f"{packet['event']['coin']} {t['cycles']}", f"{_d(f, 'contrast.coin_winning_cycles', lang)} / "
                      f"{_d(f, 'contrast.coin_cycles', lang)} {t['wins']}", "text"))
    if "contrast.last_same_side_net_usd" in f:
        stats.append((t["last"], _signed(f, "contrast.last_same_side_net_usd", lang), _tone(f, "contrast.last_same_side_net_usd")))
    if "position.notional_usd" in f:
        side = SIDE[lang].get(packet["event"]["direction"], "")
        stats.append((t["now"], f"{packet['event']['coin']} {side} {_d(f, 'position.notional_usd', lang)}", "text"))
    return {**_base(packet, lang, brand, t["record"]), "file": "record", "font": font,
            "big": _signed(f, "trader.alltime_pnl_usd", lang), "big_tone": _tone(f, "trader.alltime_pnl_usd"),
            "big_label": t["alltime"], "curve": _pnl_curve(f, 70, 300, 1130, 520), "curve_label": t["curve"],
            "stats": [{"label": a, "value": b or "—", "tone": c} for a, b, c in stats[:4]]}


def crowd_inputs(packet: dict, lang: str, brand: str, font: str) -> dict:
    f, t, ev = _facts(packet), L[lang], packet["event"]
    same_is_long = ev["direction"] == "long"
    longs = ("crowd.same_side" if same_is_long else "crowd.opposite_side")
    shorts = ("crowd.opposite_side" if same_is_long else "crowd.same_side")
    vals = [_num(f, f"{p}_notional_usd") or 0 for p in (longs, shorts)]
    top = max(vals) or 1
    bars = [{"label": t["longs"], "value": _d(f, f"{longs}_notional_usd", lang) or "—",
             "count": _d(f, f"{longs}_traders", lang) or "0", "w": round(760 * vals[0] / top), "tone": "up"},
            {"label": t["shorts"], "value": _d(f, f"{shorts}_notional_usd", lang) or "—",
             "count": _d(f, f"{shorts}_traders", lang) or "0", "w": round(760 * vals[1] / top), "tone": "down"}]
    timers = None
    if "crowd.timing_same_side" in f:
        tl = "crowd.timing_same_side" if same_is_long else "crowd.timing_opposite_side"
        ts = "crowd.timing_opposite_side" if same_is_long else "crowd.timing_same_side"
        a, b = _num(f, tl) or 0, _num(f, ts) or 0
        timers = {"long": int(a), "short": int(b), "w_long": round(760 * a / ((a + b) or 1))}
    rank = _num(f, "crowd.same_side_rank")
    side = SIDE[lang].get(ev["direction"], "")
    highlight = (f"{packet['subject']['trader_ref']} · {side} {_d(f, 'position.notional_usd', lang) or ''} · "
                 f"{t['rank']}{int(rank)}" if rank else None)
    sample = _d(f, "crowd.sample_traders", lang)
    return {**_base(packet, lang, brand, f"{ev['coin']} {t['crowd']}"), "file": "crowd", "font": font,
            "subtitle": "" if not sample else f"{sample} {t['tracked']}" if lang == "en" else f"{t['tracked']}（{sample}个）",
            "bars": bars, "timers": timers, "timers_label": t["timers"], "traders": t["traders"],
            "highlight": highlight}


def book_inputs(packet: dict, lang: str, brand: str, font: str) -> dict:
    f, t = _facts(packet), L[lang]
    rows = []
    for i in range(1, 6):
        if f"book.pos{i}.notional_usd" not in f:
            break
        side = f[f"book.pos{i}.side"]["value"]
        lev = _d(f, f"book.pos{i}.leverage", lang)
        rows.append({"coin": _d(f, f"book.pos{i}.coin", lang), "side": f"{SIDE[lang][side]} {lev or ''}".strip(),
                     "side_tone": "up" if side == "long" else "down",
                     "value": _d(f, f"book.pos{i}.notional_usd", lang),
                     "entry": _short(f, f"book.pos{i}.entry_px", lang) or "—",
                     "mark": _short(f, f"book.pos{i}.mark_px", lang) or "—",
                     "pnl": _signed(f, f"book.pos{i}.upl_usd", lang) or "—", "pnl_tone": _tone(f, f"book.pos{i}.upl_usd")})
    return {**_base(packet, lang, brand, t["book"]), "file": "book", "font": font, "rows": rows,
            "head": [t["token"], t["side"], t["value"], t["entry"], t["mark"], t["upl"]],
            "total_label": t["total"], "total": _signed(f, "book.total_upl_usd", lang),
            "total_tone": _tone(f, "book.total_upl_usd")}


V2_BUILDERS = {"dashboard_card": dashboard_inputs, "record_card": record_inputs, "crowd_card": crowd_inputs,
               "book_card": book_inputs}


def pick_card(packet: dict, hook_keys: list[str]) -> str | None:
    """The card that shows what the post leads with; None when the packet cannot fill one."""
    facts = _facts(packet)
    order = (["record_card"] if hook_keys[:1] in (["record"], ["last_time"]) else
             ["crowd_card"] if hook_keys[:1] in (["against_timers"], ["top_of_sample"]) else
             ["book_card"] if hook_keys[:1] in (["book_in_profit"], ["opened_many_today"], ["hedge"]) else [])
    for name in order + ["dashboard_card", "record_card", "crowd_card", "book_card"]:
        if V2_REQUIRES[name] in facts:
            return name
    return None
