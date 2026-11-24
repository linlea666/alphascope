"""Square cards (docs/16): portrait 1080x1350 images for OKX and Binance Square, built around the trader. Every figure
is a fact's display string; names and avatars come from the packet; nothing is computed here but which card to draw
and what colour a number is."""

from nr.cards.avatars import data_uri, drawable
from nr.cards.v2 import _d, _facts, _num, _signed

THEME = {"bg": "#0d1117", "bg2": "#1a1f2b", "panel": "#161b22", "line": "#2d333b", "text": "#f0f3f6",
         "muted": "#8b949e", "up": "#2ebd85", "down": "#f6465d", "accent": "#f5c451", "chip": "#262c36"}
VENUE = {"okx": "OKX 广场", "binance": "币安广场"}
SOURCE = {"okx": "数据：OKX 官方榜单与带单记录 · AlphaScope 整理", "binance": "数据：币安带单公开数据 · AlphaScope 追踪"}
VERB = {"whale_open": "开", "flip": "反手", "whale_add": "加仓", "underwater_add": "浮亏加仓", "whale_close": "平掉",
        "liquidation": "爆仓", "near_liquidation": "逼近强平", "notable_trader_move": "动了"}
SIDE = {"long": "多单", "short": "空单"}
SQUARE_REQUIRES = {"square_podium": "row.1.multiple", "square_persona": "trader", "square_star": "change.notional_usd",
                   "square_duel": "a.trader"}


TITLE = {"whale_open": "开仓快报", "flip": "反手快报", "whale_add": "加仓动态", "underwater_add": "浮亏加仓",
         "whale_close": "平仓战报", "liquidation": "爆仓", "near_liquidation": "强平警报", "notable_trader_move": "明星动态"}


def fit(text: str | None, width: float, largest: int, smallest: int = 22) -> int:
    """A font size at which the text fits the width: CJK characters are about 1 em wide, the rest about 0.6 em."""
    units = sum(1.0 if ord(ch) > 0x2E80 else 0.6 for ch in (text or "")) or 1
    return max(smallest, min(largest, int(width / units)))


def _who(f: dict, fid: str, fallback: str, avatars: bool) -> dict:
    x = f.get(fid) or {}
    name = drawable(x.get("value") if isinstance(x.get("value"), str) else fallback)
    return {"name": name, "initial": (name or "?")[:1], "avatar": data_uri(x.get("avatar_url")) if avatars else None}


def _tone(f: dict, fid: str) -> str:
    v = _num(f, fid)
    return "text" if v is None else "up" if v > 0 else "down" if v < 0 else "text"


def _base(packet: dict, title: str, subtitle: str = "") -> dict:
    venue = packet["subject"].get("venue") or "okx"
    f = _facts(packet)
    return {"c": THEME, "file": "square", "w": 1080, "h": 1350, "venue": VENUE.get(venue, ""), "title": title,
            "subtitle": subtitle, "footer": SOURCE.get(venue, ""), "date": _d(f, "board.date", "zh") or ""}


def podium_inputs(packet: dict, lang: str, brand: str, font: str, avatars: bool = True) -> dict:
    f = _facts(packet)
    people = []
    for i in (1, 2, 3):
        p = f"row.{i}"
        if f"{p}.multiple" not in f:
            break
        move = ("新上榜" if f"{p}.new_entry" in f else f"↑{_d(f, f'{p}.places_up', 'zh')}" if f"{p}.places_up" in f
                else f"↓{_d(f, f'{p}.places_down', 'zh')}" if f"{p}.places_down" in f else "")
        assets = _num(f, f"{p}.lead_assets")
        who = _who(f, f"{p}.trader", "", avatars)
        people.append({**who, "name_size": fit(who["name"], 260 if i > 1 else 900, 32 if i > 1 else 52),
                       "big": _d(f, f"{p}.multiple", "zh"),
                       "rank": _d(f, f"{p}.rank", "zh"), "move": move, "move_tone": "up" if move.startswith(("↑", "新"))
                       else "down" if move.startswith("↓") else "muted",
                       "pnl": _d(f, f"{p}.pnl", "zh"), "assets": _d(f, f"{p}.lead_assets", "zh"),
                       "small": assets is not None and assets < 10_000})
    title = (packet["subject"].get("title") or {}).get("zh") or "翻倍榜"
    return {**_base(packet, title, _d(f, "selection", "zh") or ""), "kind": "podium", "font": font, "brand": brand,
            "people": people}


def persona_inputs(packet: dict, lang: str, brand: str, font: str, avatars: bool = True) -> dict:
    f = _facts(packet)
    who = _who(f, "trader", packet["subject"].get("trader_ref") or "", avatars)
    tiles = []
    for fid, label in (("peak.multiple", "巅峰时"), ("now.off_peak", "离高点"), ("traits.multiple", "倍数"),
                       ("traits.off_peak", "离高点"), ("traits.roi_30d", "30 天收益率"), ("traits.win_rate", "胜率"),
                       ("traits.max_lever", "最高杠杆"), ("traits.win_streak", "连胜"), ("traits.loss_streak", "连败"),
                       ("traits.pnl_7d", "7 天盈亏"), ("peak.rank", "最高排名"), ("now.rank", "现在排名"),
                       ("result.realized_pnl", "结果")):
        if fid in f and len(tiles) < 6:
            tone = "down" if fid in ("now.off_peak", "traits.off_peak", "traits.loss_streak") else \
                _tone(f, fid) if fid.endswith(("pnl", "pnl_7d")) else "text"
            value = _signed(f, fid, "zh") if fid.endswith(("pnl", "pnl_7d")) else _d(f, fid, "zh")
            tiles.append({"label": label, "value": value, "tone": tone, "size": fit(value, 250, 50)})
    stage = _d(f, "stage.now", "zh")
    before = _d(f, "stage.before", "zh")
    badge = stage or _d(f, "now.status", "zh") or _d(f, "answer", "zh") or ""
    title = (packet["subject"].get("title") or {}).get("zh") or (f"{before} → {stage}" if stage and before else badge)
    tags = (f.get("traits.tags") or {}).get("value") or []
    return {**_base(packet, title, _d(f, "question", "zh") or ""), "kind": "persona", "font": font, "brand": brand,
            "who": who, "name_size": fit(who["name"], 900, 56), "badge": badge, "badge_tone": "down" if badge in ("陨落", "危机", "已掉出榜单", "从高点大幅回撤", "亏了", "爆仓了")
            else "accent", "tags": tags[:4], "tiles": tiles}


