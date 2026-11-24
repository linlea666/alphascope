"""The editorial style library: twelve styles, each in Chinese and English. Styles are data (editorial_style, versioned);
these are the seeds. Every style shares the anti-boilerplate list; each adds its own structure and limits."""

COMMON_ZH = ["值得注意的是", "总而言之", "综上所述", "让我们", "不难看出", "众所周知", "一文读懂", "家人们", "重磅",
             "震惊", "史诗级", "炸裂", "梭哈", "必看"]
COMMON_EN = ["Let's dive in", "In conclusion", "It's worth noting", "game-changer", "to the moon", "insane",
             "BREAKING:", "smart money knows", "not financial advice but", "buckle up"]

# key, zh name, en name, use, length, depth (1-3), intensity (1-3), structure, extra avoid (zh, en)
SPECS = [
    ("breaking", "突发财经快讯", "Breaking markets news", "大额开仓、反手、强平风险等时效性事件", 200, 2, 2,
     ["事实一句", "规模与风险一句", "看点一句"], [], []),
    ("wire", "国际英文快讯", "Wire-style brief", "面向国际受众的标准快讯", 220, 2, 1,
     ["lead", "context", "what to watch"], [], []),
    ("derivatives", "专业衍生品分析", "Derivatives analysis", "杠杆、保证金估算、强平距离", 600, 3, 1,
     ["仓位与杠杆", "强平价与距离", "情景说明（不预测）"], ["爆仓了"], ["got rekt"]),
    ("data", "数据研究", "Data research", "排名、分布、罕见程度、样本说明", 800, 3, 1,
     ["发现", "数据与样本", "局限"], ["绝对", "一定"], ["proves", "always"]),
    ("suspense", "悬念故事", "Suspense narrative", "连续故事的开篇或续集，用事实制造悬念", 400, 1, 2,
     ["钩子（事实）", "已知", "未知与下一步观察"], ["即将爆仓", "马上崩盘"], ["about to blow up"]),
    ("profile", "交易员人物纪实", "Trader profile", "基于可核验战绩的人物纪实，不猜身份", 900, 2, 1,
     ["这是谁（只用公开昵称或短地址）", "做过什么（完整周期）", "这次有什么不同"], ["疑似某机构", "神秘大佬"],
     ["mystery fund", "insider"]),
    ("retrospective", "历史复盘", "Retrospective", "一段仓位或一个故事从开始到结局", 1200, 2, 1,
     ["当时我们知道什么", "之后发生了什么", "结局与教训（不归因）"], [], []),
    ("explainer", "新手教育", "Explainer for newcomers", "用一个真实事件解释一个概念", 700, 1, 1,
     ["概念一句话", "用这件事说明", "常见误解"], ["稳赚"], ["easy money"]),
    ("opinion", "鲜明观点评论", "Opinion with a clear view", "有立场的原创评论，观点与事实分开写", 600, 2, 3,
     ["事实", "观点（标明是观点）", "反方可能"], ["必涨", "必跌"], ["guaranteed"]),
    ("light", "轻松幽默", "Light and witty", "轻松但不失真，玩笑不碰数字", 220, 1, 2,
     ["一个有趣的事实", "一句俏皮话", "一句真实提醒"], ["哈哈哈哈"], ["lmao"]),
    ("investigation", "长篇调查", "Long-form investigation", "多事件、多数据源的深度调查（thread 或长文）", 2500, 3, 1,
     ["问题", "证据链", "能证明什么、不能证明什么", "方法与样本"], [], []),
    ("weekly", "多事件周报", "Weekly round-up", "一周多个事件的汇总与回看", 1500, 2, 1,
     ["本周要点", "逐条（含后续结果）", "下周观察"], [], []),
]


