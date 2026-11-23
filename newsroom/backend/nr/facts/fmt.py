"""Deterministic display strings for fact values, zh and en. Writers (human or AI) copy these; cards render the first.
The claims guard accepts any rendering of the same value within display precision, not just these strings."""

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

BEIJING = timezone(timedelta(hours=8))


def _q(v: Decimal, places: int) -> str:
    return str(v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)) if places else \
        str(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _trim(s: str) -> str:
    return s.rstrip("0").rstrip(".") if "." in s else s


def _commas(v: Decimal, places: int) -> str:
    return f"{v:,.{places}f}"


def usd(v: Decimal) -> dict[str, list[str]]:
    a, sign = abs(v), "-" if v < 0 else ""
    if a >= Decimal("1e9"):
        en = [f"{sign}${_trim(_q(a / Decimal('1e9'), 2))}B", f"{sign}${_trim(_q(a / Decimal('1e9'), 1))}B"]
    elif a >= Decimal("1e6"):
        en = [f"{sign}${_trim(_q(a / Decimal('1e6'), 2))}M", f"{sign}${_trim(_q(a / Decimal('1e6'), 1))}M"]
    elif a >= Decimal("1e3"):
        en = [f"{sign}${_trim(_q(a / Decimal('1e3'), 1))}K", f"{sign}${_q(a / Decimal('1e3'), 0)}K"]
    else:
        en = [f"{sign}${_trim(_q(a, 2))}"]
    en.append(f"{sign}${_commas(a, 0)}")
    # the way people write it comes first: 2-3 significant digits (2812万美元, 1.29亿美元, 39.3万美元)
    if a >= Decimal("1e8"):
        zh = [f"{sign}{_trim(_q(a / Decimal('1e8'), 2))}亿美元", f"{sign}{_trim(_q(a / Decimal('1e8'), 1))}亿美元"]
    elif a >= Decimal("1e6"):
        zh = [f"{sign}{_q(a / Decimal('1e4'), 0)}万美元", f"{sign}{_trim(_q(a / Decimal('1e4'), 2))}万美元"]
    elif a >= Decimal("1e4"):
        zh = [f"{sign}{_trim(_q(a / Decimal('1e4'), 1))}万美元", f"{sign}{_trim(_q(a / Decimal('1e4'), 2))}万美元"]
    else:
        zh = [f"{sign}{_q(a, 0)}美元", f"{sign}{_trim(_q(a, 2))}美元"]
    return {"zh": list(dict.fromkeys(zh)), "en": list(dict.fromkeys(en))}


def price(v: Decimal) -> dict[str, list[str]]:
    a = abs(v)
    if a >= 1000:  # whole dollars first; 6.35万 is how Chinese posts quote a BTC price
        s, fine = _commas(a, 0), _trim(_commas(a, 1))
        zh = [f"{s}美元", s, f"{fine}美元"] + ([f"{_trim(_q(a / Decimal('1e4'), 2))}万"] if a >= 10000 else [])
        return {"zh": list(dict.fromkeys(zh)), "en": list(dict.fromkeys([f"${s}", f"${fine}"]))}
    places = 2 if a >= 1 else 6
    s = _trim(_commas(a, places)) if a >= 1 else _trim(_q(a, places))
    return {"zh": [f"{s}美元", s], "en": [f"${s}"]}


def size(v: Decimal, coin: str) -> dict[str, list[str]]:
    a = abs(v)
    exact = _trim(_commas(a, 4))
    s = _commas(a, 0) if a >= 100 else _trim(_commas(a, 2)) if a >= 1 else exact  # 267 BTC, not 267.4216 BTC
    return {"zh": list(dict.fromkeys([f"{s}枚{coin}", f"{s} {coin}", f"{exact} {coin}"])),
            "en": list(dict.fromkeys([f"{s} {coin}", f"{exact} {coin}"]))}


def leverage(v: Decimal) -> dict[str, list[str]]:
    s = _trim(_q(v, 1))
    return {"zh": [f"{s}倍"], "en": [f"{s}x"]}


def pct(fraction: Decimal) -> dict[str, list[str]]:
    p = fraction * 100
    return {"zh": [f"{_trim(_q(p, 2))}%", f"{_trim(_q(p, 1))}%"], "en": [f"{_trim(_q(p, 2))}%", f"{_trim(_q(p, 1))}%"]}


def multiple(v: Decimal) -> dict[str, list[str]]:
    s = _trim(_q(v, 1))
    return {"zh": [f"{s}倍"], "en": [f"{s}x"]}


def count(n: int) -> dict[str, list[str]]:
    return {"zh": [str(n)], "en": [str(n)]}


def cashtag(coin: str) -> str:
    """$NVDA for the venue's xyz:NVDA: the way a post names any perp."""
    return "$" + coin.split(":")[-1]


def headcount(n: int) -> dict[str, list[str]]:
    """A large count the way a post says it: 约1.4万 / ~14,000."""
    if n < 1000:
        return count(n)
    wan = _trim(_q(Decimal(n) / Decimal("1e4"), 1))
    k = f"{round(n, -3):,}"
    return {"zh": [f"约{wan}万", f"{n:,}"], "en": [f"~{k}", f"{n:,}"]}


def duration(hours: Decimal) -> tuple[Decimal, str, dict[str, list[str]]]:
    """Whole hours under three days, else days to one decimal: the value is in the displayed unit, so the guard
    matches what the writer copies."""
    if hours < 72:
        v = Decimal(_q(hours, 0))
        return v, "hours", {"zh": [f"{v}小时"], "en": [f"{v} hours", f"{v}h"]}
    v = Decimal(_trim(_q(hours / 24, 1)))
    return v, "days", {"zh": [f"{v}天"], "en": [f"{v} days"]}


def when(t: datetime) -> dict[str, list[str]]:
    u, b = t.astimezone(timezone.utc), t.astimezone(BEIJING)
    return {"zh": [f"{b:%Y-%m-%d %H:%M}（北京时间）", f"{u:%Y-%m-%d %H:%M} UTC"],
            "en": [f"{u:%Y-%m-%d %H:%M} UTC", f"{u:%b} {u.day}, {u:%H:%M} UTC"]}


def window(a: datetime | None, b: datetime) -> dict[str, list[str]]:
    if a is None:
        w = when(b)
        return {"zh": [f"{w['zh'][0]}之前"], "en": [f"before {w['en'][0]}"]}
    ua, ub, ba, bb = (a.astimezone(timezone.utc), b.astimezone(timezone.utc), a.astimezone(BEIJING),
                      b.astimezone(BEIJING))
    return {"zh": [f"北京时间 {ba:%m-%d %H:%M} 至 {bb:%m-%d %H:%M} 之间", f"UTC {ua:%m-%d %H:%M} 至 {ub:%m-%d %H:%M} 之间"],
            "en": [f"between {ua:%b} {ua.day} {ua:%H:%M} and {ub:%b} {ub.day} {ub:%H:%M} UTC"]}
