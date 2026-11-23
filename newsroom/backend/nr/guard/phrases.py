"""Wording rules tied to the packet's constraints (docs/06-facts-and-guard.md). Each rule looks at one sentence at a
time, so a qualifier ("疑似", "unrealized") only counts when it sits next to the claim it qualifies."""

import re
from decimal import Decimal

from nr.guard.numbers import MULT, NUMBER, Finding

SENTENCE = re.compile(r"[^。！？!?\n；;]+(?:[。！？!?；;]|\n|$)|[^\n]+")
LIQ_EVENT = re.compile(r"爆仓(?!风险|边缘|线)|被强平|遭(?:到)?强平|强制平仓|被清算|\bliquidated\b|\bwiped out\b|\bgot rekt\b",
                       re.I)
LIQ_QUALIFIER = re.compile(r"疑似|可能|或已|推测|似乎|据推算|\bsuspected\b|\bpossibl[ey]\b|\blikely\b|\bapparent(ly)?\b|"
                           r"\bappears?\b|\binferred\b|\bseems?\b", re.I)
REALIZED = re.compile(r"已实现|落袋|锁定(?:利润|收益|盈利)|兑现|实现了?盈利|止盈离场|\brealized\b|\bbanked\b|\blocked in\b|"
                      r"\bcashed out\b|\btook profits?\b|\bbooked\b", re.I)
UNREALIZED = re.compile(r"浮盈|浮亏|未实现|账面|\bunrealized\b|\bpaper\b|\bon paper\b|\bopen (?:profit|loss|p&l)\b", re.I)
WINDOW_MARK = re.compile(r"之间|至|到|之前|以前|\bbetween\b|\bby\b|\bbefore\b|\bsometime\b", re.I)
CLOCK_ACTION = re.compile(r"\d{1,2}[:：]\d{2}.{0,12}(开仓|开多|开空|建仓|加仓|买入|卖出|平仓|减仓|反手)|"
                          r"(开仓|开多|开空|建仓|加仓|买入|卖出|平仓|减仓|反手).{0,6}(于|在)\s*\d{1,2}[:：]\d{2}|"
                          r"\b(opened|bought|sold|added|closed|entered|flipped)\b[^.]{0,30}\bat\s+\d{1,2}:\d{2}", re.I)
OPEN_WORDS = re.compile(r"新开|刚开|开仓(?!均?价)|建仓|开了|\bjust opened\b|\bopened\b|\bopens\b|\bnew position\b|"
                        r"\bentered\b", re.I)
FIRST_SEEN_OK = re.compile(r"首次(?:被)?(?:观察|发现|监测)|已持有|\bfirst (?:observed|seen|spotted)\b|\balready (?:held|holding)\b",
                           re.I)
MARGIN_WORDS = re.compile(r"本金|保证金|投入|押上|押注|砸下|砸了|梭哈|\ball[- ]in\b|\bstaked\b|\bput up\b|\bbet\b|\bwagered\b|"
                          r"\bown money\b|\bmargin\b", re.I)
ESTIMATE = re.compile(r"估算|约|大约|推算|\bestimated\b|\bimplied\b|\broughly\b|\babout\b|\b~", re.I)
WHOLE_MARKET = re.compile(r"全市场|全网|所有交易者|所有人|整个市场|\bwhole market\b|\bentire market\b|\ball traders\b|"
                          r"\beveryone\b", re.I)
IDENTITY = re.compile(r"(疑似|可能|或为|据传|应该是|大概率是|就是)\S{0,10}(机构|基金|交易所|做市商|内幕|项目方|团队)|内幕|知情人|"
                      r"\binsiders?\b|\binside information\b|\b(?:is|was) (?:likely|probably|reportedly) (?:a|an|the) "
                      r"(?:fund|institution|exchange|market maker)\b|\bbelongs to\b|\bowned by\b|\bcontrolled by\b", re.I)
ADVICE = re.compile(r"稳赚|必涨|必跌|包赚|建议(?:买入|卖出|做多|做空|跟单|抄底)|赶紧(?:买|跟)|无脑|\bguaranteed\b|\bcan'?t lose\b|"
                    r"\bshould (?:buy|sell|long|short)\b|(?<!not )\bfinancial advice\b", re.I)