def _style(lang: str, spec) -> dict:
    key, zh, en, use, length, depth, intensity, structure, avoid_zh, avoid_en = spec
    zh_lang = lang == "zh"
    guide = (f"适用：{use}。篇幅约 {length} 字以内；术语深度 {depth}/3；情绪强度 {intensity}/3。"
             "所有数字照抄事实包；先事实后观点；不喊单、不夸张。" if zh_lang else
             f"Use: {use}. Up to about {length} characters; jargon depth {depth}/3; intensity {intensity}/3. "
             "Copy every number from the fact packet; facts before views; no calls to trade, no hype.")
    return {"key": f"{lang}_{key}", "language": lang, "name": zh if zh_lang else en, "guide": guide,
            "structure": {"sections": structure, "max_length": length, "depth": depth, "intensity": intensity},
            "forbidden": (COMMON_ZH + avoid_zh) if zh_lang else (COMMON_EN + avoid_en)}


LIBRARY = [_style(lang, spec) for spec in SPECS for lang in ("zh", "en")]


# ---- house voices for X (2026-11): what the accounts people actually follow do ----------------------------------
# Each has its own avoid list, a structure the prompt reads and examples whose {placeholders} show shape, never facts.
# The default reporter for each is in settings "styles.by_reporter".

def _house(key, language, name, guide, structure, examples, avoid=()):
    common = COMMON_ZH if language == "zh" else COMMON_EN
    return {"key": f"{language}_{key}", "language": language, "name": name, "guide": guide,
            "structure": structure, "examples": examples,
            "forbidden": [w for w in common if w not in structure.get("allow", [])] + list(avoid)}


