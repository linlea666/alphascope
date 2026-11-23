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
    assert codes(phrases.check("爆仓价6.35万，离现价只剩2.69%。", packet, [])) == []  # a price, not an event


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
        out = {**draft_output(getattr(self, "lang", "zh"), self.body, ["position.notional_usd", "crowd.same_side_rank"]),
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


def test_v4_texts_follow_the_accounts_language_and_desk_notes_the_desk_language(sf):
    from nr.ai.runner import resolve_prompt
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed, NOW)
    with sf() as s:
        assert resolve_prompt(s, "reporter", 4, "zh")["language"] == "zh"
        assert resolve_prompt(s, "reporter", 4, "en")["language"] == "en"
        assert resolve_prompt(s, "reporter", 3, "en")["language"] is None  # no language text: the shared one
        assert resolve_prompt(s, "eic", 3, "en")["language"] == "zh"  # only Chinese exists: still runs
        eid = s.execute(text("SELECT id FROM news_event")).scalar()
        ids = {}
        for lang in ("zh", "en"):
            aid = assignment.create(s, eid, nr[lang], source="trial", status="accepted", user_id=nr["chief"])
            assignment.accept(s, aid, nr["chief"])
            ids[lang] = aid
        s.commit()
    for lang, body in (("zh", "🚨 鲸鱼警报\n巨鲸 $BTC 多单3000万美元"), ("en", "WHALE ALERT!!\nThis whale is long $30 MILLION $BTC")):
        w = Writer(body)
        w.lang = lang
        generate_draft(sf, ids[lang], NOW, transport=w, prompt_version=5)
        req = w.requests[0]
        assert ("你给一个加密货币 X 账号写帖子" in req["system"]) == (lang == "zh")
        assert ("crypto Twitter" in req["system"] or "CT actually follows" in req["system"]) == (lang == "en")
        assert ("用中文写" in req["user"]) if lang == "zh" else ("in\nChinese" in req["user"] or "in Chinese" in req["user"])
    with sf() as s:
        refs = sorted(s.execute(text("SELECT prompt_ref FROM article_version")).scalars())
        guard = s.execute(text("SELECT guard_status FROM article_version WHERE prompt_ref LIKE '%.en'")).scalar()
    assert refs == ["reporter_breaking@v5.en", "reporter_breaking@v5.zh"] and guard == "pass"  # "$30 MILLION" reads


def test_book_breakdown_style_lists_positions_puts_the_link_last_and_draws_the_book(sf, tmp_path):
    from nr.cards.render import render_for_packet
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "first_seen", 0, 10, 100000, observed=NOW - timedelta(days=5)),
                   ev(2, "open", 0, 102, 63500, entry=60400, observed=NOW - timedelta(hours=1))]
    recent = (NOW - timedelta(hours=5)).isoformat()
    positions = [position(coin="xyz:NVDA", szi=37330, mark=165, entry=131.74, lev=10, liq=None, upl=1241469),
                 position(coin="ETH", szi=4410, mark=1804.66, entry=1702.14, lev=20, liq=None, upl=452189),
                 position(szi=102.52, mark=63525, entry=60427, lev=20, liq=None, upl=317604)]
    for p in positions:
        p["first_seen_at"] = recent
    feed.contexts[WHALE] = context(positions=positions)
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid = s.execute(text("SELECT id FROM news_event WHERE event_class = 'whale_open'")).scalar()
        pid = s.execute(text("SELECT current_fact_packet_id FROM news_event WHERE id = :e"), {"e": eid}).scalar()
        pk = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": pid}).scalar()
        aid = assignment.create(s, eid, nr["en"], source="trial", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        s.commit()
    assert fact(pk, "book.pos1.coin")["value"] == "$NVDA" and fact(pk, "book.opened_24h")["value"] == 3
    assert fact(pk, "book.total_upl_usd")["display"]["en"][0] == "$2.01M"
    body = ("This trader opened longs on $NVDA, $ETH, and $BTC today and is already up $2M in unrealized profit!\n\n"
            "$NVDA: +$1.24M\n$ETH: +$452K\n$BTC: +$318K\n" + f"hypurrscan.io/address/{WHALE}")
    w = Writer(body)
    w.lang = "en"
    generate_draft(sf, aid, NOW, transport=w, prompt_version=5, style_key="en_portfolio")
    data = json.loads(w.requests[0]["user"].split("<data>", 1)[1].split("</data>", 1)[0])
    assert data["hooks"][0]["key"] == "book_in_profit"
    with sf() as s:
        v = s.execute(text("SELECT body, thread, guard_status, guard_report, card_template FROM article_version "
                           "WHERE article_id = :a"), {"a": art}).one()
    assert v[0].endswith(f"\n\nhttps://hypurrscan.io/address/{WHALE}") and v[0].count("hypurrscan") == 1
    assert not v[1] or all("hypurrscan" not in t for t in v[1])  # the link is in the body, not the thread too
    assert v[2] == "pass", v[3]["findings"]
    assert v[4] == "book_card"
    out = render_for_packet(sf, pid, "book_card", "en")
    with sf() as s:
        path = s.execute(text("SELECT file_path FROM media_asset WHERE id = :i"), {"i": out["assets"]["card_png"]}).scalar()
    (tmp_path.parent / "sample-book_card.png").write_bytes(open(path, "rb").read())


def test_pnl_direction_and_estimate_markers():
    packet = {"event": {"class": "whale_add"}, "constraints": [], "facts": [
        {"id": "position.unrealized_pnl_usd", "kind": "unrealized_pnl", "value": "100400", "display": fmt.usd(Decimal("100400"))},
        {"id": "position.implied_margin_usd", "kind": "estimate", "value": "411000", "display": fmt.usd(Decimal("411000"))}]}
    assert codes(phrases.check("Already $100.4K underwater on the position.", packet, [])) == ["PNL_SIGN"]
    assert codes(phrases.check("Up $100.4K unrealized.", packet, [])) == []
    assert codes(phrases.check("$BTC: -$100.4K unrealized", packet, [])) == ["PNL_SIGN"]
    assert codes(phrases.check("浮亏10万美元还在加仓。", packet, [])) == ["PNL_SIGN"]
    assert codes(phrases.check("浮盈10万美元。", packet, [])) == []
    assert codes(phrases.check("~$411K est. margin behind it.", packet, [])) == []


def test_time_spans_from_recency_are_not_figures():
    from nr import settings_store
    allow = settings_store.DEFAULTS["guard"]["allow_numbers"]
    assert codes(numbers.check("2,250 ETH gone in the past 24 hours.", FACTS, allow)) == ["UNMATCHED_NUMBER"]  # 2,250 isn't a fact
    assert codes(numbers.check("Closed in the past 24 hours, 267 BTC left.", FACTS, allow)) == []
    assert codes(numbers.check("过去24小时加到267枚BTC。", FACTS, allow)) == []