def sentences(text: str):
    for m in SENTENCE.finditer(text):
        if m.group(0).strip():
            yield m.start(), m.group(0)


def _money_values(sentence: str) -> list[Decimal]:
    out = []
    for m in NUMBER.finditer(sentence):
        unit = (m.group("unit") or "").lower()
        if unit in ("%", "个百分点", "x", "倍"):
            continue
        try:
            out.append(Decimal(m.group("num").replace(",", "")) * MULT.get(unit, Decimal(1)))
        except ArithmeticError:
            pass
    return out


def _near(values: list[Decimal], target: Decimal | None) -> bool:
    return target is not None and any(abs(v - target) <= max(abs(target) * Decimal("0.05"), Decimal("0.5"))
                                      for v in values)


def check(text: str, packet: dict, forbidden: list[str]) -> list[Finding]:
    codes = {c["code"] for c in packet["constraints"]}
    facts = {f["id"]: f for f in packet["facts"]}
    has_realized = any(f["kind"] == "realized_pnl" for f in packet["facts"])
    upl = facts.get("position.unrealized_pnl_usd")
    upl_v = abs(Decimal(upl["value"])) if upl else None
    margin = facts.get("position.implied_margin_usd")
    margin_v = abs(Decimal(margin["value"])) if margin else None
    is_liq = packet["event"]["class"] == "liquidation"
    out: list[Finding] = []

    def flag(code, start, m, detail):
        out.append(Finding(code, (start + m.start(), start + m.end()), m.group(0), detail))

    for start, sent in sentences(text):
        for m in LIQ_EVENT.finditer(sent):
            if not is_liq:
                flag("LIQ_NOT_IN_FACTS", start, m, "no liquidation in this event's facts")
            elif "LIQ_SUSPECTED_ONLY" in codes and not LIQ_QUALIFIER.search(sent):
                flag("LIQ_UNCONFIRMED", start, m, "liquidation is only suspected: say so in the same sentence")
        money = _money_values(sent)
        for m in REALIZED.finditer(sent):
            if not has_realized or (_near(money, upl_v) and not UNREALIZED.search(sent)):
                flag("PNL_CALLED_REALIZED", start, m, "only unrealized or estimated P&L is in the facts")
        if upl_v is not None and _near(money, upl_v) and not UNREALIZED.search(sent) \
                and re.search(r"盈利|亏损|赚|亏|\bprofit\b|\bloss\b|\bup\b|\bdown\b|\bgain\b", sent, re.I):
            out.append(Finding("PNL_NOT_MARKED_UNREALIZED", (start, start + len(sent)), sent.strip()[:60],
                               "mark open-position P&L as unrealized (浮盈/浮亏/unrealized)"))
        if "TIME_WINDOW_ONLY" in codes:
            for m in CLOCK_ACTION.finditer(sent):
                if not WINDOW_MARK.search(sent):
                    flag("EXACT_TIME_CLAIM", start, m, "only an observation window is known, not an exact time")
        if "FIRST_SEEN_NOT_OPEN" in codes:
            for m in OPEN_WORDS.finditer(sent):
                if not FIRST_SEEN_OK.search(sent):
                    flag("OPEN_NOT_OBSERVED", start, m, "the position was already open when first observed")
        for m in MARGIN_WORDS.finditer(sent):
            if money and not (_near(money, margin_v) and ESTIMATE.search(sent)):
                flag("NOTIONAL_AS_MARGIN", start, m, "notional is not margin; margin may only be cited as the estimate")
        for m in WHOLE_MARKET.finditer(sent):
            flag("WHOLE_MARKET_CLAIM", start, m, "the data is an observed sample, not the whole market")
        for m in IDENTITY.finditer(sent):
            flag("IDENTITY_CLAIM", start, m, "do not guess who controls the account or imply inside information")
        for m in ADVICE.finditer(sent):
            flag("ADVICE_OR_GUARANTEE", start, m, "no trading advice or guaranteed outcomes")
        for phrase in forbidden:
            for m in re.finditer(re.escape(phrase), sent, re.I):
                out.append(Finding("STYLE_PHRASE", (start + m.start(), start + m.end()), m.group(0),
                                   "phrase on the style's avoid list"))
    return out