def star_inputs(packet: dict, lang: str, brand: str, font: str, avatars: bool = True) -> dict:
    f, ev = _facts(packet), packet["event"]
    who = {"name": drawable(packet["subject"].get("name") or packet["subject"]["trader_ref"]),
           "initial": drawable(packet["subject"].get("name") or "?")[:1],
           "avatar": data_uri(packet["subject"].get("avatar_url")) if avatars else None}
    side = SIDE.get(ev.get("direction") or "", "")
    verb = VERB.get(ev["class"], "")
    size = _d(f, "position.notional_usd", "zh") or _d(f, "change.notional_usd", "zh")
    action = f"{verb} ${ev['coin']} {side}" if ev["class"] in ("whale_open", "flip", "whale_close") else f"{verb} ${ev['coin']}"
    big, big_tone, big_label = size, "text", "仓位"
    if "close.realized_pnl_usd" in f:
        big, big_tone = _signed(f, "close.realized_pnl_usd", "zh"), _tone(f, "close.realized_pnl_usd")
        big_label = "这一单" + ("进账" if big_tone == "up" else "亏损" if big_tone == "down" else "结果")
    tiles = []
    for fid, label in (("position.leverage", "杠杆"), ("close.held", "拿了"), ("position.unrealized_pnl_usd", "浮盈亏"),
                       ("history.binance_pnl_24h_usd", "24 小时盈亏"), ("history.binance_win_streak", "连胜"),
                       ("history.binance_loss_streak", "连败"), ("trader.binance_roi_30d", "30 天收益率"),
                       ("trader.binance_win_rate_30d", "30 天胜率"), ("contrast.last_coin_close_pnl_usd", "上一笔这个币")):
        if fid in f and len(tiles) < 6:
            pnl = fid.endswith("_usd") and "pnl" in fid
            value = _signed(f, fid, "zh") if pnl else _d(f, fid, "zh")
            tiles.append({"label": label, "value": value, "size": fit(value, 250, 50),
                          "tone": _tone(f, fid) if pnl else "down" if fid.endswith("loss_streak") else "text"})
    return {**_base(packet, TITLE.get(ev["class"], "明星动态")), "kind": "star", "font": font, "brand": brand, "who": who,
            "action": action, "size": size, "big": big, "big_tone": big_tone, "big_label": big_label,
            "big_size": fit(big, 880, 128), "name_size": fit(who["name"], 640, 56), "tiles": tiles}


def duel_inputs(packet: dict, lang: str, brand: str, font: str, avatars: bool = True) -> dict:
    f = _facts(packet)
    leader = _d(f, "duel.leader", "zh")
    sides = []
    for p in ("a", "b"):
        who = _who(f, f"{p}.trader", "", avatars)
        result = f"{p}.realized_pnl" if f"{p}.realized_pnl" in f else f"{p}.upl" if f"{p}.upl" in f else None
        sides.append({**who, "side": _d(f, f"{p}.side", "zh"), "side_tone": "up" if _d(f, f"{p}.side", "zh") == "多单" else "down",
                      "notional": _d(f, f"{p}.notional", "zh"), "leverage": _d(f, f"{p}.leverage", "zh"),
                      "result": _signed(f, result, "zh") if result else _d(f, f"{p}.status", "zh"),
                      "result_label": "已平仓" if result and result.endswith("realized_pnl") else "浮盈亏" if result else "",
                      "result_tone": _tone(f, result) if result else "muted", "lead": leader is not None and who["name"] == drawable(leader),
                      "name_size": fit(who["name"], 400, 40), "result_size": fit(_signed(f, result, "zh") if result else "", 400, 60)})
    coin = _d(f, "coin", "zh") or packet["event"]["coin"]
    title = ("对决结果" if "a.status" in f else "明星对决") + f"：${coin}"
    return {**_base(packet, title), "kind": "duel", "font": font, "brand": brand, "coin": coin, "sides": sides}


SQUARE_BUILDERS = {"square_podium": podium_inputs, "square_persona": persona_inputs, "square_star": star_inputs,
                   "square_duel": duel_inputs}


def square_card(packet: dict) -> str | None:
    """The square card for this packet; None when it has nothing to draw."""
    cls = packet["event"]["class"]
    col = cls.removeprefix("column:") if cls.startswith("column:") else None
    name = ("square_podium" if col in ("okx_multipliers", "okx_dark_horses") else
            "square_persona" if col in ("story_turn", "okx_fallen", "prediction_followup") else
            "square_duel" if col in ("duel", "duel_result") else
            None if col else "square_star")
    facts = {x["id"] for x in packet["facts"]}
    return name if name and SQUARE_REQUIRES[name] in facts else None
