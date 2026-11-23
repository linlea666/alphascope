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


def render_svg(inputs: dict) -> str:
    """One layout for every template; templates differ in which facts they show."""
    return ENV.get_template(f"card.{TEMPLATE_VERSION}.svg.j2").render(**inputs)


def render_for_packet(sf: sessionmaker, packet_id: int, template: str, language: str, brand: str = "default") -> dict:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template}")
    with sf() as s:
        row = s.execute(text("SELECT content, content_hash, version FROM fact_packet WHERE id = :i"),
                        {"i": packet_id}).mappings().one()
        packet = {**row["content"], "hash": row["content_hash"], "version": row["version"]}
        inputs = card_inputs(packet, template, language, "AlphaScope" if brand == "default" else brand)
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
    return {"assets": out, "data_hash": data_hash, "rows": inputs["rows"]}

