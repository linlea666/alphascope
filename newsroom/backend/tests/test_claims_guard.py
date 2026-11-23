from decimal import Decimal

from nr.facts import fmt
from nr.guard import numbers, phrases
from nr.guard.duplicates import similarity
from nr.guard.run import run
from nr.guard.xlength import weighted_length

ALLOW = ["24h", "7d", "30d", "90d", "24小时", "7天", "30天", "90天"]


def F(fid, kind, value, disp=None, **x):
    return {"id": fid, "kind": kind, "value": value, "display": disp or {}, **x}


def packet(constraints=("OBSERVED_SAMPLE", "NOTIONAL_NOT_MARGIN", "NO_IDENTITY", "PNL_UNREALIZED", "TIME_WINDOW_ONLY",
                        "FIRST_SEEN_NOT_OPEN"), cls="whale_add", extra=()):
    facts = [
        F("position.notional_usd", "notional", "12345678.9", fmt.usd(Decimal("12345678.9"))),
        F("position.leverage", "leverage", "20"),
        F("position.unrealized_pnl_usd", "unrealized_pnl", "-210000"),
        F("position.implied_margin_usd", "estimate", "617283.95"),
        F("position.entry_px", "price", "98000"),
        F("position.liq_distance", "percent", "0.0234"),
        F("change.notional_usd", "notional", "5000000"),
        F("change.window", "time_window", {"from": "2026-11-20T08:00:00+00:00", "to": "2026-11-20T08:30:00+00:00"}),
        F("episode.first_seen_at", "time", "2026-11-19T22:00:00+00:00"),
        F("history.complete_cycles", "count", 34),
        *extra,
    ]
    return {"event": {"class": cls}, "facts": facts, "constraints": [{"code": c} for c in constraints]}


def nums(text, pk=None):
    return [f.text for f in numbers.check(text, (pk or packet())["facts"], ALLOW)]


def codes(text, pk=None, forbidden=()):
    return [f.code for f in phrases.check(text, pk or packet(), list(forbidden))]


def test_numbers_must_come_from_the_packet():
    assert nums("该地址持有约1234.57万美元的 BTC 多单，杠杆20倍。") == []
    assert nums("A $12.3M BTC long at 20x, 2.3% from liquidation.") == []
    assert nums("浮亏21万美元，过去 34 个完整周期") == []
    assert nums("北京时间 11-20 16:00 至 16:30 之间加仓 500万美元") == []
    assert nums("Entry around $98,000; in the last 24h") == []
    # invented or wrongly rounded numbers
    assert nums("持有约1500万美元多单") == ["1500万美元"]
    assert nums("A $13M position") == ["$13M"]  # more than half a unit off $12.35M
    assert nums("杠杆50倍") == ["50倍"]
    assert nums("11月21日加仓") == ["11月21日"]
    assert nums("at 09:15 UTC") == ["09:15"]
    # numbers written in Chinese characters are checked too
    assert nums("持有约一千二百三十万美元的多单，杠杆二十倍") == []
    assert nums("持有三千万美元多单") == ["三千万美元"]
    assert nums("杠杆五十倍") == ["五十倍"]
    assert nums("一个历史战绩突出的地址") == []
    # tickers, handles and the address shortener are not numbers
    assert nums("1000PEPE and $BTC by @alpha_1 0xabcd…1234") == []


