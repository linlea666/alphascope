"""Writing v3: round numbers, relative time, short addresses, house styles per reporter, ranked hooks, a character
budget, and the address + link added by code as the thread's last post."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from nr.desk import assignment
from nr.desk.drafting import char_budget, generate_draft
from nr.desk.hooks import hooks
from nr.facts import fmt
from nr.facts.builder import short_ref
from nr.guard import numbers, phrases
from nr.guard.run import run as run_guard
from tests.fakes import WHALE, FakeFeed, context, ev, position
from tests.helpers import NOW, draft_output, newsroom
from tests.pipeline import pipeline
from tests.test_contrast import crowd
from tests.test_detection import fact

FACTS = [{"id": "position.notional_usd", "kind": "notional", "value": "28123456", "display": fmt.usd(Decimal("28123456"))},
         {"id": "position.size", "kind": "size", "value": "267.4216", "display": fmt.size(Decimal("267.4216"), "BTC")},
         {"id": "position.liq_px", "kind": "price", "value": "63501.03", "display": fmt.price(Decimal("63501.03"))},
         {"id": "crowd.sample_traders", "kind": "count", "value": 13969, "display": fmt.headcount(13969)}]


def codes(found):
    return [f.code for f in found]


def test_round_numbers_short_addresses_and_hooks_pass_the_guard():
    assert fmt.usd(Decimal("28123456"))["zh"][0] == "2812万美元" and fmt.size(Decimal("267.4216"), "BTC")["zh"][0] == "267枚BTC"
    assert "6.35万" in fmt.price(Decimal("63501.03"))["zh"] and fmt.headcount(13969)["zh"][0] == "约1.4万"
    assert short_ref("0x9ee0000000000000000000000000000000be185") == "0x9ee…be185"
    ok = "🚨 鲸鱼警报\n0x9ee…be185 刚把 $BTC 空单加到2800万美元，267枚\n在我们追踪的约1.4万个地址里排第一\n爆仓价6.35万，他知道些什么？"
    assert codes(numbers.check(ok, FACTS, [])) == []
    assert codes(numbers.check("空单2000万美元", FACTS, [])) == ["UNMATCHED_NUMBER"]  # rounding, not another figure
    packet = {"facts": FACTS, "constraints": [], "event": {"class": "whale_open"}}
    assert codes(phrases.check("他知道些什么？", packet, [])) == []
    assert codes(phrases.check("他有内幕消息。", packet, [])) == ["IDENTITY_CLAIM"]
    assert codes(phrases.check("他押注BTC下跌，仓位2800万美元。", packet, [])) == []  # a figure of speech
    assert codes(phrases.check("他梭哈2800万美元做空。", packet, [])) == ["NOTIONAL_AS_MARGIN"]


def test_just_now_only_holds_for_three_hours():
    seen = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    packet = {"facts": [{"id": "change.recency", "kind": "text", "value": "just_now", "as_of": seen.isoformat(),
                         "display": {"zh": ["刚刚"], "en": ["just now"]}}], "constraints": [], "event": {"class": "whale_open"}}

    def report(now):
        return run_guard({"body": "刚刚，巨鲸开出 $BTC 空单"}, packet, facts_used=[], version_packet_id=1,
                         current_packet_id=1, long_post_allowed=False, allow=[], forbidden=[], duplicate=None,
                         dup_block=1, dup_warn=1, now=now)
    assert report(seen + timedelta(hours=2))["status"] == "pass"
    assert [f["code"] for f in report(seen + timedelta(hours=4))["findings"]] == ["RECENCY_STALE"]


def test_hooks_rank_the_story_and_a_style_can_prefer_its_kind():
    packet = {"event": {"class": "underwater_add"}, "facts": [
        {"id": "crowd.same_side_rank", "kind": "count", "value": 1, "display": fmt.count(1)},
        {"id": "crowd.sample_traders", "kind": "count", "value": 13969, "display": fmt.headcount(13969)},
        {"id": "position.liq_distance", "kind": "percent", "value": "0.008", "display": fmt.pct(Decimal("0.008"))},
        {"id": "position.notional_usd", "kind": "notional", "value": "64000000", "display": fmt.usd(Decimal("64000000"))}]}
    ranked = [h["key"] for h in hooks(packet, "zh")]
    assert ranked[:3] == ["near_liquidation", "top_of_sample", "adding_underwater"]
    assert hooks(packet, "zh", prefer=["adding_underwater"])[0]["key"] == "adding_underwater"
    assert "约1.4万" in hooks(packet, "zh")[1]["angle"]


class Writer:
    def __init__(self, body):
        self.body, self.requests = body, []

    def __call__(self, cfg, req):
        from nr.ai.providers.base import ChatResult
        self.requests.append({"system": req.system, "user": req.user})
        out = {**draft_output("zh", self.body, ["position.notional_usd", "crowd.same_side_rank"]),
               "thread": ["详情见 https://hypurrscan.io/address/" + WHALE], "lede_fact_ids": ["crowd.same_side_rank"],
               "why_now": "样本第二大", "open_question": "会继续加吗？"}
        return ChatResult(text=json.dumps(out, ensure_ascii=False), input_tokens=900, output_tokens=200,
                          stop_reason="end", http_status=200, latency_ms=5, model=cfg["model"])


def test_v3_writes_in_the_reporters_house_style_and_code_adds_the_link(sf):
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid = s.execute(text("SELECT id FROM news_event")).scalar()
        aid = assignment.create(s, eid, nr["zh"], source="trial", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        s.commit()
    writer = Writer("🚨 鲸鱼警报\n0xabab…ababab 的 $BTC 多单3000万美元\n在我们追踪的地址里排第2")
    generate_draft(sf, aid, NOW, transport=writer, prompt_version=3)
    req = writer.requests[0]
    data = json.loads(req["user"].split("<data>", 1)[1].split("</data>", 1)[0])
    assert data["style"]["name"] == "鲸鱼警报"  # reporter_breaking's house style, not the account's
    assert data["hooks"][0]["key"] == "top_of_sample" and "at most 120 characters" in req["user"]
    assert fact(data["fact_packet"], "change.recency")["display"]["zh"] == ["过去几小时"]
    with sf() as s:
        v = s.execute(text("SELECT thread, guard_report FROM article_version WHERE article_id = :a"), {"a": art}).one()
    assert v[0] == [f"地址：{WHALE}\nhttps://hypurrscan.io/address/{WHALE}"]  # the model's own link is replaced
    assert not [f for f in v[1]["findings"] if f["severity"] == "fail"], v[1]["findings"]
    assert char_budget({"limit": 280, "weighted": True}, "en", False) == 250


def test_v2_cards_draw_from_the_packet(sf, tmp_path):
    from nr.cards.render import render_for_packet
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000), ev(2, "increase", 300, 420, 101000)]
    ctx = context(positions=[position(szi=420, mark=101000, entry=98500, lev=20, liq=91000)])
    ctx["trader"]["alltime_pnl"] = "232980000"
    ctx["pnl_series"] = [{"t": (NOW - timedelta(days=300 - 10 * i)).isoformat(), "pnl": str(i * i * 260000 - 4e6),
                          "account_value": "1"} for i in range(30)]
    feed.contexts[WHALE] = ctx
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed, NOW)
    with sf() as s:
        pid = s.execute(text("SELECT current_fact_packet_id FROM news_event ORDER BY id DESC LIMIT 1")).scalar()
        pk = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": pid}).scalar()
    assert fact(pk, "trader.pnl_series") and nr
    for card in ("dashboard_card", "record_card", "crowd_card"):
        for lang in ("zh", "en"):
            out = render_for_packet(sf, pid, card, lang)
            with sf() as s:
                path = s.execute(text("SELECT file_path FROM media_asset WHERE id = :i"), {"i": out["assets"]["card_png"]}).scalar()
            png = open(path, "rb").read()
            assert png[:4] == b"\x89PNG" and len(png) > 20_000
            if lang == "zh":
                (tmp_path.parent / f"sample-{card}.png").write_bytes(png)