HOUSE = [
    _house("whale_alert", "zh", "鲸鱼警报",
           "像 X 上最受欢迎的巨鲸快讯号那样写。第一行就是钩子（🚨鲸鱼警报 / 突发 / 刚刚），接着两三行、每行一个事实，"
           "最后一句反问或看点。只挑 2–3 个最有冲击力的数字，全部取整。少形容词，多反差。",
           {"lines": "3-5", "emoji": "0-2，如 🚨🐋", "cashtag": True, "closing": "反问或看点",
            "prefer_hooks": ["record", "top_of_sample", "personal_record", "sheer_size"]},
           ["🚨 鲸鱼警报\n\n{短地址} 刚刚把 $BTC 空单加到 {金额}\n在我们追踪的 {地址数} 个地址里排第 {名次}\n\n他累计赚了 {累计盈利}。\n\n他知道些什么？",
            "突发：一头巨鲸刚刚开出 {金额} 的 $ETH 多单，{杠杆} 杠杆\n\n强平价 {强平价}，离现价只有 {距离}\n\n这次扛得住吗？"]),
    _house("degen", "zh", "赌鲸江湖",
           "口语、带情绪，像币圈老哥围观：『刚刚，巨鲸又上头了』『拉满』『赌狗』都可以用，但每个数字都必须对。"
           "重点写杠杆、强平价、浮盈浮亏的刺激感。说本金或保证金时只能用估算值，并带「约」。",
           {"lines": "3-5", "emoji": "0-2，如 🙈🔥", "cashtag": True, "closing": "调侃或反问",
            "prefer_hooks": ["near_liquidation", "adding_underwater", "sheer_size", "record"], "allow": ["梭哈"]},
           ["刚刚，巨鲸又上头了\n\n用约 {保证金估算} 拉满，开出 {金额} $BTC 多单\n{杠杆} 全仓，爆仓价 {强平价}\n\n赌狗的快乐你想象不到",
            "{短地址} 浮亏 {浮亏} 还在加 $ETH 空单 🙈\n\n仓位已经 {金额}，离强平只剩 {距离}\n\n头铁还是有底牌？"]),
    _house("dossier", "zh", "追踪档案",
           "像追一个人的连续剧：先交代他过去做过什么（战绩、上一笔怎么结束），再写这次做了什么，最后留一个悬念。"
           "历史只用档案里有的事实，并带上「自某月以来」这样的范围。",
           {"lines": "4-6", "emoji": "0-1", "cashtag": True, "closing": "悬念",
            "prefer_hooks": ["last_time", "sequel", "record", "keeps_adding", "personal_record"]},
           ["{短地址} 又回来了。\n\n上一次他做多 $BTC，拿了 {时长}，净赚 {盈利}。\n\n现在他反手做空 {金额}，{杠杆}。\n\n这次还能赢吗？",
            "时隔几天，这个地址再次加仓 $SOL。\n\n自 {日期} 以来他在 SOL 上开过 {次数} 次仓，这次最大：{金额}。\n现浮亏 {浮亏}。"]),
    _house("crowd", "zh", "聪明钱对赌",
           "讲对立：这头巨鲸站在哪边，我们追踪的其他人、尤其是择时高手站在哪边。用排名和人数制造反差，最后问谁会是对的。"
           "一定要说「我们追踪的地址里」，不能说全网。",
           {"lines": "4-5", "emoji": "0-1，如 ⚔️", "cashtag": True, "closing": "谁会是对的",
            "prefer_hooks": ["against_timers", "top_of_sample", "hedge"]},
           ["巨鲸 vs 择时高手 ⚔️\n\n{短地址} 刚刚把 $ETH 空单加到 {金额}，在我们追踪的地址里排第 {名次}\n\n"
            "但 {人数} 位择时高手里，{多头人数} 位在做多\n\n谁会是对的？"]),
    _house("outcome", "zh", "结局复盘",
           "写一个仓位的结局或续集：之前怎样，现在怎样。没有发布过的前情时，就用档案里上一笔同方向的结局做对照。"
           "不预测，不归因。",
           {"lines": "3-5", "emoji": "0-1", "cashtag": True, "closing": "一句看点",
            "prefer_hooks": ["sequel", "last_time"]},
           ["还记得那笔 {金额} 的 $BTC 空单吗？\n\n现在：{现状}\n\n{一句看点}"]),
    _house("one_liner", "zh", "一句话快讯",
           "一到两行，让配图说话。只写最硬的一个事实和一个数字。", {"lines": "1-2", "emoji": "0-1，如 🐋", "cashtag": True,
                                                    "closing": "无", "prefer_hooks": ["sheer_size", "top_of_sample"]},
           ["🐋 {短地址} 刚刚开出 {金额} $HYPE 空单，{杠杆}"]),
    _house("whale_alert", "en", "Whale alert",
           "Write like the whale-alert accounts people follow: a hook line (🚨 WHALE ALERT / BREAKING / JUST IN), two or "
           "three short lines with one fact each, then a question or what to watch. Two or three round numbers only.",
           {"lines": "3-5", "emoji": "0-2, e.g. 🚨🐋", "cashtag": True, "closing": "question",
            "prefer_hooks": ["record", "top_of_sample", "personal_record", "sheer_size"], "allow": ["BREAKING:"]},
           ["🚨 WHALE ALERT\n\n{short address} just took his $BTC short to {amount}\nThat's #{rank} among the {N} wallets "
            "we track.\n\nHe's up {all-time pnl} all-time.\n\nDoes he know something?"]),
    _house("degen", "en", "Degen desk",
           "Casual and punchy, like CT watching a high-stakes trade: 'just went full degen', 'max leverage'. Every "
           "number exact; margin only as the estimate with '~'.",
           {"lines": "3-5", "emoji": "0-2", "cashtag": True, "closing": "quip or question",
            "prefer_hooks": ["near_liquidation", "adding_underwater", "sheer_size", "record"]},
           ["This whale just went full degen\n\n~{margin} margin → {amount} $BTC long at {leverage}\nLiquidation at "
            "{liq price}\n\nIron hands or about to get rekt?"]),
    _house("dossier", "en", "Trader dossier",
           "Tell it as a series: what he did before (record, how the last trade ended), what he is doing now, and a "
           "cliffhanger. History only from the archive, with its range ('since March').",
           {"lines": "4-6", "emoji": "0-1", "cashtag": True, "closing": "cliffhanger",
            "prefer_hooks": ["last_time", "sequel", "record", "keeps_adding", "personal_record"]},
           ["{short address} is back.\n\nLast time he went long $BTC for {duration} and made {profit}.\n\nNow he's "
            "short {amount} at {leverage}.\n\nCan he do it again?"]),
    _house("crowd", "en", "Smart money vs whale",
           "Make it a face-off: which side the whale is on, which side the other wallets we track and the proven "
           "timers are on. Always 'among the wallets we track', never 'the market'.",
           {"lines": "4-5", "emoji": "0-1", "cashtag": True, "closing": "who's right",
            "prefer_hooks": ["against_timers", "top_of_sample", "hedge"]},
           ["Whale vs proven timers ⚔️\n\n{short address} is now #{rank} $ETH short among the wallets we track\n\n"
            "But {n} of {N} proven timers are long\n\nWho's right?"]),
    _house("outcome", "en", "How it ended",
           "The ending or the next chapter of a position: then and now. Without an earlier post, compare with how his "
           "last trade this way ended. No predictions, no blame.",
           {"lines": "3-5", "emoji": "0-1", "cashtag": True, "closing": "what to watch",
            "prefer_hooks": ["sequel", "last_time"]},
           ["Remember that {amount} $BTC short?\n\nNow: {status}\n\n{what to watch}"]),
    _house("one_liner", "en", "One-liner",
           "One or two lines and let the card speak: the single hardest fact and one number.",
           {"lines": "1-2", "emoji": "0-1", "cashtag": True, "closing": "none", "prefer_hooks": ["sheer_size", "top_of_sample"]},
           ["🐋 {short address} just opened a {amount} $HYPE short at {leverage}"]),
]


