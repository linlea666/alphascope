"""Idempotent seed data: two example accounts (paused until someone authorizes them), their styles, the version 1
prompts and the two Phase 1 agents. Real positioning, prompts and handles are edited in the app, not in this file
(the repository is public)."""

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.ai.prompts import SEEDS, SEEDS_V2, SEEDS_V3
from nr.ai.prompts_review import SEEDS_REVIEW
from nr.ai.prompts_square import SEEDS_SQUARE
from nr.ai.prompts_v4 import SEEDS_V4, SEEDS_V5
from nr.styles import HOUSE, HOUSE_EN_V2, HOUSE_V2, LIBRARY, SQUARE

STYLES = [
    {"key": "zh_data_explainer", "language": "zh", "name": "中文数据解读",
     "guide": "面向中文加密交易者。先给事实，再给一句数据背景，最后点出风险或看点。短句，口语化但专业；"
              "不夸张、不喊单、不用感叹号堆砌。数字照抄事实包的展示写法。",
     "structure": {"single_post": ["事实一句", "背景/对比一句", "风险或后续看点一句"]},
     "forbidden": ["值得注意的是", "总而言之", "综上所述", "让我们", "震惊", "重磅", "史诗级", "一文读懂", "家人们", "梭哈"]},
    {"key": "en_market_desk", "language": "en", "name": "Global market desk",
     "guide": "For an international crypto audience. Lead with the verified fact, then the context that makes it "
              "unusual, then the risk to watch. Wire-service tone: plain verbs, no hype, no emoji walls.",
     "structure": {"single_post": ["lead fact", "why it is unusual", "what to watch"]},
     "forbidden": ["Let's dive in", "In conclusion", "game-changer", "to the moon", "massive", "insane", "BREAKING:",
                   "smart money knows", "not financial advice but"]},
]
ACCOUNTS = [
    {"handle": "alphascope_cn", "display_name": "AlphaScope 中文", "language": "zh", "timezone": "Asia/Shanghai",
     "style_key": "zh_data_explainer", "venues": ["hyperliquid"],
     "positioning": "中文加密交易者的 Hyperliquid 巨鲸与衍生品数据解读：只讲链上可核验的数据。",
     "audience_profile": {"region": "中文社区", "depth": "进阶", "interests": ["巨鲸", "杠杆与强平", "交易员档案"]}},
    {"handle": "alphascope_global", "display_name": "AlphaScope Global", "language": "en", "timezone": "UTC",
     "style_key": "en_market_desk", "venues": ["hyperliquid"],
     "positioning": "Verified on-chain whale and positioning data from an observed sample of Hyperliquid traders, "
                    "for a global audience.",
     "audience_profile": {"region": "global", "depth": "intermediate", "interests": ["whales", "derivatives risk"]}},
    # OKX data can disappear at any time; OKX material goes to OKX's own Square, never to the X accounts
    {"handle": "alphascope_okx_square", "display_name": "AlphaScope · OKX 广场", "language": "zh",
     "timezone": "Asia/Shanghai", "style_key": "zh_square_story", "platform": "okx_square", "venues": ["okx"],
     "long_post_allowed": True,
     "positioning": "OKX 带单员的可核验数据：榜单与真实可跟单收益、离榜之后、强平与风险。只发在 OKX 广场。",
     "audience_profile": {"region": "OKX 用户", "depth": "进阶", "interests": ["带单员", "跟单收益", "风险"]}},
    # Binance lead traders via the kolcue relay (docs/16): the star list's stories, only on Binance Square
    {"handle": "alphascope_binance_square", "display_name": "AlphaScope · 币安广场", "language": "zh",
     "timezone": "Asia/Shanghai", "style_key": "zh_square_star", "platform": "binance_square", "venues": ["binance"],
     "long_post_allowed": True,
     "positioning": "币安明星带单员的追踪报道：开仓、平仓、战绩和起落，像追连续剧一样追人。只发在币安广场。",
     "audience_profile": {"region": "币安用户", "depth": "入门到进阶", "interests": ["带单员", "明星交易员", "跟单"]}},
]
AGENTS = [
    {"agent_key": "eic", "display_name": "AI 总编辑 / Editor-in-chief",
     "role_description": "Chooses what to cover, explains why, proposes accounts and angles, plans follow-ups.",
     "max_output_tokens": 2500, "budget": "0.30"},
    {"agent_key": "reporter", "display_name": "AI 记者（通用） / Reporter",
     "role_description": "Writes one account's draft from the fact packet and the assigned angle. Other reporter "
                         "roles use this role's models unless they have their own.",
     "max_output_tokens": 2500, "budget": "0.30"},
    {"agent_key": "reporter_breaking", "display_name": "突发新闻记者 / Breaking news",
     "role_description": "Large opens, flips, sudden size changes: fast, precise, fact first."},
    {"agent_key": "reporter_investigator", "display_name": "巨鲸调查记者 / Whale investigator",
     "role_description": "A trader's verified record, past big trades and habits; no identity guessing."},
    {"agent_key": "reporter_data", "display_name": "数据研究记者 / Data researcher",
     "role_description": "Rarity, rankings, distributions and samples; columns and research pieces."},
    {"agent_key": "reporter_derivatives", "display_name": "衍生品风险记者 / Derivatives risk",
     "role_description": "Leverage, estimated margin, liquidation distance; never announces unconfirmed liquidations."},
    {"agent_key": "reporter_historian", "display_name": "历史故事记者 / Story historian",
     "role_description": "Running stories, recaps and trader dossiers, chapter by chapter."},
    {"agent_key": "reporter_star", "display_name": "明星追踪记者 / Star tracker",
     "role_description": "Follows the star traders move by move on the squares: opens, closes and their results, "
                         "streaks, the account's 30-day record."},
    {"agent_key": "reporter_square", "display_name": "广场故事记者 / Square storyteller",
     "role_description": "Turns boards and traders into character stories for OKX and Binance Square: multiples, "
                         "rises and falls, sequels."},
    {"agent_key": "editor_zh", "display_name": "中文市场编辑 / Chinese desk editor",
     "role_description": "Localizes and polishes a draft for Chinese readers without new claims."},
    {"agent_key": "editor_en", "display_name": "全球英文编辑 / Global English editor",
     "role_description": "Localizes and polishes a draft for an international audience without new claims."},
    {"agent_key": "fact_checker", "display_name": "事实核查员 / Fact checker",
     "role_description": "Rewrites a draft to clear the claims guard's findings; cannot change rules or facts."},
    {"agent_key": "reviewer", "display_name": "AI 审稿编辑 / Desk reviewer",
     "role_description": "Reads a fact-checked draft like a native reader: AI tone, hook, clarity, style; approves, asks "
                         "for a revision or sends it to a person.", "max_output_tokens": 1500, "budget": "0.10"},
]


