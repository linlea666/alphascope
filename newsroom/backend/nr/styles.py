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