# ---- 2026-11 additions: a Lookonchain-style book breakdown, and English voices written the way CT posts read ------
# New keys / new versions only: seeded styles are never edited in place.

HOUSE_V2 = [
    _house("portfolio", "zh", "持仓清单",
           "像链上数据号那样把一个人的整盘仓位摊开：第一句总结他今天做了什么、整体赚亏多少（用 book.total_upl_usd 和 "
           "book.opened_24h，「今天」只能在 book.opened_24h 支撑时写）；中间每个仓位一行「$币种：±浮盈亏」；链接系统会加在最后一行。",
           {"lines": "4-7", "emoji": "0-1", "cashtag": True, "closing": "无，最后一行是链接", "link_in_body": True,
            "prefer_hooks": ["book_in_profit", "opened_many_today", "hedge"]},
           ["这个交易员今天开了 $NVDA、$BTC、$ETH 多单，浮盈已经 {合计浮盈}！\n\n$NVDA：+{浮盈1}\n$ETH：+{浮盈2}\n$BTC：+{浮盈3}"]),
    _house("portfolio", "en", "Book breakdown",
           "Lay the whole book out the way on-chain trackers do: line one sums up what he did and where he stands "
           "(book.total_upl_usd, book.opened_24h - only say 'today' when book.opened_24h backs it), then one line per "
           "position '$COIN: +$X', most profitable first. The system puts the link on the last line.",
           {"lines": "4-7", "emoji": "0-1", "cashtag": True, "closing": "none, the link closes it", "link_in_body": True,
            "prefer_hooks": ["book_in_profit", "opened_many_today", "hedge"]},
           ["This trader opened longs on $NVDA, $BTC, and $ETH today and is already up {total} in unrealized profit!\n\n"
            "$NVDA: +{pnl1}\n$ETH: +{pnl2}\n$BTC: +{pnl3}"]),
]
# The squares (OKX / Binance Square, docs/16): Chinese, about people. "梭哈" is how these readers talk, so it is allowed.
SQUARE = [
    _house("square_story", "zh", "人物故事",
           "写一个交易员故事里的一集：先用人设和阶段定调（梭哈型、稳健型、正在巅峰 / 危机 / 逆袭），第一行人名加反差或悬念，"
           "中间把这次发生了什么和他之前的高光或翻车连起来，结尾按 data.closing 抛一个让人想回的问题。",
           {"lines": "4-7", "emoji": "1-3", "cashtag": True, "closing": "按 data.closing：站队 / 预测 / 投票 / 求补充",
            "allow": ["梭哈"], "prefer_hooks": ["stage_turn", "streak", "vs_last"]},
           ["{昵称}又上头了。\n\n30 天收益 {收益率}，刚又开了 {金额} 的 $ETH 空单，{杠杆} 倍。\n"
            "上一笔 $ETH 空单他赚了 {盈利}。\n\n这次还能吃到吗？你跟不跟？",
            "从 30 天翻 {倍数} 倍到回吐 {回撤}，{昵称}只用了一周。\n\n{过程一行}\n{现状一行}\n\n是洗盘还是陨落？评论区说说"]),
    _house("square_board", "zh", "榜单盘点",
           "把榜单写成人：一条讲 3–5 个交易员，每人一行「昵称：倍数 / 收益额 / 名次变化」，最后挑最有戏的那个多说一句。"
           "带单资产很小的要写出资产规模，不让人以为是大资金。",
           {"lines": "5-8", "emoji": "1-3", "cashtag": False, "closing": "投票：你最想跟谁 / 谁下周还在榜上",
            "allow": ["梭哈"], "prefer_hooks": ["board_top", "new_entry"]},
           ["OKX 7 天收益榜，前五名都翻了倍👇\n\n{昵称1}：{倍数1} 倍，赚 {收益1}\n{昵称2}：{倍数2} 倍，排名 ↑{名次}\n"
            "{昵称3}：新上榜，{倍数3} 倍\n\n{昵称1} 的带单资产只有 {资产}，你敢跟吗？"]),
    _house("square_fall", "zh", "陨落 / 逆袭",
           "强反差：先写他最猛的时候（倍数、名次、收益），再写现在（回撤多少、掉出榜单、爆仓，或者从谷底爬回来），"
           "一句点出转折在哪。不幸灾乐祸，不下结论，让读者判断。",
           {"lines": "4-6", "emoji": "0-2", "cashtag": True, "closing": "预测：还能回来吗 / 会不会继续跌",
            "allow": ["梭哈"], "prefer_hooks": ["stage_turn", "off_peak"]},
           ["一个月前，{昵称}是 OKX 30 天榜第 {名次}，翻了 {倍数} 倍。\n\n现在：从高点回撤 {回撤}，已经掉出前 100。\n\n"
            "天才陨落，还是只是休息？"]),
    _house("square_star", "zh", "明星追踪",
           "追星式报道：读者认识他，开头直接叫名字说动作和金额（「{昵称}刚开了…」「{昵称}平掉…进账…」），"
           "接一句和他上一笔或近期战绩的对比（连胜、24 小时盈亏、30 天收益率），结尾问读者怎么看。",
           {"lines": "3-6", "emoji": "1-2", "cashtag": True, "closing": "站队或预测",
            "allow": ["梭哈"], "prefer_hooks": ["vs_last", "streak"]},
           ["{昵称}刚开了约 {金额} 的 $ETH 空单，{杠杆} 倍杠杆。\n\n他上一笔 $BTC 多单刚赚了 {盈利}，已经 {N} 连胜。\n\n这次还跟吗？",
            "{昵称}平掉 $ETH 空单，进账 {盈利} 💰\n\n拿了 {时长}，24 小时一共赚了 {合计}。\n\n下一单你猜他做多还是做空？"]),
]
# English house voices, second version: the hooks and closers CT actually uses (WHALE ALERT!!, BREAKING:, JUST IN:)
HOUSE_EN_V2 = {
    "en_whale_alert": ["WHALE ALERT!!\n\nThis whale just opened a ${N} MILLION $BTC long\n\nLiquidation at {liq price}"
                       "\n\nDoes he know something??",
                       "💥BREAKING:\n\nThis whale just opened a {leverage} $BTC short worth {amount}!\n\nWhat does he know?",
                       "JUST IN: A whale just opened a massive $ETH long worth {amount} with {leverage} leverage."],
    "en_degen": ["This whale just went full degen 🎰\n\n~{margin} margin → {amount} $BTC long at {leverage}\nLiquidation: "
                 "{liq price}, just {distance} away\n\nIron hands or about to get rekt?"],
    "en_dossier": ["{short address} is back.\n\nLast time he went long $BTC, held {duration} and walked away up {profit}."
                   "\n\nNow he's short {amount} at {leverage}.\n\nCan he do it twice?"],
    "en_crowd": ["Whale vs the smart money ⚔️\n\n{short address} is now the #{rank} $ETH short among the ~{N} wallets we "
                 "track\n\nBut {n} of the proven timers are long\n\nWho's right?"],
}
