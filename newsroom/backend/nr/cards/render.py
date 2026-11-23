"""Data cards: deterministic SVG from fact-packet display strings only, rendered to PNG with CairoSVG. Every asset
records the template version, the packet it came from and a hash of its inputs. Cards never imitate an exchange's
interface: they carry our brand and a source line."""

import functools
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import cairosvg
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr.config import get_settings
from nr.facts import fmt

TEMPLATE_VERSION = "v1"
ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                  autoescape=select_autoescape(enabled_extensions=("j2",), default=True))
THEMES = {"default": {"bg": "#0e1420", "text": "#f2f5fa", "muted": "#9aa7bd", "accent": "#3d8bfd", "warn": "#f0b429"}}
LABELS = {
    "zh": {"position.notional_usd": "名义价值", "position.leverage": "杠杆", "position.entry_px": "开仓均价",
           "position.liq_px": "强平价", "position.liq_distance": "距强平", "position.unrealized_pnl_usd": "未实现盈亏",
           "change.notional_usd": "本次变动", "episode.added_notional_usd": "累计加仓", "close.pnl_estimate_usd": "估算平仓盈亏",
           "close.entry_px": "平仓前均价", "close.liq_px": "最后报告强平价", "change.mark_px": "观测时价格",
           "liquidation.closed_pnl_usd": "强平已实现盈亏", "history.net_realized_usd": "历史完整周期净盈亏"},
    "en": {"position.notional_usd": "Notional", "position.leverage": "Leverage", "position.entry_px": "Avg entry",
           "position.liq_px": "Liq. price", "position.liq_distance": "To liquidation",
           "position.unrealized_pnl_usd": "Unrealized P&L", "change.notional_usd": "This change",
           "episode.added_notional_usd": "Added so far", "close.pnl_estimate_usd": "Est. closing P&L",
           "close.entry_px": "Entry before close", "close.liq_px": "Last liq. price", "change.mark_px": "Mark at observation",
           "liquidation.closed_pnl_usd": "Realized at liquidation", "history.net_realized_usd": "Net, complete cycles"},
}
TEMPLATES = {
    "position_card": ["position.notional_usd", "position.leverage", "position.entry_px", "position.liq_px",
                      "position.liq_distance", "position.unrealized_pnl_usd", "change.notional_usd",
                      "episode.added_notional_usd"],
    "close_card": ["change.notional_usd", "close.entry_px", "change.mark_px", "close.pnl_estimate_usd", "close.liq_px"],
    "liq_card": ["change.notional_usd", "close.liq_px", "change.mark_px", "liquidation.closed_pnl_usd", "close.entry_px"],
}
TITLES = {
    "zh": {"whale_open": "大额开仓", "flip": "多空反手", "whale_add": "大额加仓", "underwater_add": "浮亏中加仓",
           "whale_close": "大额平仓", "liquidation": "强平", "near_liquidation": "接近强平", "notable_trader_move": "战绩交易员动向"},
    "en": {"whale_open": "Large open", "flip": "Position flip", "whale_add": "Large add", "underwater_add": "Adding underwater",
           "whale_close": "Large close", "liquidation": "Liquidation", "near_liquidation": "Near liquidation",
           "notable_trader_move": "Proven trader moves"},
}
NOTE_CODES = {
    "zh": {"PNL_UNREALIZED": "盈亏为未实现（浮动）", "TIME_WINDOW_ONLY": "时间为两次观测之间的窗口",
           "FIRST_SEEN_NOT_OPEN": "首次观测时已持有，开仓时间未知", "LIQ_SUSPECTED_ONLY": "疑似强平，未经交易所记录确认",
           "PNL_ESTIMATED_FROM_MARK": "平仓盈亏按观测价估算"},
    "en": {"PNL_UNREALIZED": "P&L is unrealized", "TIME_WINDOW_ONLY": "Time is a window between two observations",
           "FIRST_SEEN_NOT_OPEN": "Already open when first observed; opening time unknown",
           "LIQ_SUSPECTED_ONLY": "Suspected liquidation, not confirmed by an exchange record",
           "PNL_ESTIMATED_FROM_MARK": "Closing P&L estimated from the mark at observation"},
}


# CairoSVG only uses the first family name, so pick one that fontconfig actually has (Noto on the server).
FONT_PREFERENCE = ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei", "Hiragino Sans GB", "Heiti SC",
                   "STHeiti", "PingFang SC")


@functools.cache
def cjk_font() -> str:
    if not shutil.which("fc-list"):
        return FONT_PREFERENCE[0]
    out = subprocess.run(["fc-list", ":lang=zh", "family"], capture_output=True, text=True, timeout=10).stdout
    have = {name.strip() for line in out.splitlines() for name in line.split(",")}
    return next((f for f in FONT_PREFERENCE if f in have), FONT_PREFERENCE[0])


