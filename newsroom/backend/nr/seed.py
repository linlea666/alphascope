"""Idempotent seed data: two example accounts (paused until someone authorizes them), their styles, the version 1
prompts and the two Phase 1 agents. Real positioning, prompts and handles are edited in the app, not in this file
(the repository is public)."""

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.ai.prompts import SEEDS
from nr.styles import LIBRARY

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
     "style_key": "zh_data_explainer", "positioning": "中文加密交易者的巨鲸与衍生品数据解读：只讲可核验的数据。",
     "audience_profile": {"region": "中文社区", "depth": "进阶", "interests": ["巨鲸", "杠杆与强平", "交易员档案"]}},
    {"handle": "alphascope_global", "display_name": "AlphaScope Global", "language": "en", "timezone": "UTC",
     "style_key": "en_market_desk", "positioning": "Verified whale and positioning data from an observed sample of "
                                                   "Hyperliquid and OKX traders, for a global audience.",
     "audience_profile": {"region": "global", "depth": "intermediate", "interests": ["whales", "derivatives risk"]}},
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
    {"agent_key": "editor_zh", "display_name": "中文市场编辑 / Chinese desk editor",
     "role_description": "Localizes and polishes a draft for Chinese readers without new claims."},
    {"agent_key": "editor_en", "display_name": "全球英文编辑 / Global English editor",
     "role_description": "Localizes and polishes a draft for an international audience without new claims."},
    {"agent_key": "fact_checker", "display_name": "事实核查员 / Fact checker",
     "role_description": "Rewrites a draft to clear the claims guard's findings; cannot change rules or facts."},
]


def seed(s: Session) -> dict:
    out = {"styles": 0, "accounts": 0, "prompts": 0, "agents": 0}
    for st in STYLES + LIBRARY:
        out["styles"] += s.execute(text("""
            INSERT INTO editorial_style (key, version, language, name, guide, structure, forbidden)
            VALUES (:k, 1, :l, :n, :g, CAST(:s AS jsonb), CAST(:f AS jsonb)) ON CONFLICT DO NOTHING"""),
            {"k": st["key"], "l": st["language"], "n": st["name"], "g": st["guide"],
             "s": json.dumps(st["structure"], ensure_ascii=False), "f": json.dumps(st["forbidden"], ensure_ascii=False)}
        ).rowcount
    for a in ACCOUNTS:
        out["accounts"] += s.execute(text("""
            INSERT INTO editorial_account (handle, display_name, language, timezone, style_key, positioning,
                                           audience_profile, status, brand)
            VALUES (:h, :d, :l, :tz, :st, :p, CAST(:ap AS jsonb), 'paused', 'AlphaScope') ON CONFLICT DO NOTHING"""),
            {"h": a["handle"], "d": a["display_name"], "l": a["language"], "tz": a["timezone"], "st": a["style_key"],
             "p": a["positioning"], "ap": json.dumps(a["audience_profile"], ensure_ascii=False)}).rowcount
    for key, p in SEEDS.items():
        out["prompts"] += s.execute(text("""
            INSERT INTO prompt_template (key, version, system_text, user_template, sha256, notes)
            VALUES (:k, :v, :s, :u, :h, 'seed') ON CONFLICT DO NOTHING"""),
            {"k": key, "v": p["version"], "s": p["system"], "u": p["user"],
             "h": hashlib.sha256((p["system"] + p["user"]).encode()).hexdigest()}).rowcount
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
