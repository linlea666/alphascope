"""Every number in a story must come from the fact packet.

Numbers are read with their units (万/亿/K/M/B, %, x/倍, $), dates and clock times separately, then matched against
packet values: within half a unit of the last written digit, and for amounts within 5% (so "1亿" cannot stand for
1.4亿). Anything else is UNMATCHED_NUMBER, which fails the guard.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

BEIJING = timezone(timedelta(hours=8))
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"))}
MULT = {"亿美元": Decimal("1e8"), "亿": Decimal("1e8"), "万美元": Decimal("1e4"), "万": Decimal("1e4"),
        "k": Decimal("1e3"), "thousand": Decimal("1e3"), "m": Decimal("1e6"), "million": Decimal("1e6"),
        "mn": Decimal("1e6"), "b": Decimal("1e9"), "bn": Decimal("1e9"), "billion": Decimal("1e9")}
URL = re.compile(r"https?://\S+")
HANDLE = re.compile(r"@\w+")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{3,}(?:…|\.\.\.)?[0-9a-fA-F]*")  # full, or short as 0x9ee…be185
OKX_REF = re.compile(r"OKX·\d+")
# boundaries are ASCII-only: Python's \w also matches CJK, and "约1500万" must still yield 1500
TICKER = re.compile(r"(?<![A-Za-z0-9])(?!(?:MILLION|BILLION|THOUSAND)\b)\$?\d*[A-Z][A-Z0-9]+(?![A-Za-z0-9])")  # BTC, 1000PEPE, $ETH (cashtags)
ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
SHORT_DATE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])")
ZH_DATE = re.compile(r"(\d{1,2})月(\d{1,2})日")
ZH_MONTH = re.compile(r"(?<![\d-])(\d{1,2})月(?![\d日])")  # "9月以来": a month on its own
EN_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
YEAR = re.compile(r"(?<![\d.])(20\d{2})(?=年|\s|$|[,.;)])")
CLOCK = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>亿美元|万美元|亿|万|美元|个百分点|%|倍|x(?![a-zA-Z])|bn\b|mn\b|[kKmMbB](?![a-zA-Z])|(?i:thousand|million|billion)\b)?")


CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
# Chinese-numeral amounts ("三千万美元", "二十倍") would otherwise slip past the digit check
CN_NUMBER = re.compile(r"(?<![0-9.])(?P<num>[零〇一二两三四五六七八九十][零〇一二两三四五六七八九十百千万亿]*"
                       r"(?:点[零〇一二三四五六七八九]+)?)\s*(?P<unit>美元|美金|刀|倍|个百分点)")


def cn_to_decimal(t: str) -> Decimal | None:
    whole, _, frac = t.partition("点")
    total, section, digit = Decimal(0), Decimal(0), Decimal(0)
    for ch in whole:
        if ch in CN_DIGITS:
            digit = Decimal(CN_DIGITS[ch])
        elif ch in CN_UNITS:
            section += (digit or 1) * CN_UNITS[ch]
            digit = Decimal(0)
        elif ch == "万":
            total += (section + digit) * 10_000
            section = digit = Decimal(0)
        elif ch == "亿":
            total = (total + section + digit) * 100_000_000
            section = digit = Decimal(0)
        else:
            return None
    value = total + section + digit
    if frac:
        value += Decimal("0." + "".join(str(CN_DIGITS[c]) for c in frac))
    return value


@dataclass
class Finding:
    code: str
    span: tuple[int, int]
    text: str
    detail: str


def _num(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _half_unit(raw: str) -> Decimal:
    digits = raw.replace(",", "")
    places = len(digits.split(".")[1]) if "." in digits else 0
    return Decimal(5).scaleb(-(places + 1))


def _rounded_half_unit(raw: str) -> Decimal:
    """Trailing zeros of a whole number are rounding ("2800万" is 2800 to the nearest hundred), not precision."""
    digits = raw.replace(",", "")
    if "." in digits or digits == "0":
        return _half_unit(raw)
    zeros = len(digits) - len(digits.rstrip("0"))
    return Decimal(5).scaleb(zeros - 1)


def fact_numbers(facts: list[dict]) -> dict[str, list[Decimal]]:
    """Absolute values by family: money, percent (as %), multiple (x), plain (sizes, counts)."""
    out: dict[str, list[Decimal]] = {"money": [], "percent": [], "multiple": [], "plain": []}
    for f in facts:
        if f["kind"] in ("time", "time_window", "text"):
            continue
        try:
            v = abs(Decimal(str(f["value"])))
        except (InvalidOperation, TypeError):
            continue
        k = f["kind"]
        if k in ("notional", "realized_pnl", "unrealized_pnl", "pnl", "estimate", "price"):
            out["money"].append(v)
        elif k in ("percent", "return"):
            out["percent"].append(v * 100)
        elif k in ("leverage", "ratio"):
            out["multiple"].append(v)
            out["plain"].append(v)
        else:
            out["plain"].append(v)
    return out


def fact_times(facts: list[dict]) -> list[datetime]:
    out = []
    for f in facts:
        vals = []
        if f["kind"] == "time":
            vals = [f["value"]]
        elif f["kind"] == "time_window":
            vals = [f["value"]["from"], f["value"]["to"]]
        for v in vals:
            if v:
                t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                out += [t.astimezone(timezone.utc), t.astimezone(BEIJING)]
    return out


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def check(text: str, facts: list[dict], allow: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    times = fact_times(facts)
    days = {(t.month, t.day) for t in times}
    months = {t.month for t in times}
    years = {t.year for t in times}
    clocks = {(t.hour, t.minute) for t in times}
    work = text
    for rx in (URL, HANDLE, ADDRESS, OKX_REF):
        work = _blank(work, [m.span() for m in rx.finditer(work)])
    for phrase in sorted(allow, key=len, reverse=True):
        work = _blank(work, [m.span() for m in re.finditer(re.escape(phrase), work)])
    # dates and clock times
    for rx, kind in ((ISO_DATE, "iso"), (ZH_DATE, "zh"), (EN_DATE, "en"), (SHORT_DATE, "short")):
        for m in list(rx.finditer(work)):
            if kind == "iso":
                ok = int(m.group(1)) in years and (int(m.group(2)), int(m.group(3))) in days
            elif kind == "en":
                ok = (MONTHS[m.group(1)[:3].lower()], int(m.group(2))) in days
            else:
                ok = (int(m.group(1)), int(m.group(2))) in days
            if not ok:
                findings.append(Finding("UNMATCHED_DATE", m.span(), m.group(0), "date not in the fact packet"))
            work = _blank(work, [m.span()])
    for m in list(ZH_MONTH.finditer(work)):
        if int(m.group(1)) not in months:
            findings.append(Finding("UNMATCHED_DATE", m.span(), m.group(0), "month not in the fact packet"))
        work = _blank(work, [m.span()])
    for m in list(CLOCK.finditer(work)):
        if (int(m.group(1)), int(m.group(2))) not in clocks:
            findings.append(Finding("UNMATCHED_TIME", m.span(), m.group(0), "time not in the fact packet"))
        work = _blank(work, [m.span()])
    for m in list(YEAR.finditer(work)):
        if int(m.group(1)) not in years:
            findings.append(Finding("UNMATCHED_DATE", m.span(), m.group(0), "year not in the fact packet"))
        work = _blank(work, [m.span()])
    work = _blank(work, [m.span() for m in TICKER.finditer(work)])
    pool = fact_numbers(facts)
    for m in CN_NUMBER.finditer(work):
        v, unit = cn_to_decimal(m.group("num")), m.group("unit")
        family = "multiple" if unit == "倍" else "percent" if unit == "个百分点" else "money"
        ok = v is not None and any(abs(v - c) <= max(abs(c) * Decimal("0.05"), Decimal("0.5")) for c in pool[family])
        if not ok:
            findings.append(Finding("UNMATCHED_NUMBER", m.span(), m.group(0), "number not found in the fact packet"))
        work = _blank(work, [m.span()])
    for m in NUMBER.finditer(work):
        raw, unit = m.group("num"), (m.group("unit") or "").lower()
        v = _num(raw)
        if v is None:
            continue
        half = _half_unit(raw)
        after = work[m.end():m.end() + 4].strip().lower()
        if unit in ("%", "个百分点"):
            candidates, value, tol = pool["percent"], v, half
        elif unit in ("x", "倍"):
            candidates, value, tol = pool["multiple"], v, half
        else:
            mult = MULT.get(unit, Decimal(1))
            value, tol = v * mult, half * mult
            if value >= 1000:  # a round amount; the 5% rule below still stops a coarse figure standing for another
                tol = _rounded_half_unit(raw) * mult
            money = bool(m.group("cur")) or "美元" in unit or after.startswith(("usd", "美元", "美金", "dollar"))
            candidates = pool["money"] if money else pool["money"] + pool["plain"]
        # amounts: also within 5%, so a coarse rounding cannot stand for a different figure
        ok = any(abs(value - c) <= tol + Decimal("1e-9")
                 and (value < 1000 or abs(value - c) / max(c, Decimal(1)) <= Decimal("0.05")) for c in candidates)
        if not ok:
            findings.append(Finding("UNMATCHED_NUMBER", m.span(), m.group(0).strip(),
                                    "number not found in the fact packet within display precision"))
    return findings