def seed(s: Session) -> dict:
    out = {"styles": 0, "accounts": 0, "prompts": 0, "agents": 0}
    for st in STYLES + LIBRARY + HOUSE + HOUSE_V2 + SQUARE:
        out["styles"] += s.execute(text("""
            INSERT INTO editorial_style (key, version, language, name, guide, structure, forbidden, examples)
            VALUES (:k, 1, :l, :n, :g, CAST(:s AS jsonb), CAST(:f AS jsonb), CAST(:e AS jsonb)) ON CONFLICT DO NOTHING"""),
            {"k": st["key"], "l": st["language"], "n": st["name"], "g": st["guide"],
             "s": json.dumps(st["structure"], ensure_ascii=False), "f": json.dumps(st["forbidden"], ensure_ascii=False),
             "e": json.dumps(st.get("examples", []), ensure_ascii=False)}
        ).rowcount
    for key, examples in HOUSE_EN_V2.items():  # version 2 of a seeded voice: same guide, the examples CT reads
        out["styles"] += s.execute(text("""
            INSERT INTO editorial_style (key, version, language, name, guide, structure, forbidden, examples)
            SELECT key, 2, language, name, guide, structure, forbidden, CAST(:e AS jsonb) FROM editorial_style
            WHERE key = :k AND version = 1 ON CONFLICT DO NOTHING"""),
            {"k": key, "e": json.dumps(examples, ensure_ascii=False)}).rowcount
    for a in ACCOUNTS:
        out["accounts"] += s.execute(text("""
            INSERT INTO editorial_account (handle, display_name, language, timezone, style_key, positioning,
                                           audience_profile, status, brand, platform, venues, long_post_allowed)
            VALUES (:h, :d, :l, :tz, :st, :p, CAST(:ap AS jsonb), 'paused', 'AlphaScope', :pf, :v, :lp)
            ON CONFLICT DO NOTHING"""),
            {"h": a["handle"], "d": a["display_name"], "l": a["language"], "tz": a["timezone"], "st": a["style_key"],
             "p": a["positioning"], "ap": json.dumps(a["audience_profile"], ensure_ascii=False),
             "pf": a.get("platform", "x"), "v": a["venues"], "lp": a.get("long_post_allowed", False)}).rowcount
    prompts = [(k, p["version"], None, p["system"], p["user"]) for k, p in
               [*SEEDS.items(), *SEEDS_V2.items(), *SEEDS_V3.items()]] + SEEDS_V4 + SEEDS_V5 + SEEDS_REVIEW + SEEDS_SQUARE
    for key, version, language, system, user in prompts:
        out["prompts"] += s.execute(text("""
            INSERT INTO prompt_template (key, version, language, system_text, user_template, sha256, notes)
            VALUES (:k, :v, :l, :s, :u, :h, 'seed') ON CONFLICT DO NOTHING"""),
            {"k": key, "v": version, "l": language, "s": system, "u": user,
             "h": hashlib.sha256((system + user).encode()).hexdigest()}).rowcount
    for ag in AGENTS:
        exists = s.execute(text("SELECT 1 FROM ai_agent_profile WHERE agent_key = :k"), {"k": ag["agent_key"]}).scalar()
        if not exists:
            s.execute(text("""
                INSERT INTO ai_agent_profile (agent_key, version, display_name, role_description, prompt_key,
                                              prompt_version, max_output_tokens, budget_usd_per_task)
                VALUES (:k, 1, :d, :r, :k, 1, :m, :b)"""),
                {"k": ag["agent_key"], "d": ag["display_name"], "r": ag["role_description"],
                 "m": ag.get("max_output_tokens", 2500), "b": ag.get("budget", "0.30")})
            out["agents"] += 1
    return out
