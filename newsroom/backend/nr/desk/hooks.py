"""News angles ranked by deterministic code: what in this packet would make someone stop scrolling. The reporter
leads with the first unless its style prefers another kind. Each hook names the facts it rests on."""

from decimal import Decimal


def _v(facts: dict, fid: str) -> Decimal | None:
    f = facts.get(fid)
    try:
        return Decimal(str(f["value"])) if f else None
    except ArithmeticError:
        return None


def _d(facts: dict, fid: str, lang: str) -> str:
    return facts[fid]["display"][lang][0]


def hooks(packet: dict, language: str, memory: dict | None = None, prefer: list[str] | None = None) -> list[dict]:
    f = {x["id"]: x for x in packet["facts"]}
    cls, zh = packet["event"]["class"], language == "zh"
    out: list[dict] = []

    def add(key: str, strength: float, text_zh: str, text_en: str, ids: list[str]) -> None:
        out.append({"key": key, "strength": round(strength), "angle": text_zh if zh else text_en, "fact_ids": ids})

    if (pnl := _v(f, "trader.alltime_pnl_usd")) is not None and pnl >= 10_000_000:
        p = _d(f, "trader.alltime_pnl_usd", language)
        add("record", 90 if pnl >= 100_000_000 else 76, f"这是一位累计盈利 {p} 的交易员（排行榜口径，含浮盈）",
            f"a trader up {p} all-time (leaderboard, incl. unrealized)", ["trader.alltime_pnl_usd"])
    if (rank := _v(f, "crowd.same_side_rank")) is not None and rank <= 3:
        n = _d(f, "crowd.sample_traders", language) if "crowd.sample_traders" in f else ""
        add("top_of_sample", 86 - 4 * (int(rank) - 1), f"在我们追踪的{n}个地址里，这是同方向第 {int(rank)} 大的仓位",
            f"the #{int(rank)} position on this side among the {n} wallets we track", ["crowd.same_side_rank"]
            + (["crowd.sample_traders"] if n else []))
    for fid in ("contrast.vs_largest_prior_cycle", "radar.vs_largest_prior_peak"):
        if (vs := _v(f, fid)) is not None and vs >= Decimal("1.5"):
            add("personal_record", 70 + min(18, float(vs - Decimal("1.5")) * 8),
                f"这是他此前最大仓位的 {_d(f, fid, language)}", f"{_d(f, fid, language)} his largest before", [fid])
            break
    if (dist := _v(f, "position.liq_distance")) is not None and dist <= Decimal("0.03"):
        add("near_liquidation", 88 if dist <= Decimal("0.01") else 80,
            f"离强平价只有 {_d(f, 'position.liq_distance', language)}",
            f"only {_d(f, 'position.liq_distance', language)} from liquidation", ["position.liq_distance", "position.liq_px"])
    if cls == "underwater_add":
        add("adding_underwater", 74, "浮亏中还在加仓", "adding while underwater", ["position.unrealized_pnl_usd"])
    same, opp = _v(f, "crowd.timing_same_side"), _v(f, "crowd.timing_opposite_side")
    if same is not None and opp is not None and opp >= 2 * max(same, 1):
        add("against_timers", 78, f"择时高手大多站在他的对面（{int(opp)} 对 {int(same)}）",
            f"most proven timers are on the other side ({int(opp)} vs {int(same)})",
            ["crowd.timing_opposite_side", "crowd.timing_same_side"])
    if "book.opposite" in f:
        add("hedge", 62, "他同时持有反向大仓位，可能是对冲", "he also holds a big opposite position: maybe a hedge",
            ["book.opposite"])
    if (inc := _v(f, "episode.increases")) is not None and inc >= 3:
        add("keeps_adding", 66, f"已经连续加仓 {int(inc)} 次", f"{int(inc)} adds in a row", ["episode.increases"])
    if "contrast.last_same_side_net_usd" in f:
        net = _v(f, "contrast.last_same_side_net_usd") or Decimal(0)
        add("last_time", 72, f"上一次同方向的仓位{'赚了' if net > 0 else '亏了'} {_d(f, 'contrast.last_same_side_net_usd', language).lstrip('-')}",
            f"his last trade this way {'made' if net > 0 else 'lost'} {_d(f, 'contrast.last_same_side_net_usd', language).lstrip('-')}",
            ["contrast.last_same_side_net_usd", "contrast.last_same_side_held"])
    if (total := _v(f, "book.total_upl_usd")) is not None and total >= 500_000:
        winners = sum(1 for i in range(1, 6) if (_v(f, f"book.pos{i}.upl_usd") or 0) > 0)
        if winners >= 2:
            add("book_in_profit", 90 if total >= 2_000_000 else 78,
                f"他同时拿着 {winners} 个盈利仓位，合计浮盈 {_d(f, 'book.total_upl_usd', language)}",
                f"{winners} winning positions at once, up {_d(f, 'book.total_upl_usd', language)} unrealized",
                ["book.total_upl_usd"] + [f"book.pos{i}.upl_usd" for i in range(1, 6) if f"book.pos{i}.upl_usd" in f])
    if (opened := _v(f, "book.opened_24h")) is not None and opened >= 2:
        add("opened_many_today", 80, f"今天新开了 {int(opened)} 个仓位：{_d(f, 'book.opened_24h_coins', language)}",
            f"opened {int(opened)} positions today: {_d(f, 'book.opened_24h_coins', language)}",
            ["book.opened_24h", "book.opened_24h_coins"])
    if memory and memory.get("published"):
        add("sequel", 82, "这是我们报道过的故事的续集", "a sequel to a story we covered", [])
    for fid in ("position.notional_usd", "change.notional_usd"):
        if (n := _v(f, fid)) is not None:
            add("sheer_size", 72 if n >= 50_000_000 else 58 if n >= 10_000_000 else 40,
                f"规模 {_d(f, fid, language)}", f"size {_d(f, fid, language)}", [fid])
            break
    for h in out:  # a style's favourite kinds of story come first when they are there at all
        if prefer and h["key"] in prefer:
            h["strength"] += 15 - prefer.index(h["key"])
    return sorted(out, key=lambda h: -h["strength"])[:5]