def test_wording_constraints():
    assert codes("浮亏21万美元，仍在加仓。") == []
    assert "PNL_CALLED_REALIZED" in codes("已实现利润21万美元")
    assert "PNL_NOT_MARKED_UNREALIZED" in codes("目前亏损21万美元")
    assert "PNL_CALLED_REALIZED" in codes("The whale banked $210K on this trade.")
    assert "EXACT_TIME_CLAIM" in codes("该巨鲸于 16:15 加仓 BTC")
    assert codes("在北京时间 16:00 至 16:30 之间加仓") == []
    assert "OPEN_NOT_OBSERVED" in codes("这个钱包刚开了一笔 BTC 多单")
    assert codes("该仓位首次被观察到时已持有") == []
    assert codes("开仓均价 98,000 美元") == []
    assert "NOTIONAL_AS_MARGIN" in codes("他投入了1234.57万美元本金")
    assert codes("按20倍杠杆估算，保证金约61.73万美元") == []
    assert "WHOLE_MARKET_CLAIM" in codes("全市场都在做多 BTC")
    assert "IDENTITY_CLAIM" in codes("该地址疑似某机构")
    assert "IDENTITY_CLAIM" in codes("Looks like an insider trade")
    assert "ADVICE_OR_GUARANTEE" in codes("建议跟单，稳赚")
    assert "LIQ_NOT_IN_FACTS" in codes("这个巨鲸爆仓了")
    assert codes("距强平价仅 2.34%，爆仓风险上升") == []
    liq = packet(constraints=("LIQ_SUSPECTED_ONLY", "OBSERVED_SAMPLE"), cls="liquidation")
    assert "LIQ_UNCONFIRMED" in codes("巨鲸被强平", liq)
    assert codes("巨鲸疑似被强平", liq) == []
    assert codes("A $5M long was likely liquidated", liq) == []
    assert codes("Let's dive in: a whale moved", forbidden=["Let's dive in"]) == ["STYLE_PHRASE"]


def test_guard_report_and_hard_failures():
    pk = packet()
    ok = run({"headline": "巨鲸加仓 BTC", "body": "该地址在北京时间 11-20 16:00 至 16:30 之间加仓500万美元，浮亏21万美元。"},
             pk, facts_used=["change.notional_usd"], version_packet_id=7, current_packet_id=7, long_post_allowed=False,
             allow=ALLOW, forbidden=[], duplicate=None, dup_block=0.45, dup_warn=0.3)
    assert ok["status"] == "pass", ok["findings"]
    bad = run({"body": "巨鲸投入1500万美元"}, pk, facts_used=["nope"], version_packet_id=6, current_packet_id=7,
              long_post_allowed=False, allow=ALLOW, forbidden=[], duplicate={"similarity": 0.8, "article_id": 1,
                                                                             "account_id": 2, "kind": "draft"},
              dup_block=0.45, dup_warn=0.3)
    found = {f["code"] for f in bad["findings"]}
    assert bad["status"] == "fail"
    assert {"UNMATCHED_NUMBER", "UNKNOWN_FACT", "FACT_PACKET_SUPERSEDED", "NEAR_DUPLICATE"} <= found
    long = run({"body": "巨" * 141}, pk, facts_used=[], version_packet_id=7, current_packet_id=7,
               long_post_allowed=False, allow=ALLOW, forbidden=[], duplicate=None, dup_block=0.45, dup_warn=0.3)
    assert {f["code"] for f in long["findings"]} == {"TOO_LONG"}


def test_rewording_is_still_a_duplicate():
    a = "一个历史战绩突出的 Hyperliquid 钱包在北京时间下午加仓 500 万美元 BTC 多单，目前浮亏 21 万美元，距强平价 2.34%。"
    b = "一个历史战绩突出的 Hyperliquid 钱包在北京时间下午加仓了 500 万美元 BTC 多单，当前浮亏 21 万美元，离强平价 2.34%。"
    c = "数据角度：这个账户 34 个完整周期里，仓位从未超过 300 万美元；这一次的规模是它此前最大仓位的四倍以上。"
    assert similarity(a, b, "zh") >= 0.45 and similarity(a, c, "zh") < 0.2
    assert similarity("A whale added $5M to a BTC long", "A whale added $5M to its BTC long", "en") > 0.3


def test_x_weighted_length():
    assert weighted_length("hello") == 5 and weighted_length("巨鲸") == 4
    assert weighted_length("see https://example.com/a/very/long/path") == 4 + 23