def card_inputs(packet: dict, template: str, language: str, brand: str) -> dict:
    facts = {f["id"]: f for f in packet["facts"]}
    ev, lang = packet["event"], language
    status = ""
    if ev["class"] == "liquidation":
        status = ("（已确认）" if ev["liquidation_status"] == "confirmed" else "（疑似）") if lang == "zh" else \
            (" (confirmed)" if ev["liquidation_status"] == "confirmed" else " (suspected)")
    side = {"zh": {"long": "多", "short": "空"}, "en": {"long": "long", "short": "short"}}[lang].get(ev["direction"], "")
    rows = [{"id": fid, "label": LABELS[lang][fid], "value": facts[fid]["display"][lang][0]}
            for fid in TEMPLATES[template] if fid in facts][:6]
    codes = {c["code"] for c in packet["constraints"]}
    notes = [NOTE_CODES[lang][c] for c in NOTE_CODES[lang] if c in codes][:2]
    window = facts.get("change.window") or facts.get("change.time")
    when = window["display"][lang][0] if window else ""
    footer = (f"数据：AlphaScope 观测样本 · {when} · 事实包 v{packet.get('version')} #{packet.get('hash', '')[:8]}" if lang == "zh"
              else f"Data: AlphaScope observed sample · {when} · fact packet v{packet.get('version')} #{packet.get('hash', '')[:8]}")
    return {"t": {"kicker": f"{ev['coin']} · {side}" if lang == "zh" else f"{ev['coin']} {side}",
                  "title": TITLES[lang][ev["class"]] + status, "subject": packet["subject"]["trader_ref"],
                  "footer": footer, "brand": brand},
            "rows": rows, "notes": notes, "c": THEMES.get(brand, THEMES["default"]), "font": cjk_font()}


CHART_TEMPLATES = ("chart_card", "lifecycle_card")
NOTE_CODES["zh"].update({"OBSERVED_SAMPLE": "AlphaScope 观测样本，不是全市场", "COPY_BACKTEST_ASSUMPTIONS": "跟单收益为回测，假设见正文",
                         "FORECAST_SHADOW": "研究预测（影子模式），不是交易信号", "SCREEN_NOT_SIGNAL": "研究筛选，不是可交易信号",
                         "SURVIVORSHIP": "榜单只显示当下表现好的人", "COMPLETE_CYCLES_ONLY": "只统计完整的已平仓周期"})
NOTE_CODES["en"].update({"OBSERVED_SAMPLE": "AlphaScope observed sample, not the whole market",
                         "COPY_BACKTEST_ASSUMPTIONS": "Copy returns are backtests with stated assumptions",
                         "FORECAST_SHADOW": "Research forecasts in shadow mode, not signals",
                         "SCREEN_NOT_SIGNAL": "A research screen, not a tradable signal",
                         "SURVIVORSHIP": "Boards only show who is doing well now",
                         "COMPLETE_CYCLES_ONLY": "Complete closed cycles only"})


def render_svg(inputs: dict) -> str:
    """Fact cards share one layout (templates differ in which facts they show); charts have their own."""
    name = "chart" if "bars" in inputs else "card"
    return ENV.get_template(f"{name}.{TEMPLATE_VERSION}.svg.j2").render(**inputs)


def _num(fact: dict) -> float:
    try:
        return abs(float(fact["value"]))
    except (TypeError, ValueError):
        return 0.0


def _footer(packet: dict, lang: str) -> str:
    return (f"数据：AlphaScope · 事实包 v{packet.get('version')} #{packet.get('hash', '')[:8]}" if lang == "zh"
            else f"Data: AlphaScope · fact packet v{packet.get('version')} #{packet.get('hash', '')[:8]}")


def chart_inputs(packet: dict, lang: str, brand: str) -> dict:
    """Bar chart from the packet's `chart` fact: every label and value is a display string of a packet fact."""
    facts = {f["id"]: f for f in packet["facts"]}
    spec = facts["chart"]["value"]
    rows = spec["rows"][:9]
    top = max((_num(facts[fid]) for r in rows for fid in r["fact_ids"] if fid in facts), default=0) or 1
    labels, bars, y = [], [], 190
    row_h = min(44, int(370 / max(len(rows), 1)))
    for r in rows:
        label = facts[r["label_fact"]]["display"][lang][0] if r.get("label_fact") else r["label"][lang]
        labels.append({"x": 64, "y": y + 18, "size": 20, "text": label[:24], "strong": True})
        for i, fid in enumerate(r["fact_ids"][:2]):
            if fid not in facts:
                continue
            w = max(4, int(460 * _num(facts[fid]) / top))
            by = y + i * 18
            bars.append({"x": 400, "y": by + 4, "w": w, "h": 14, "series": i})
            labels.append({"x": 412 + w, "y": by + 17, "size": 17, "text": facts[fid]["display"][lang][0],
                           "strong": False})
        y += row_h
    codes = {c["code"] for c in packet["constraints"]}
    subj = packet["subject"].get("title", {}).get(lang) or packet["subject"]["trader_ref"]
    return {"t": {"kicker": subj, "title": spec["title"][lang], "footer": _footer(packet, lang), "brand": brand},
            "labels": labels, "bars": bars, "line": None, "dots": [],
            "notes": [NOTE_CODES[lang][c] for c in NOTE_CODES[lang] if c in codes][:2],
            "c": THEMES.get(brand, THEMES["default"]), "font": cjk_font()}


def lifecycle_inputs(packet: dict, lang: str, brand: str) -> dict:
    """Observed size of one position over time; points are polls, so changes happened somewhere between them."""
    facts = {f["id"]: f for f in packet["facts"]}
    path = facts["episode.path"]["value"]
    sizes = [float(p["size"]) for p in path]
    top = max(sizes) or 1
    x0, x1, y0, y1 = 140, 1100, 520, 190
    ts = [datetime.fromisoformat(p["t"]).timestamp() for p in path]
    span = (ts[-1] - ts[0]) or 1  # x follows real time, so uneven polling shows as uneven spacing
    dots = [{"x": round(x0 + (x1 - x0) * (t - ts[0]) / span, 1), "y": round(y0 - (y0 - y1) * v / top, 1)}
            for t, v in zip(ts, sizes)]
    coin = packet["event"]["coin"]
    peak = max(path, key=lambda p: float(p["size"]))["size"]
    labels = [{"x": x0, "y": 548, "size": 17, "text": _when(path[0]["t"], lang), "strong": False},
              {"x": x1, "y": 548, "size": 17, "text": _when(path[-1]["t"], lang), "strong": False, "anchor": "end"},
              {"x": x0 - 12, "y": y1 + 6, "size": 18, "text": f"{peak} {coin}", "strong": True, "anchor": "end"},
              {"x": x0 - 12, "y": y0 + 6, "size": 18, "text": "0", "strong": False, "anchor": "end"}]
    note = "数据为轮询观测：变化发生在两次观测之间" if lang == "zh" else "Polled observations: changes happen between points"
    return {"t": {"kicker": packet["subject"]["trader_ref"],
                  "title": f"{coin} 仓位变化路径" if lang == "zh" else f"{coin} position path",
                  "footer": _footer(packet, lang), "brand": brand},
            "labels": labels, "bars": [], "line": " ".join(f"{p['x']},{p['y']}" for p in dots), "dots": dots,
            "notes": [], "legend": note, "c": THEMES.get(brand, THEMES["default"]), "font": cjk_font()}


def _when(iso: str, lang: str) -> str:
    return fmt.when(datetime.fromisoformat(iso))[lang][0]


def render_for_packet(sf: sessionmaker, packet_id: int, template: str, language: str, brand: str = "default") -> dict:
    if template not in TEMPLATES and template not in CHART_TEMPLATES:
        raise ValueError(f"unknown template {template}")
    with sf() as s:
        row = s.execute(text("SELECT content, content_hash, version FROM fact_packet WHERE id = :i"),
                        {"i": packet_id}).mappings().one()
        packet = {**row["content"], "hash": row["content_hash"], "version": row["version"]}
        name = "AlphaScope" if brand == "default" else brand
        facts = {f["id"] for f in packet["facts"]}
        if template == "chart_card" and "chart" not in facts or template == "lifecycle_card" and "episode.path" not in facts:
            raise ValueError(f"this packet has nothing for {template}")
        inputs = (chart_inputs(packet, language, name) if template == "chart_card" else
                  lifecycle_inputs(packet, language, name) if template == "lifecycle_card" else
                  card_inputs(packet, template, language, name))
        data_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        svg = render_svg(inputs)
        png = cairosvg.svg2png(bytestring=svg.encode())
        out = {}
        for kind, blob, ext in (("card_svg", svg.encode(), "svg"), ("card_png", png, "png")):
            sha = hashlib.sha256(blob).hexdigest()
            now = datetime.now(UTC)
            path = Path(get_settings().asset_dir) / f"{now:%Y}" / f"{now:%m}" / f"{sha}.{ext}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            out[kind] = s.execute(text("""
                INSERT INTO media_asset (kind, template_key, template_version, fact_packet_id, language, brand, data_hash,
                                         file_path, sha256, width, height)
                VALUES (:k, :t, :tv, :p, :l, :b, :dh, :fp, :sha, 1200, 675)
                ON CONFLICT (template_key, template_version, fact_packet_id, language, brand, kind)
                DO UPDATE SET file_path = EXCLUDED.file_path, sha256 = EXCLUDED.sha256, data_hash = EXCLUDED.data_hash
                RETURNING id"""),
                {"k": kind, "t": template, "tv": TEMPLATE_VERSION, "p": packet_id, "l": language, "b": brand,
                 "dh": data_hash, "fp": str(path), "sha": sha}).scalar_one()
        s.commit()
    return {"assets": out, "data_hash": data_hash, "rows": inputs.get("rows", [])}

